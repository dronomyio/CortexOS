"""
Agent Orchestrator — Opus 4.6 Powered
======================================

BEFORE (static pipeline):
  ingest → always: segment → transcribe → vision ALL → index ALL → enrich ALL
  synthesize → always: search → pull enrichments → generate

AFTER (dynamic, Opus-planned):
  ingest → segment → transcribe → Opus plans each segment →
    selective vision (skip filler) → adaptive analysis (find discrepancies) →
    selective enrichment (only verifiable claims) → index
  synthesize → search → Opus plans strategy →
    optional additional searches → optional fresh enrichment →
    strategy-aware generation (direct/comparative/investigative/timeline)

Every billable operation goes through x402 payment agent.
Planning itself is free — only triggered actions cost money.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentResult, AgentStatus
from agents.context_enrichment_agent import ContextEnrichmentAgent
from agents.observability import trace_ingest, trace_synthesis, init_opik, VidExEvaluator
from agents.opus_planner import OpusPlanner, IngestPlan, SynthesisPlan, AdaptiveAnalysis
from agents.synthesis_agent import SynthesisAgent
from agents.video_qa_agent import VideoQAAgent
from agents.x402_payment_agent import X402PaymentAgent
from config import AppConfig
from vision.engine import VisionEngine
from pipeline.video_processor import VideoProcessor
from search.weaviate_indexer import WeaviateIndexer
from models.text_generator import TextGenerator

logger = logging.getLogger(__name__)


# ─── Extended Synthesis Prompts (strategy-aware) ─────────────────────────

SYNTHESIS_STRATEGIES = {
    "direct": (
        "You are a precise research analyst. Answer the question directly using "
        "the video content and web evidence. Cite sources as [1], [2]. Be concise."
    ),
    "comparative": (
        "You are a fact-checking analyst. Compare what the video claims against "
        "what web evidence shows. Structure as: 'The video states X at [timestamp]. "
        "However, [evidence] shows Y [source].' Highlight every discrepancy."
    ),
    "investigative": (
        "You are an investigative analyst. The evidence contains conflicts or "
        "suspicious claims. Examine each piece carefully, note what's confirmed, "
        "what's contradicted, and what remains unverifiable. Rate confidence for "
        "each finding. Be thorough — this content may be misleading."
    ),
    "timeline": (
        "You are a chronological analyst. Build a timeline of events from the "
        "video content and web evidence. Format as sequential entries with "
        "timestamps and source citations. Note where the timeline has gaps."
    ),
}


class AgentOrchestrator:

    def __init__(self, config: AppConfig):
        self.config = config
        self.vision = VisionEngine(config.vision)
        self.payments = X402PaymentAgent(config.x402)
        self.processor = VideoProcessor(
            segment_duration=config.segment_duration_seconds,
            output_base="/tmp/videx",
        )
        self.indexer = WeaviateIndexer(
            host=config.weaviate.host,
            port=config.weaviate.port,
            grpc_port=config.weaviate.grpc_port,
        )

        self.enrichment_agent = ContextEnrichmentAgent(config.parallel, self.payments)
        self.qa_agent = VideoQAAgent(config.parallel, self.payments, weaviate_client=None)
        self.text_gen = TextGenerator(
            model_id=config.text_gen.model_id,
            local_path=config.text_gen.local_path,
            device=config.text_gen.device,
            torch_dtype=config.text_gen.torch_dtype,
            max_new_tokens=config.text_gen.max_new_tokens,
            paid_fallback=config.text_gen.paid_fallback,
            paid_model=config.text_gen.paid_model,
        )
        self.synthesis_agent = SynthesisAgent(
            text_generator=self.text_gen,
            payment_agent=self.payments,
        )

        # Opus 4.6 reasoning planner
        self.planner = OpusPlanner(self.text_gen)

        # Stored enrichments per video for synthesis
        self._enrichment_store: Dict[str, List[Dict]] = {}

        # Stored adaptive analyses per video
        self._adaptive_analyses: Dict[str, List[Dict]] = {}

        # Track source video paths for clip extraction
        self._video_paths: Dict[str, str] = {}

    async def startup(self):
        """Connect to Weaviate, load text generator, ensure schema exists."""
        await self.indexer.connect()
        await self.indexer.ensure_schema()
        if self.indexer.connected:
            self.qa_agent.weaviate = self.indexer._client
        try:
            await self.text_gen.load()
            logger.info(f"Text generator ready: {self.text_gen.backend_name}")
            logger.info("Opus planner active — dynamic pipeline decisions enabled")
        except Exception as e:
            logger.warning(f"Text generator not available: {e} — falling back to static pipeline")
        logger.info(
            "Orchestrator started — Weaviate connected"
            if self.indexer.connected
            else "Orchestrator started — Weaviate unavailable"
        )

    # ─── Full Pipeline: Upload → Plan → Selective Process → Index ────────

    async def ingest_video(self, video_path: str, video_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Opus-planned ingestion pipeline.

        Instead of processing every segment identically, Opus examines
        the content and creates a tailored processing plan:
        - Skip vision for filler segments (saves GPU time)
        - Focus enrichment on segments with verifiable claims
        - Flag discrepancies between speech and visuals
        - Identify clip-worthy moments

        Returns pipeline results with plan details and payment receipts.
        """
        start = time.perf_counter()

        # Step 1: FFmpeg segment + Whisper transcribe + keyframe extract
        logger.info(f"Ingesting video: {video_path}")
        pipeline_result = await self.processor.process(video_path, video_id)
        video_id = pipeline_result["video_id"]
        segments = pipeline_result["segments"]
        self._video_paths[video_id] = video_path

        # Step 2: Pay for video processing
        total_minutes = pipeline_result["total_duration"] / 60
        video_payment = await self.payments.pay_for_video_processing(video_id, total_minutes)

        # Step 3: OPUS PLANNING — examine segments, create processing plan
        logger.info(f"Opus planner analyzing {len(segments)} segments...")
        ingest_plans = await self.planner.plan_ingest(segments)

        plan_summary = {
            "vision_planned": sum(1 for p in ingest_plans if p.vision_analysis),
            "enrichment_planned": sum(1 for p in ingest_plans if p.enrichment),
            "index_planned": sum(1 for p in ingest_plans if p.index),
            "clip_worthy": sum(1 for p in ingest_plans if p.clip_worthy),
            "skipped": [p.to_dict() for p in ingest_plans if p.skip_reason],
        }
        logger.info(
            f"Plan: {plan_summary['vision_planned']}/{len(segments)} vision, "
            f"{plan_summary['enrichment_planned']}/{len(segments)} enrichment, "
            f"{plan_summary['clip_worthy']} clip-worthy"
        )

        # Step 4: Execute plan — selective vision analysis
        vision_analyses = []
        for i, (seg, plan) in enumerate(zip(segments, ingest_plans)):
            if plan.vision_analysis:
                video_seg_path = seg.get("video_path")
                if video_seg_path:
                    try:
                        analysis = await self.vision.analyze_segment(video_seg_path)
                        vision_analyses.append(analysis.to_dict())
                    except Exception as e:
                        logger.warning(f"Vision failed for segment {seg.get('index', i)}: {e}")
                        vision_analyses.append(self._empty_analysis(seg))
                else:
                    vision_analyses.append(self._empty_analysis(seg))
            else:
                logger.info(f"Skipping vision for segment {i}: {plan.skip_reason}")
                vision_analyses.append(self._empty_analysis(seg))

        # Step 5: Adaptive analysis — Opus examines vision results
        adaptive_analyses = []
        for i, (seg, plan, vision) in enumerate(zip(segments, ingest_plans, vision_analyses)):
            if plan.vision_analysis and vision.get("entities"):
                analysis = await self.planner.analyze_segment_deep(
                    transcript=seg.get("transcript", ""),
                    vision_output=vision,
                    video_id=video_id,
                    start_seconds=seg.get("start_seconds", 0),
                    end_seconds=seg.get("end_seconds", 0),
                )
                adaptive_analyses.append(analysis.to_dict())

                # Override enrichment focus with Opus findings
                if analysis.claims_to_verify:
                    plan.enrichment = True
                    plan.enrichment_focus = analysis.claims_to_verify
                if analysis.risk_score > 0.5:
                    plan.enrichment = True
                    logger.warning(
                        f"High-risk segment {i} (score={analysis.risk_score}), "
                        f"forcing enrichment"
                    )
            else:
                adaptive_analyses.append({})

        self._adaptive_analyses[video_id] = adaptive_analyses

        # Step 6: Index into Weaviate (selective based on plan)
        indexable_segments = [
            seg for seg, plan in zip(segments, ingest_plans) if plan.index
        ]
        indexable_analyses = [
            va for va, plan in zip(vision_analyses, ingest_plans) if plan.index
        ]
        index_stats = await self.indexer.index_video(
            video_id, indexable_segments, indexable_analyses,
        )

        # Step 7: Selective enrichment (only segments Opus marked)
        enrichment_results = await self._enrich_planned(
            video_id, segments, vision_analyses, ingest_plans,
        )
        self._enrichment_store[video_id] = [e for e in enrichment_results if e]

        duration = time.perf_counter() - start

        return {
            "video_id": video_id,
            "source_path": video_path,
            "total_duration_seconds": pipeline_result["total_duration"],
            "segments_count": len(segments),
            "opus_plan": plan_summary,
            "adaptive_analyses": len([a for a in adaptive_analyses if a]),
            "discrepancies_found": sum(
                len(a.get("discrepancies", [])) for a in adaptive_analyses
            ),
            "high_risk_segments": [
                i for i, a in enumerate(adaptive_analyses)
                if a.get("risk_score", 0) > 0.5
            ],
            "clip_worthy_segments": [
                i for i, p in enumerate(ingest_plans) if p.clip_worthy
            ],
            "indexing": index_stats,
            "enrichments": len([e for e in enrichment_results if e]),
            "pipeline_duration_seconds": round(duration, 2),
            "payment": video_payment.to_dict(),
            "daily_spend": self.payments.get_daily_spend(),
            "planner_stats": self.planner.get_stats(),
        }

    # ─── Opus-Planned Synthesis ──────────────────────────────────────────

    async def synthesize(
        self,
        question: str,
        video_id: Optional[str] = None,
        search_mode: str = "hybrid",
        enrich_on_demand: bool = True,
        limit: int = 10,
    ) -> AgentResult:
        """
        Opus-planned synthesis: search → plan → optional extra steps → generate.

        Instead of always running the same pipeline, Opus examines the
        search results and decides:
        - Are results sufficient or do we need more searches?
        - Do we need fresh web verification?
        - Are there conflicts requiring investigative analysis?
        - What synthesis strategy (direct/comparative/investigative/timeline)?
        """
        # Step 1: Initial search
        search_results = await self.indexer.search(question, video_id, search_mode, limit)

        # Step 2: Gather stored enrichments
        enrichments = []
        matched_video_ids = set()
        for r in search_results.get("merged", []):
            vid = r.get("video_id", "")
            if vid:
                matched_video_ids.add(vid)
        for vid in matched_video_ids:
            enrichments.extend(self._enrichment_store.get(vid, []))

        # Step 3: OPUS PLANNING — decide synthesis strategy
        logger.info(f"Opus planning synthesis for: {question[:80]}...")
        plan = await self.planner.plan_synthesis(
            question, search_results, enrichments, search_mode,
        )
        logger.info(
            f"Synthesis plan: strategy={plan.strategy}, "
            f"confidence={plan.confidence}, "
            f"additional_searches={len(plan.additional_searches)}, "
            f"fresh_enrichment={plan.needs_fresh_enrichment}"
        )

        # Step 4: Execute additional searches if Opus requested them
        if plan.additional_searches:
            for extra in plan.additional_searches[:3]:
                extra_query = extra.get("query", "")
                extra_mode = extra.get("mode", "hybrid")
                if extra_query:
                    logger.info(f"Running additional search: '{extra_query}' ({extra_mode})")
                    extra_results = await self.indexer.search(
                        extra_query, video_id, extra_mode, limit,
                    )
                    existing_merged = search_results.get("merged", [])
                    new_merged = extra_results.get("merged", [])
                    seen = {
                        (r.get("video_id"), r.get("start_seconds"))
                        for r in existing_merged
                    }
                    for r in new_merged:
                        key = (r.get("video_id"), r.get("start_seconds"))
                        if key not in seen:
                            existing_merged.append(r)
                            seen.add(key)
                    search_results["merged"] = existing_merged

        # Step 5: Override enrichment decision based on Opus plan
        effective_enrich = enrich_on_demand
        if plan.needs_fresh_enrichment:
            effective_enrich = True
        elif plan.confidence == "high" and not plan.conflict_detected:
            effective_enrich = False

        # Step 6: Select synthesis prompt based on Opus strategy
        strategy_prompt = SYNTHESIS_STRATEGIES.get(plan.strategy, SYNTHESIS_STRATEGIES["direct"])

        # Step 7: Run synthesis with strategy-aware prompting
        result = await self.synthesis_agent.run_with_timeout(
            {
                "question": question,
                "search_results": search_results,
                "enrichments": enrichments,
                "enrichment_on_demand": effective_enrich,
                "system_prompt_override": strategy_prompt,
            },
            timeout=self.config.agent_timeout_seconds,
        )

        # Step 8: Attach planning metadata to result
        if result.data and isinstance(result.data, dict):
            result.data["opus_plan"] = plan.to_dict()
            result.data["synthesis_strategy"] = plan.strategy

            for vid in matched_video_ids:
                analyses = self._adaptive_analyses.get(vid, [])
                discrepancies = []
                for a in analyses:
                    discrepancies.extend(a.get("discrepancies", []))
                if discrepancies:
                    result.data["discrepancies_found"] = discrepancies

        return result

    # ─── Planned Enrichment ──────────────────────────────────────────────

    async def _enrich_planned(
        self,
        video_id: str,
        segments: List[Dict],
        vision_analyses: List[Dict],
        plans: List[IngestPlan],
    ) -> List[Optional[Dict]]:
        """Selective enrichment — only segments the planner marked."""
        sem = asyncio.Semaphore(self.config.max_concurrent_agents)

        async def enrich_one(
            seg: Dict, analysis: Dict, plan: IngestPlan,
        ) -> Optional[Dict]:
            if not plan.enrichment:
                return None
            async with sem:
                try:
                    task = {
                        "video_id": video_id,
                        "segment_index": seg.get("index", 0),
                        "analysis": analysis,
                        "transcript": seg.get("transcript", ""),
                    }
                    if plan.enrichment_focus:
                        task["focus_claims"] = plan.enrichment_focus

                    result = await self.enrichment_agent.run_with_timeout(
                        task, timeout=self.config.agent_timeout_seconds,
                    )
                    return result.data if result.status == AgentStatus.COMPLETED else None
                except Exception:
                    return None

        tasks = [
            enrich_one(seg, vision_analyses[i] if i < len(vision_analyses) else {}, plan)
            for i, (seg, plan) in enumerate(zip(segments, plans))
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r if not isinstance(r, Exception) else None for r in results]

    # ─── Unchanged methods ───────────────────────────────────────────────

    async def process_video(
        self, video_id: str, segments: List[Dict],
    ) -> Dict[str, Any]:
        """Process pre-segmented video data."""
        start = time.perf_counter()
        total_minutes = sum(
            (s.get("end_seconds", 0) - s.get("start_seconds", 0)) / 60 for s in segments
        )
        video_payment = await self.payments.pay_for_video_processing(video_id, total_minutes)
        plans = await self.planner.plan_ingest(segments)

        sem = asyncio.Semaphore(self.config.max_concurrent_agents)

        async def process_one(segment: Dict, plan: IngestPlan) -> Dict:
            async with sem:
                seg_idx = segment.get("index", 0)
                vision_result = None
                if plan.vision_analysis:
                    video_path = segment.get("video_path")
                    if video_path:
                        try:
                            vision_result = await self.vision.analyze_segment(video_path)
                        except Exception as e:
                            logger.warning(f"Vision failed for segment {seg_idx}: {e}")

                analysis_dict = vision_result.to_dict() if vision_result else self._empty_analysis(segment)

                enrichment = None
                if plan.enrichment:
                    enrichment_result = await self.enrichment_agent.run_with_timeout(
                        {"video_id": video_id, "segment_index": seg_idx,
                         "analysis": analysis_dict, "transcript": segment.get("transcript", "")},
                        timeout=self.config.agent_timeout_seconds,
                    )
                    enrichment = enrichment_result.data if enrichment_result.status == AgentStatus.COMPLETED else None

                return {
                    "segment_index": seg_idx,
                    "vision_analysis": analysis_dict,
                    "enrichment": enrichment,
                    "plan": plan.to_dict(),
                }

        segment_results = await asyncio.gather(
            *[process_one(s, plans[i] if i < len(plans) else IngestPlan(i, {}))
              for i, s in enumerate(segments)],
            return_exceptions=True,
        )
        successful = [r for r in segment_results if not isinstance(r, Exception)]
        failed = [str(r) for r in segment_results if isinstance(r, Exception)]

        return {
            "video_id": video_id,
            "segments_processed": len(successful),
            "segments_failed": len(failed),
            "results": successful,
            "errors": failed,
            "duration_seconds": round(time.perf_counter() - start, 2),
            "video_payment": video_payment.to_dict(),
            "daily_spend": self.payments.get_daily_spend(),
            "planner_stats": self.planner.get_stats(),
        }

    async def search(
        self, query: str, video_id: Optional[str] = None, mode: str = "hybrid", limit: int = 10,
    ) -> Dict[str, Any]:
        return await self.indexer.search(query, video_id, mode, limit)

    async def extract_clip(
        self, video_id: str, start_seconds: float, end_seconds: float,
        source_path: Optional[str] = None,
    ) -> Optional[str]:
        if source_path is None:
            source_path = self._video_paths.get(video_id)
        if source_path is None:
            return None
        return await self.processor.extract_clip(source_path, start_seconds, end_seconds)

    async def extract_clip_for_result(
        self, result: Dict, source_path: Optional[str] = None,
    ) -> Optional[str]:
        video_id = result.get("video_id", "")
        if source_path is None:
            source_path = self._video_paths.get(video_id)
        if source_path is None:
            return None
        return await self.indexer.extract_clip_for_result(result, source_path)

    async def ask(
        self, question: str, video_id: Optional[str] = None, verify: bool = True,
    ) -> AgentResult:
        return await self.qa_agent.run_with_timeout(
            {"question": question, "video_id": video_id, "verify_with_web": verify},
            timeout=self.config.agent_timeout_seconds,
        )

    @staticmethod
    def _empty_analysis(segment: Dict) -> Dict:
        return {
            "description": segment.get("transcript", "")[:500],
            "entities": [], "topics": [], "actions": [], "claims": [],
            "temporal_summary": "",
        }

    def get_status(self) -> Dict:
        return {
            "agents": {
                "context_enrichment": self.enrichment_agent.status.value,
                "video_qa": self.qa_agent.status.value,
                "synthesis": self.synthesis_agent.status.value,
                "opus_planner": "active" if self.text_gen._loaded else "inactive",
            },
            "vision_backend": self.config.vision.backend.value,
            "text_gen_backend": self.text_gen.backend_name,
            "paid_fallback": self.config.vision.paid_fallback_enabled,
            "weaviate_connected": self.indexer.connected,
            "indexed_videos": list(self._video_paths.keys()),
            "stored_enrichments": {vid: len(e) for vid, e in self._enrichment_store.items()},
            "adaptive_analyses": {vid: len(a) for vid, a in self._adaptive_analyses.items()},
            "x402": self.payments.get_daily_spend(),
            "payment_ledger_size": len(self.payments._ledger),
            "planner_stats": self.planner.get_stats(),
        }

    def get_payment_ledger(self, limit: int = 50) -> List[Dict]:
        return self.payments.get_ledger(limit)

    async def shutdown(self):
        await self.enrichment_agent.cleanup()
        await self.qa_agent.cleanup()
        await self.payments.close()
        self.indexer.close()
