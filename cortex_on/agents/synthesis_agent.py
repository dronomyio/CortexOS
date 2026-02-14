"""
Synthesis Agent
===============

The missing piece: takes raw search results + stored enrichments and
produces a coherent, cited narrative answer.

This is what the user actually sees. Without this, they get JSON blobs.
With this, they get:

  "At 0:45, the professor claims CRISPR has 99% precision. However,
   a 2024 Nature meta-analysis of 127 studies shows median on-target
   efficiency of 68%, varying 30-99% by locus [1]. The FDA did approve
   Casgevy (the first CRISPR therapy) in December 2023 [2].

   Sources:
   [1] nature.com/articles/s41586-024-...
   [2] fda.gov/news-events/press-announcements/..."

Pipeline:
  Search results (Weaviate) + Enrichments (Parallel.ai)
    → Assemble context window
    → Local Qwen2.5-7B generates synthesis (or paid fallback)
    → Parse citations
    → Return structured + readable answer

Billed via x402 per synthesis.
"""
import logging
from typing import Any, Dict, List, Optional

from .base_agent import AgentResult, AgentStatus, BaseAgent
from .x402_payment_agent import X402PaymentAgent
from models.text_generator import TextGenerator

logger = logging.getLogger(__name__)


# ─── Synthesis Prompts ───────────────────────────────────────────────────

SYNTHESIS_SYSTEM = """You are a precise research analyst. Your job is to synthesize video content with web-verified evidence into a clear, cited answer.

Rules:
1. State what the video shows/says first, with timestamps
2. Then verify or correct claims using the web evidence provided
3. Cite sources as [1], [2], etc. — list full URLs at the end
4. If evidence contradicts the video, say so clearly
5. If evidence is insufficient, say what's confirmed and what isn't
6. Be concise. No filler. Every sentence should add information.
7. Use plain language. No jargon unless the user's query uses it."""

SYNTHESIS_USER_TEMPLATE = """## User Question
{question}

## Video Content Found
{video_context}

## Web Evidence (from Parallel.ai)
{web_evidence}

## Instructions
Synthesize a clear answer that:
- References specific timestamps from the video
- Verifies or corrects claims using the web evidence
- Cites sources as [1], [2], etc.
- Lists all source URLs at the end

If the video content and web evidence conflict, explain the discrepancy.
If no relevant video content was found, say so and answer from web evidence alone."""


