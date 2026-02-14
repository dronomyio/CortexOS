"""
Opus Reasoning Planner
======================

The brain of VidEx. Replaces hardcoded pipeline steps with dynamic
Opus 4.6 reasoning that decides WHAT to do based on content analysis.

Old orchestrator (static):
  ingest → always: segment → transcribe → vision → index → enrich ALL
  synthesize → always: search → pull enrichments → generate

Opus planner (dynamic):
  ingest → Opus examines first segment →
    "This has charts, run deep vision analysis"
    "This segment is intro filler, skip enrichment"
    "Speaker made 3 verifiable claims, prioritize those for Parallel.ai"
    "This is a duplicate of segment 2, skip indexing"

  synthesize → Opus examines query + search results →
    "User asked about price data, need fresh web verification"
    "Search found visual chart match, weight that over transcript"
    "Claims in results conflict, need additional search"
    "Simple factual answer found in transcript, skip enrichment"

This is what makes Opus 4.6 irreplaceable — not just text generation,
but reasoning about what pipeline steps to run and why.

Billed via x402: planning itself is free, but each action it triggers
is billed at standard rates.
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from models.text_generator import TextGenerator

logger = logging.getLogger(__name__)


# ─── Planning Prompts ────────────────────────────────────────────────────

INGEST_PLANNER_SYSTEM = """You are an autonomous video analysis planner. You examine video segments and decide what processing each one needs.

You receive a batch of segments with their transcripts and basic metadata. For EACH segment, output a JSON processing plan.

Available actions per segment:
- "vision_analysis": true/false — Run Qwen2.5-VL deep video understanding. Use for segments with charts, diagrams, screen recordings, demonstrations, or complex visual content. Skip for talking heads with no visual aids.
- "vision_priority": "high"|"normal"|"low" — If vision_analysis=true, how carefully to analyze. "high" = max frames, "normal" = standard, "low" = quick scan.
- "enrichment": true/false — Run Parallel.ai web verification. Use when transcript contains verifiable claims, statistics, names, dates, prices, or technical assertions. Skip for opinions, greetings, filler, music.
- "enrichment_focus": list of specific claims/entities to verify. Empty if enrichment=false.
- "index": true/false — Index in Weaviate. Almost always true. Skip only for content-free segments (silence, music-only, identical repeats).
- "clip_worthy": true/false — This segment contains a notable moment worth extracting as a standalone clip.
- "skip_reason": string or null — If skipping any action, explain why in <10 words.

Think about cost efficiency: each vision analysis costs GPU time, each enrichment costs API calls.
Prioritize segments where analysis will add the most value.

Respond with ONLY a JSON array, one object per segment. No markdown, no explanation."""

INGEST_PLANNER_USER = """Analyze these {count} video segments and create a processing plan for each:

{segments_json}

For each segment, decide: vision_analysis, enrichment, index, clip_worthy.
Focus enrichment on segments with verifiable claims or factual assertions.
Focus vision on segments with visual content beyond a talking head.

Return a JSON array with one plan object per segment."""


SYNTHESIS_PLANNER_SYSTEM = """You are an autonomous research planner. You examine a user's question and search results, then decide the optimal strategy to produce a high-quality cited answer.

Available actions:
- "search_sufficient": true/false — Do the current search results contain enough information to answer? If false, suggest additional searches.
- "additional_searches": list of {query, mode} — Extra searches to run. mode: "transcript"|"visual"|"hybrid"
- "needs_fresh_enrichment": true/false — Should we call Parallel.ai live for this specific question? Use when: claims need verification against current data, question asks about current state (prices, status), existing enrichments are stale or don't cover the question.
- "enrichment_queries": list of strings — Specific things to verify with Parallel.ai.
- "conflict_detected": true/false — Do search results contain contradictory information?
- "confidence_assessment": "high"|"medium"|"low" — How confident are you that available data can answer the question?
- "synthesis_strategy": "direct"|"comparative"|"investigative"|"timeline" — How to structure the answer.
  - "direct": Simple factual answer from clear evidence
  - "comparative": Compare what video says vs what evidence shows
  - "investigative": Evidence conflicts or claims are suspicious, need careful analysis
  - "timeline": Question asks about sequence of events, build chronological narrative
