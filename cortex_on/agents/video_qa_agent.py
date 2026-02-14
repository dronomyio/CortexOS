"""
Video QA Agent — Domain-Agnostic
=================================

Ask any question about any video. The agent:
1. Searches the video corpus (Weaviate) for relevant segments
2. Uses VisionEngine to re-analyze matched segments if needed
3. Verifies answers with Parallel.ai web evidence
4. Pays for the query via x402

Works for lectures, news, tutorials, meetings, entertainment — anything.
"""
import logging
from typing import Any, Dict, List, Optional

from .base_agent import AgentResult, AgentStatus, BaseAgent
from .parallel_client import ParallelClient
from .x402_payment_agent import X402PaymentAgent
from config import ParallelAIConfig, X402Config

logger = logging.getLogger(__name__)


class VideoQAAgent(BaseAgent):

    def __init__(
        self,
        parallel_config: ParallelAIConfig,
        payment_agent: X402PaymentAgent,
        weaviate_client=None,
    ):
        super().__init__(name="video_qa")
        self.parallel = ParallelClient(parallel_config)
        self.payments = payment_agent
        self.weaviate = weaviate_client

    async def execute(self, task: Dict[str, Any]) -> AgentResult:
        """
        Answer a question about video content.

        Task: {"question": "...", "video_id": "...", "verify_with_web": true}
        """
        question = task.get("question", "")
        video_id = task.get("video_id")
        verify = task.get("verify_with_web", True)

        if not question:
            return AgentResult(agent_name=self.name, status=AgentStatus.FAILED, error="No question")

        # Pay via x402
        payment = await self.payments.pay_for_qa()

        # Step 1: Search video corpus
        video_matches = await self._search_corpus(question, video_id)

        # Step 2: Extract context from matches
        context_snippets = []
        entities = set()
        for match in video_matches:
            text = match.get("text", "")
            if text:
                context_snippets.append(text)
            for e in match.get("entities", []):
                entities.add(e)

        # Step 3: Verify with Parallel.ai
        web_evidence = {}
        if verify and (context_snippets or entities):
            web_evidence = await self._verify_with_web(
                question, list(entities)[:5], context_snippets[:3]
            )

        # Step 4: Build answer
        answer = {
            "question": question,
            "video_matches": video_matches,
            "web_evidence": web_evidence,
            "summary": self._build_summary(question, video_matches, web_evidence),
            "confidence": self._score(video_matches, web_evidence),
            "payment": payment.to_dict(),
        }

        return AgentResult(
            agent_name=self.name, status=AgentStatus.COMPLETED, data=answer,
            metadata={"matches": len(video_matches), "web_sources": len(web_evidence.get("citations", []))},
        )

    async def _search_corpus(self, question: str, video_id: Optional[str]) -> List[Dict]:
        """Search Weaviate for matching segments."""
        if self.weaviate is None:
            return []
        try:
            collection = self.weaviate.collections.get("VideoSegments")
            results = collection.query.near_text(query=question, limit=5)
            return [
                {
                    "text": obj.properties.get("text", ""),
                    "start_seconds": obj.properties.get("start_seconds", 0),
                    "end_seconds": obj.properties.get("end_seconds", 0),
                    "video_id": obj.properties.get("video_id", ""),
                    "entities": obj.properties.get("entities", []),
                    "score": getattr(obj.metadata, "certainty", 0) if obj.metadata else 0,
                }
                for obj in results.objects
            ]
        except Exception as e:
            self.logger.error(f"Weaviate search failed: {e}")
            return []

    async def _verify_with_web(
        self, question: str, entities: List[str], snippets: List[str],
    ) -> Dict:
        context = "; ".join(snippets[:2])[:500]
        objective = (
            f"Find current, verified information to answer: {question}. "
            f"Context from video: {context}"
        )
        result = await self.parallel.search(
            objective=objective,
            search_queries=[question] + entities[:2],
            max_results=5, max_chars_per_result=3000,
        )
        if "error" in result:
            return {"verified": False}

        citations = [
            {"url": r.get("url", ""), "title": r.get("title", ""), "excerpt": r.get("excerpt", "")[:200]}
            for r in result.get("results", [])
        ]
        return {"verified": True, "citations": citations}

    def _build_summary(self, question: str, matches: List, web: Dict) -> str:
        parts = []
        if matches:
            best = matches[0]
            parts.append(f"Found at {best['start_seconds']:.0f}s: \"{best['text'][:150]}\"")
        if web.get("verified") and web.get("citations"):
            parts.append(f"Verified with {len(web['citations'])} web sources.")
        return " ".join(parts) or "No matching content found."

    def _score(self, matches: List, web: Dict) -> float:
        s = 0.0
        if matches:
            s += max(m.get("score", 0) for m in matches) * 0.6
        if web.get("verified"):
            s += min(len(web.get("citations", [])) / 5, 1.0) * 0.4
        return round(min(s, 1.0), 3)

    async def cleanup(self):
        await self.parallel.close()


# ── Auto-Discovery ───────────────────────────────────────────────────────
def register_routes(app):
    """Auto-discovered by main.py — registers video QA endpoints."""
    from fastapi import Query as FQuery

    @app.get("/api/v1/qa/ask", tags=["video-qa"])
    async def qa_ask(
        question: str = FQuery(..., description="Question about indexed videos"),
        video_id: str = FQuery(None, description="Limit to specific video"),
        top_k: int = FQuery(5, ge=1, le=20),
    ):
        """Ask a question across indexed video content."""
        try:
            from config import ParallelAIConfig, X402Config
            from agents.parallel_client import ParallelClient
            from agents.x402_payment_agent import X402PaymentAgent

            payment_agent = X402PaymentAgent(X402Config())
            agent = VideoQAAgent(
                parallel_config=ParallelAIConfig(),
                payment_agent=payment_agent,
                weaviate_url=None,
            )
            result = await agent.execute({
                "question": question,
                "video_id": video_id,
                "top_k": top_k,
            })
            return result.data
        except ImportError as e:
            from fastapi import HTTPException
            raise HTTPException(503, f"VideoQAAgent deps not available: {e}")
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(500, f"QA failed: {e}")

    @app.get("/api/v1/qa/stats", tags=["video-qa"])
    async def qa_stats():
        """Video QA agent status."""
        return {"agent": "video-qa", "status": "active"}

    print("[VideoQAAgent] Registered routes: /api/v1/qa/ask, /qa/stats")