class SynthesisAgent(BaseAgent):
    """
    Synthesizes search results + enrichments into cited answers.

    Usage:
        agent = SynthesisAgent(text_gen, payment_agent)
        result = await agent.run_with_timeout({
            "question": "Is CRISPR really 99% precise?",
            "search_results": {...},          # From WeaviateIndexer.search()
            "enrichments": {...},             # Stored from ingestion
            "enrichment_on_demand": True,     # Also call Parallel.ai fresh?
        })
    """

    def __init__(
        self,
        text_generator: TextGenerator,
        payment_agent: X402PaymentAgent,
        parallel_client=None,
    ):
        super().__init__(name="synthesis")
        self.text_gen = text_generator
        self.payments = payment_agent
        self.parallel = parallel_client

    async def execute(self, task: Dict[str, Any]) -> AgentResult:
        """
        Synthesize search results into a cited answer.

        Task:
        {
            "question": "...",
            "search_results": {                 # From indexer.search()
                "merged": [...],
                "transcript_results": [...],
                "visual_results": [...]
            },
            "enrichments": [...],               # Stored enrichment data
            "enrichment_on_demand": False,       # Fresh Parallel.ai call?
        }
        """
        question = task.get("question", "")
        search_results = task.get("search_results", {})
        enrichments = task.get("enrichments", [])

        if not question:
            return AgentResult(agent_name=self.name, status=AgentStatus.FAILED, error="No question")

        # Pay for synthesis
        payment = await self.payments.pay_for_synthesis()

        # Step 1: Build video context from search results
        video_context = self._build_video_context(search_results)

        # Step 2: Build web evidence from enrichments
        web_evidence = self._build_web_evidence(enrichments)

        # Step 3: Optional on-demand enrichment via Parallel.ai
        if task.get("enrichment_on_demand") and self.parallel:
            fresh_evidence = await self._enrich_on_demand(question, search_results)
            if fresh_evidence:
                web_evidence += "\n\n### Fresh Web Verification\n" + fresh_evidence

        # Step 4: Generate synthesis with local model (or paid fallback)
        prompt = SYNTHESIS_USER_TEMPLATE.format(
            question=question,
            video_context=video_context or "No matching video content found.",
            web_evidence=web_evidence or "No web evidence available.",
        )

        # Use strategy-aware system prompt if Opus planner provided one
        system_prompt = task.get("system_prompt_override", SYNTHESIS_SYSTEM)

        try:
            synthesis_text = await self.text_gen.generate(
                system=system_prompt,
                user=prompt,
                max_tokens=1024,
                temperature=0.2,  # Low temperature for factual synthesis
            )
        except Exception as e:
            logger.error(f"Synthesis generation failed: {e}")
            # Fallback: return structured but unsynthesized data
            synthesis_text = self._fallback_synthesis(question, search_results, enrichments)

        # Step 5: Extract citations
        sources = self._extract_sources(synthesis_text, search_results, enrichments)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            data={
                "question": question,
                "synthesis": synthesis_text,
                "sources": sources,
                "video_matches": len(search_results.get("merged", [])),
                "web_sources": len(sources),
                "generator_backend": self.text_gen.backend_name,
                "payment": payment.to_dict(),
            },
            metadata={
                "search_mode": search_results.get("mode", "unknown"),
                "total_results": search_results.get("total_results", 0),
            },
        )

    # ─── Context Assembly ────────────────────────────────────────────────

    def _build_video_context(self, search_results: Dict) -> str:
        """Assemble video content from search results into readable context."""
        parts = []

        for i, result in enumerate(search_results.get("merged", [])[:8]):
            match_type = result.get("match_source", result.get("type", "unknown"))
            start = result.get("start_seconds", 0)
            end = result.get("end_seconds", 0)
            score = result.get("score", 0)

            if match_type == "transcript":
                text = result.get("text", "")[:500]
                entities = result.get("entities", [])
                topics = result.get("topics", [])
                parts.append(
                    f"### Match {i+1} [Transcript] ({start:.0f}s - {end:.0f}s) score={score:.2f}\n"
                    f"Text: {text}\n"
                    f"Entities: {', '.join(entities[:5]) if entities else 'none'}\n"
                    f"Topics: {', '.join(topics[:5]) if topics else 'none'}"
                )
            elif match_type == "visual":
                desc = result.get("description", "")[:200]
                time_s = result.get("absolute_time_s", start)
                parts.append(
                    f"### Match {i+1} [Visual] ({time_s:.0f}s) score={score:.2f}\n"
                    f"Frame description: {desc}"
                )

        return "\n\n".join(parts)

    def _build_web_evidence(self, enrichments: List) -> str:
        """Assemble web evidence from stored enrichments."""
        parts = []

        for enrichment in enrichments:
            if not enrichment or not isinstance(enrichment, dict):
                continue

            # Claims verified
            claims = enrichment.get("claims_verified", {})
            if claims:
                for claim, verification in claims.items():
                    if isinstance(verification, dict):
                        verdict = verification.get("verdict", "")
                        source = verification.get("source", "")
                        parts.append(f"- Claim: \"{claim}\" → {verdict} (source: {source})")
                    else:
                        parts.append(f"- Claim: \"{claim}\" → {verification}")

            # Entity enrichments
            entities = enrichment.get("entities_enriched", {})
            for name, info in entities.items():
                if isinstance(info, dict):
                    defn = info.get("definition", info.get("current_status", ""))
                    src = info.get("source", "")
                    parts.append(f"- {name}: {defn} (source: {src})")

            # Additional context / citations
            citations = enrichment.get("additional_context", enrichment.get("citations", []))
            if isinstance(citations, list):
                for c in citations[:5]:
                    if isinstance(c, dict):
                        title = c.get("title", "")
                        url = c.get("url", "")
                        excerpt = c.get("excerpt", "")[:200]
                        parts.append(f"- [{title}]({url}): {excerpt}")

        return "\n".join(parts) if parts else ""

    # ─── On-Demand Enrichment ────────────────────────────────────────────

    async def _enrich_on_demand(self, question: str, search_results: Dict) -> str:
        """Call Parallel.ai fresh for the specific question + found context."""
        if not self.parallel:
            return ""

        # Build context from top search results
        context_snippets = []
        for r in search_results.get("merged", [])[:3]:
            text = r.get("text", r.get("description", ""))[:200]
            if text:
                context_snippets.append(text)

        context = "; ".join(context_snippets)[:500]
        objective = (
            f"Verify and find current information to answer: {question}. "
            f"Context from video: {context}"
        )

        try:
            result = await self.parallel.search(
                objective=objective,
                search_queries=[question],
                max_results=5,
                max_chars_per_result=3000,
            )

            if "error" in result:
                return ""

            parts = []
            for r in result.get("results", []):
                title = r.get("title", "")
                url = r.get("url", "")
                excerpt = r.get("excerpt", "")[:200]
                parts.append(f"- [{title}]({url}): {excerpt}")

            return "\n".join(parts)

        except Exception as e:
            logger.warning(f"On-demand enrichment failed: {e}")
            return ""

    # ─── Citation Extraction ─────────────────────────────────────────────

    def _extract_sources(
        self, synthesis: str, search_results: Dict, enrichments: List,
    ) -> List[Dict]:
        """Extract cited sources from the synthesis text and match to URLs."""
        sources = []
        seen_urls = set()

        # Collect all URLs from enrichments
        all_urls = {}
        for enrichment in enrichments:
            if not enrichment or not isinstance(enrichment, dict):
                continue
            for c in enrichment.get("citations", enrichment.get("additional_context", [])):
                if isinstance(c, dict) and c.get("url"):
                    all_urls[c["url"]] = c.get("title", "")

            for claim_info in enrichment.get("claims_verified", {}).values():
                if isinstance(claim_info, dict) and claim_info.get("source"):
                    src = claim_info["source"]
                    if src.startswith("http"):
                        all_urls[src] = ""

            for entity_info in enrichment.get("entities_enriched", {}).values():
                if isinstance(entity_info, dict) and entity_info.get("source"):
                    src = entity_info["source"]
                    if src.startswith("http"):
                        all_urls[src] = ""

        # Build source list
        for url, title in all_urls.items():
            if url not in seen_urls:
                sources.append({"url": url, "title": title})
                seen_urls.add(url)

        return sources

    # ─── Fallback (No LLM Available) ────────────────────────────────────

    def _fallback_synthesis(
        self, question: str, search_results: Dict, enrichments: List,
    ) -> str:
        """
        Structured but unsynthesized answer when the LLM is unavailable.
        Better than returning raw JSON.
        """
        parts = [f"Question: {question}\n"]

        merged = search_results.get("merged", [])
        if merged:
            parts.append("Video matches:")
            for i, r in enumerate(merged[:5]):
                start = r.get("start_seconds", 0)
                text = r.get("text", r.get("description", ""))[:150]
                parts.append(f"  [{i+1}] At {start:.0f}s: {text}")

        has_evidence = False
        for enrichment in enrichments:
            if not enrichment:
                continue
            claims = enrichment.get("claims_verified", {})
            if claims:
                if not has_evidence:
                    parts.append("\nWeb verification:")
                    has_evidence = True
                for claim, info in claims.items():
                    if isinstance(info, dict):
                        parts.append(f"  - \"{claim}\" → {info.get('verdict', 'unverified')}")

        if not merged and not has_evidence:
            parts.append("No matching content found for this question.")

        return "\n".join(parts)

    async def cleanup(self):
        pass


# ── Auto-Discovery ───────────────────────────────────────────────────────
def register_routes(app):
    """Auto-discovered by main.py — registers synthesis endpoints."""
    import os as _os

    @app.get("/api/v1/synthesis/strategies", tags=["synthesis"])
    async def synthesis_strategies():
        """Available synthesis strategies and descriptions."""
        try:
            from agents.orchestrator import SYNTHESIS_STRATEGIES
            return {
                "agent": "synthesis",
                "strategies": {
                    k: v[:100] + "..." if len(v) > 100 else v
                    for k, v in SYNTHESIS_STRATEGIES.items()
                },
            }
        except ImportError:
            return {"agent": "synthesis", "strategies": {}, "error": "orchestrator not available"}

    @app.get("/api/v1/synthesis/stats", tags=["synthesis"])
    async def synthesis_stats():
        """Synthesis agent status."""
        return {
            "agent": "synthesis",
            "status": "active",
            "model": _os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
        }

    print("[SynthesisAgent] Registered routes: /api/v1/synthesis/strategies, /synthesis/stats")