- "focus_segments": list of segment indices to prioritize in synthesis
- "reasoning": 1-2 sentences explaining your plan

Respond with ONLY a JSON object. No markdown, no explanation."""

SYNTHESIS_PLANNER_USER = """Question: {question}

Search results ({result_count} matches, mode: {search_mode}):
{results_summary}

Stored enrichments available: {enrichment_count}
Enrichment summary: {enrichment_summary}

Plan the optimal synthesis strategy."""


ADAPTIVE_INGEST_SYSTEM = """You are analyzing a video segment in detail to decide what additional processing it needs.

You've already seen the transcript. Now examine the vision analysis output and decide:

1. "claims_to_verify": List of specific factual claims that should be checked against web sources. Extract exact quotes or paraphrases.
2. "entities_to_enrich": List of named entities (people, companies, products, protocols) that need background context.
3. "discrepancies": Any mismatch between what the transcript says and what the vision model saw (e.g., speaker says "$142" but chart shows "$128").
4. "follow_up_questions": Questions a viewer might ask about this segment that we should pre-compute answers for.
5. "risk_score": 0.0-1.0 — How likely this segment contains misleading or inaccurate information. 0=factual, 1=highly suspicious.

Respond with ONLY a JSON object. No markdown."""

ADAPTIVE_INGEST_USER = """Transcript: {transcript}

Vision analysis output:
{vision_output}

Segment: {start_seconds}s - {end_seconds}s of video "{video_id}"

