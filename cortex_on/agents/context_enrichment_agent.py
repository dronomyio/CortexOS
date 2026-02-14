"""
Context Enrichment Agent — Domain-Agnostic
===========================================

Takes structured output from the VisionEngine (entities, topics, claims)
and enriches each with live web context via Parallel.ai.

Works on ANY video domain:
  - Lecture mentions "CRISPR gene editing" → pulls latest research papers
  - News clip claims "unemployment at 3.5%" → verifies with Bureau of Labor Statistics
  - Cooking video names "miso paste" → pulls recipes, brand comparisons
  - Meeting recording mentions "Q3 deadline" → pulls company context

No domain-specific logic. All specialization comes from the video content itself.
"""
import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentResult, AgentStatus, BaseAgent
from agents.parallel_client import ParallelClient
from agents.x402_payment_agent import X402PaymentAgent
from config import ParallelAIConfig, X402Config
from vision.engine import VisionAnalysis

logger = logging.getLogger(__name__)


class ContextEnrichmentAgent(BaseAgent):

    def __init__(self, parallel_config: ParallelAIConfig, payment_agent: X402PaymentAgent):
        super().__init__(name="context_enrichment")
        self.parallel = ParallelClient(parallel_config)
        self.payments = payment_agent
        self._cache: Dict[str, Dict] = {}

    async def execute(self, task: Dict[str, Any]) -> AgentResult:
        """
        Enrich video analysis with web context.

        Expected task:
        {
            "video_id": "...",
            "segment_index": 0,
            "analysis": VisionAnalysis.to_dict(),   # from VisionEngine
            "transcript": "...",                     # from Whisper
        }
        """
        video_id = task.get("video_id", "unknown")
        analysis = task.get("analysis", {})
        transcript = task.get("transcript", "")

        entities = analysis.get("entities", [])
        topics = analysis.get("topics", [])
        claims = analysis.get("claims", [])

        # Build search tasks from whatever the video contains
        search_tasks = []

        # Enrich entities (people, orgs, products, locations — any domain)
        for entity in entities[:5]:
            search_tasks.append({
                "objective": f"Current information and context about: {entity}",
                "search_queries": [entity, f"{entity} latest"],
                "type": "entity",
                "source": entity,
            })

        # Enrich topics with deeper context
        for topic in topics[:3]:
            search_tasks.append({
                "objective": f"Expert analysis and recent developments about: {topic}",
                "search_queries": [topic, f"{topic} analysis 2025"],
                "type": "topic",
                "source": topic,
            })

        # Verify factual claims
        for claim in claims[:3]:
            search_tasks.append({
                "objective": f"Verify this claim: {claim}. Find supporting or contradicting evidence.",
                "search_queries": [claim[:80]],
                "type": "claim_verification",
                "source": claim,
            })

        # Pay for enrichment via x402
        payment = await self.payments.pay_for_enrichment(len(search_tasks))

        # Execute searches
        enrichments = []
        for st in search_tasks:
            cache_key = hashlib.md5(json.dumps(st, sort_keys=True).encode()).hexdigest()
            if cache_key in self._cache:
                cached = self._cache[cache_key]
                if time.time() - cached["ts"] < 3600:
                    enrichments.append({"task": st, "result": cached["data"]})
                    continue

            result = await self.parallel.search(
                objective=st["objective"],
                search_queries=st["search_queries"],
                max_results=3,
                max_chars_per_result=3000,
            )
            if "error" not in result:
                self._cache[cache_key] = {"data": result, "ts": time.time()}
                enrichments.append({"task": st, "result": result})

        # Deep extraction on best sources
        all_urls = []
        for e in enrichments:
            for r in e["result"].get("results", [])[:1]:
                url = r.get("url")
                if url:
                    all_urls.append(url)

        extractions = []
        if all_urls[:3]:
            ext = await self.parallel.extract(
                urls=all_urls[:3],
                objective=f"Context about: {', '.join(entities[:3] + topics[:2])}",
            )
            if "error" not in ext:
                extractions = ext.get("results", [])

        # Compile
        compiled = {
            "video_id": video_id,
            "segment_index": task.get("segment_index", 0),
            "entity_enrichments": [
                {
                    "entity": e["task"]["source"],
                    "web_context": [r.get("excerpt", "")[:300] for r in e["result"].get("results", [])[:2]],
                    "sources": [{"url": r.get("url", ""), "title": r.get("title", "")} for r in e["result"].get("results", [])[:2]],
                }
                for e in enrichments if e["task"]["type"] == "entity"
            ],
            "topic_enrichments": [
                {
                    "topic": e["task"]["source"],
                    "web_context": [r.get("excerpt", "")[:300] for r in e["result"].get("results", [])[:2]],
                    "sources": [{"url": r.get("url", ""), "title": r.get("title", "")} for r in e["result"].get("results", [])[:2]],
                }
                for e in enrichments if e["task"]["type"] == "topic"
            ],
            "claim_verifications": [
                {
                    "claim": e["task"]["source"],
                    "evidence": [r.get("excerpt", "")[:300] for r in e["result"].get("results", [])[:2]],
                    "sources": [{"url": r.get("url", ""), "title": r.get("title", "")} for r in e["result"].get("results", [])[:2]],
                    "sources_found": len(e["result"].get("results", [])),
                }
                for e in enrichments if e["task"]["type"] == "claim_verification"
            ],
            "deep_extractions": extractions,
            "payment": payment.to_dict(),
        }

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            data=compiled,
            metadata={"searches_executed": len(search_tasks), "cache_hits": len(search_tasks) - len(enrichments)},
        )

    async def cleanup(self):
        await self.parallel.close()