Extract claims, entities, discrepancies, and assess risk."""


class IngestPlan:
    """Processing plan for a single segment."""

    def __init__(self, segment_index: int, plan_data: Dict):
        self.segment_index = segment_index
        self.vision_analysis = plan_data.get("vision_analysis", True)
        self.vision_priority = plan_data.get("vision_priority", "normal")
        self.enrichment = plan_data.get("enrichment", True)
        self.enrichment_focus = plan_data.get("enrichment_focus", [])
        self.index = plan_data.get("index", True)
        self.clip_worthy = plan_data.get("clip_worthy", False)
        self.skip_reason = plan_data.get("skip_reason")

    def to_dict(self) -> Dict:
        return {
            "segment_index": self.segment_index,
            "vision_analysis": self.vision_analysis,
            "vision_priority": self.vision_priority,
            "enrichment": self.enrichment,
            "enrichment_focus": self.enrichment_focus,
            "index": self.index,
            "clip_worthy": self.clip_worthy,
            "skip_reason": self.skip_reason,
        }


class SynthesisPlan:
    """Strategy plan for answering a question."""

    def __init__(self, plan_data: Dict):
        self.search_sufficient = plan_data.get("search_sufficient", True)
        self.additional_searches = plan_data.get("additional_searches", [])
        self.needs_fresh_enrichment = plan_data.get("needs_fresh_enrichment", False)
        self.enrichment_queries = plan_data.get("enrichment_queries", [])
        self.conflict_detected = plan_data.get("conflict_detected", False)
        self.confidence = plan_data.get("confidence_assessment", "medium")
        self.strategy = plan_data.get("synthesis_strategy", "direct")
        self.focus_segments = plan_data.get("focus_segments", [])
        self.reasoning = plan_data.get("reasoning", "")

    def to_dict(self) -> Dict:
        return {
            "search_sufficient": self.search_sufficient,
            "additional_searches": self.additional_searches,
            "needs_fresh_enrichment": self.needs_fresh_enrichment,
            "enrichment_queries": self.enrichment_queries,
            "conflict_detected": self.conflict_detected,
            "confidence_assessment": self.confidence,
            "synthesis_strategy": self.strategy,
            "focus_segments": self.focus_segments,
            "reasoning": self.reasoning,
        }


class AdaptiveAnalysis:
    """Deep analysis of a segment after vision processing."""

    def __init__(self, data: Dict):
        self.claims_to_verify = data.get("claims_to_verify", [])
        self.entities_to_enrich = data.get("entities_to_enrich", [])
        self.discrepancies = data.get("discrepancies", [])
        self.follow_up_questions = data.get("follow_up_questions", [])
        self.risk_score = data.get("risk_score", 0.0)

    def to_dict(self) -> Dict:
        return {
            "claims_to_verify": self.claims_to_verify,
            "entities_to_enrich": self.entities_to_enrich,
            "discrepancies": self.discrepancies,
            "follow_up_questions": self.follow_up_questions,
            "risk_score": self.risk_score,
        }


class OpusPlanner:
    """
    Opus 4.6 reasoning planner for dynamic pipeline decisions.

    Instead of running every step on every segment, Opus examines
    the content first and creates an optimal processing plan.

    This is the key differentiator: the model reasons about
    WHAT to do, not just executes hardcoded steps.
    """

    def __init__(self, text_generator: TextGenerator):
        self.text_gen = text_generator
        self._planning_calls = 0
        self._tokens_used = 0

    # ─── Ingest Planning ─────────────────────────────────────────────────

    async def plan_ingest(self, segments: List[Dict]) -> List[IngestPlan]:
        """
        Examine all segments and create a processing plan.

        Instead of blindly running vision + enrichment on every segment,
        Opus decides which segments need what based on content analysis.

        Args:
            segments: List of {"index", "transcript", "start_seconds",
                               "end_seconds", "keyframe_count", ...}

        Returns:
            List of IngestPlan, one per segment.
        """
        # Build compact segment summaries for the planner
        segment_summaries = []
        for seg in segments:
            summary = {
                "index": seg.get("index", 0),
                "start_s": seg.get("start_seconds", 0),
                "end_s": seg.get("end_seconds", 0),
                "transcript_preview": (seg.get("transcript", "") or "")[:300],
                "transcript_length": len(seg.get("transcript", "") or ""),
                "keyframes": seg.get("keyframe_count", 0),
                "has_audio": seg.get("has_audio", True),
            }
            segment_summaries.append(summary)

        # If only 1-2 segments, skip planning overhead — process everything
        if len(segments) <= 2:
            return [
                IngestPlan(seg.get("index", i), {
                    "vision_analysis": True,
                    "vision_priority": "high",
                    "enrichment": bool((seg.get("transcript", "") or "").strip()),
                    "enrichment_focus": [],
                    "index": True,
                    "clip_worthy": False,
                })
                for i, seg in enumerate(segments)
            ]

        prompt = INGEST_PLANNER_USER.format(
            count=len(segments),
            segments_json=json.dumps(segment_summaries, indent=2),
        )

        try:
            response = await self.text_gen.generate(
                system=INGEST_PLANNER_SYSTEM,
                user=prompt,
                max_tokens=2048,
                temperature=0.1,
            )
            self._planning_calls += 1

            plans_data = self._parse_json_response(response, expect_list=True)
            if not plans_data:
                logger.warning("Planner returned unparseable response, using defaults")
                return self._default_ingest_plans(segments)

            plans = []
            for i, seg in enumerate(segments):
                plan_data = plans_data[i] if i < len(plans_data) else {}
                plans.append(IngestPlan(seg.get("index", i), plan_data))

            logger.info(
                f"Ingest plan: {sum(1 for p in plans if p.vision_analysis)}/{len(plans)} vision, "
                f"{sum(1 for p in plans if p.enrichment)}/{len(plans)} enrichment, "
                f"{sum(1 for p in plans if p.clip_worthy)} clip-worthy"
            )
            return plans

        except Exception as e:
            logger.warning(f"Planning failed ({e}), using default plans")
            return self._default_ingest_plans(segments)

    # ─── Synthesis Planning ──────────────────────────────────────────────

    async def plan_synthesis(
        self,
        question: str,
        search_results: Dict,
        enrichments: List[Dict],
        search_mode: str = "hybrid",
    ) -> SynthesisPlan:
        """
        Examine question + search results and decide synthesis strategy.

        Instead of always running the same search → enrich → generate,
        Opus decides:
        - Are search results sufficient or do we need more searches?
        - Do we need fresh web verification for this specific question?
        - Do the results conflict? Need investigative vs direct approach?
        - Which segments are most relevant to focus on?

        Args:
            question: User's question
            search_results: From WeaviateIndexer.search()
            enrichments: Stored enrichment data
            search_mode: What search mode was used

        Returns:
            SynthesisPlan with strategy decisions
        """
        # Build compact results summary
        merged = search_results.get("merged", [])
        results_summary = []
        for i, r in enumerate(merged[:8]):
            results_summary.append({
                "idx": i,
                "type": r.get("match_source", r.get("type", "?")),
                "score": round(r.get("score", 0), 3),
                "time": f"{r.get('start_seconds', 0):.0f}s",
                "preview": (r.get("text", r.get("description", "")) or "")[:150],
                "entities": r.get("entities", [])[:3],
                "claims": r.get("claims", [])[:2],
            })

        # Build enrichment summary
        enrichment_summary = []
        for e in enrichments[:5]:
            if not e or not isinstance(e, dict):
                continue
            claims = list(e.get("claims_verified", {}).keys())[:3]
            entities = list(e.get("entities_enriched", {}).keys())[:3]
            if claims or entities:
                enrichment_summary.append({
                    "claims_verified": claims,
                    "entities": entities,
                })

        prompt = SYNTHESIS_PLANNER_USER.format(
            question=question,
            result_count=len(merged),
            search_mode=search_mode,
            results_summary=json.dumps(results_summary, indent=2),
            enrichment_count=len(enrichments),
            enrichment_summary=json.dumps(enrichment_summary, indent=2) if enrichment_summary else "None stored",
        )

        try:
            response = await self.text_gen.generate(
                system=SYNTHESIS_PLANNER_SYSTEM,
                user=prompt,
                max_tokens=512,
                temperature=0.1,
            )
            self._planning_calls += 1

            plan_data = self._parse_json_response(response, expect_list=False)
            if not plan_data:
                logger.warning("Synthesis planner returned unparseable response, using defaults")
                return SynthesisPlan({
                    "search_sufficient": len(merged) > 0,
                    "needs_fresh_enrichment": True,
                    "synthesis_strategy": "direct",
                    "confidence_assessment": "medium" if merged else "low",
                })

            plan = SynthesisPlan(plan_data)
            logger.info(
                f"Synthesis plan: strategy={plan.strategy}, "
                f"confidence={plan.confidence}, "
                f"fresh_enrichment={plan.needs_fresh_enrichment}, "
                f"additional_searches={len(plan.additional_searches)}, "
                f"reasoning={plan.reasoning}"
            )
            return plan

        except Exception as e:
            logger.warning(f"Synthesis planning failed ({e}), using defaults")
            return SynthesisPlan({
                "search_sufficient": len(merged) > 0,
                "needs_fresh_enrichment": True,
                "synthesis_strategy": "direct",
            })

    # ─── Adaptive Analysis (post-vision) ─────────────────────────────────

    async def analyze_segment_deep(
        self,
        transcript: str,
        vision_output: Dict,
        video_id: str,
        start_seconds: float,
        end_seconds: float,
    ) -> AdaptiveAnalysis:
        """
        After vision analysis, Opus examines the results and identifies
        what needs verification, what discrepancies exist, and what
        questions viewers might ask.

        This is the second planning pass — ingest planning decides
        WHETHER to run vision; this decides what to DO with the results.
        """
        vision_str = json.dumps(vision_output, indent=2)[:2000]
        transcript_preview = (transcript or "")[:800]

        prompt = ADAPTIVE_INGEST_USER.format(
            transcript=transcript_preview,
            vision_output=vision_str,
            video_id=video_id,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )

        try:
            response = await self.text_gen.generate(
                system=ADAPTIVE_INGEST_SYSTEM,
                user=prompt,
                max_tokens=512,
                temperature=0.1,
            )
            self._planning_calls += 1

            data = self._parse_json_response(response, expect_list=False)
            if not data:
                return AdaptiveAnalysis({})

            analysis = AdaptiveAnalysis(data)

            if analysis.discrepancies:
                logger.warning(
                    f"Discrepancies found in {video_id} @ {start_seconds}s: "
                    f"{analysis.discrepancies}"
                )
            if analysis.risk_score > 0.7:
                logger.warning(
                    f"High risk segment in {video_id} @ {start_seconds}s: "
                    f"score={analysis.risk_score}"
                )

            return analysis

        except Exception as e:
            logger.warning(f"Adaptive analysis failed ({e})")
            return AdaptiveAnalysis({})

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _parse_json_response(self, response: str, expect_list: bool = False) -> Any:
        """Parse JSON from LLM response, handling markdown fences."""
        text = response.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            parsed = json.loads(text)
            if expect_list and not isinstance(parsed, list):
                return None
            if not expect_list and not isinstance(parsed, dict):
                return None
            return parsed
        except json.JSONDecodeError:
            # Try to find JSON in the response
            import re
            if expect_list:
                match = re.search(r'\[.*\]', text, re.DOTALL)
            else:
                match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    return None
            return None

    def _default_ingest_plans(self, segments: List[Dict]) -> List[IngestPlan]:
        """Fallback: process everything (same as old hardcoded pipeline)."""
        return [
            IngestPlan(seg.get("index", i), {
                "vision_analysis": True,
                "enrichment": True,
                "index": True,
                "clip_worthy": False,
            })
            for i, seg in enumerate(segments)
        ]

    def get_stats(self) -> Dict:
        return {
            "planning_calls": self._planning_calls,
            "planner_backend": self.text_gen.backend_name,
        }


# ── Auto-Discovery ───────────────────────────────────────────────────────
def register_routes(app):
    """Auto-discovered by main.py — registers Opus planner endpoints."""

    @app.get("/api/v1/planner/stats", tags=["opus-planner"])
    async def planner_stats():
        """Opus 4.6 planner statistics — plans generated, strategies chosen."""
        try:
            import os
            from models.text_generator import TextGenerator
            text_gen = TextGenerator(
                paid_model=os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
                use_paid_fallback=os.getenv("TEXT_PAID_FALLBACK", "false").lower() == "true",
            )
            planner = OpusPlanner(text_gen)
            return {
                "agent": "opus-planner",
                "status": "active",
                "model": os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
                **planner.get_stats(),
            }
        except Exception as e:
            return {"agent": "opus-planner", "status": "error", "error": str(e)}

    @app.get("/api/v1/planner/strategies", tags=["opus-planner"])
    async def planner_strategies():
        """List available synthesis strategies."""
        try:
            from agents.orchestrator import SYNTHESIS_STRATEGIES
            return {"strategies": list(SYNTHESIS_STRATEGIES.keys()), "count": len(SYNTHESIS_STRATEGIES)}
        except ImportError:
            return {"strategies": ["direct", "comparative", "investigative", "timeline"], "count": 4}

    print("[OpusPlanner] Registered routes: /api/v1/planner/stats, /planner/strategies")


# ── Auto-Discovery ───────────────────────────────────────────────────────
def register_routes(app):
    """Auto-discovered by main.py — registers Opus planner endpoints."""

    @app.get("/api/v1/planner/stats", tags=["opus-planner"])
    async def planner_stats():
        """Opus 4.6 planner statistics — plans generated, strategies chosen."""
        try:
            import os
            from models.text_generator import TextGenerator
            text_gen = TextGenerator(
                paid_model=os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
                use_paid_fallback=os.getenv("TEXT_PAID_FALLBACK", "false").lower() == "true",
            )
            planner = OpusPlanner(text_gen)
            return {
                "agent": "opus-planner",
                "status": "active",
                "model": os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
                **planner.get_stats(),
            }
        except Exception as e:
            return {"agent": "opus-planner", "status": "error", "error": str(e)}

    @app.get("/api/v1/planner/strategies", tags=["opus-planner"])
    async def planner_strategies():
        """List available synthesis strategies."""
        try:
            from agents.orchestrator import SYNTHESIS_STRATEGIES
            return {"strategies": list(SYNTHESIS_STRATEGIES.keys()), "count": len(SYNTHESIS_STRATEGIES)}
        except ImportError:
            return {"strategies": ["direct", "comparative", "investigative", "timeline"], "count": 4}

    print("[OpusPlanner] Registered routes: /api/v1/planner/stats, /planner/strategies")
