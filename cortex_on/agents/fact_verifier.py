"""
Fact-Verifier Subagent — Claim Extraction + Web Verification + x402 Billing
==============================================================================

CortexOS subagent that:
  1. Receives transcript segments with Opus planning metadata
  2. Extracts verifiable claims (numbers, predictions, dates, comparisons)
  3. Verifies each claim against live web data via Parallel.ai
  4. Bills per-verification via x402_payment_agent
  5. Cross-references claims against previously indexed videos in Weaviate
  6. Produces structured verdicts: CONFIRMED | CONTRADICTED | STALE | UNVERIFIABLE

Architecture:
  - Uses ParallelClient for web search/extract (unchanged)
  - Uses X402PaymentAgent for micropayments (unchanged)
  - Uses Opus 4.6 for claim extraction and verdict reasoning
  - Queries Weaviate for cross-video contradiction detection

Location: /app/agents/fact_verifier.py
Subagent definition: .claude-plugin/agents/fact-verifier.md
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════════════

class Verdict(str, Enum):
    """Claim verification verdict."""
    CONFIRMED    = "confirmed"       # Claim matches current evidence
    CONTRADICTED = "contradicted"    # Claim conflicts with current evidence
    STALE        = "stale"           # Claim was true but is now outdated
    REVISED      = "revised"         # Same person revised their own claim
    UNVERIFIABLE = "unverifiable"    # Cannot find evidence either way
    PENDING      = "pending"         # Not yet verified


@dataclass
class Claim:
    """A single verifiable claim extracted from a transcript."""
    text: str                           # The claim text
    segment_index: int                  # Which segment it came from
    timestamp_seconds: float = 0.0      # When in the video
    category: str = "general"           # prediction | statistic | comparison | date | quote
    speaker: str = ""                   # Who made the claim (if known)
    confidence: float = 0.0             # Opus confidence this is verifiable (0-1)
    video_id: str = ""
    claim_id: str = ""

    def __post_init__(self):
        if not self.claim_id:
            self.claim_id = hashlib.md5(
                f"{self.video_id}:{self.segment_index}:{self.text[:80]}".encode()
            ).hexdigest()[:12]


@dataclass
class Evidence:
    """Evidence found for/against a claim."""
    source_url: str
    source_title: str
    excerpt: str
    relevance: float = 0.0              # How relevant to the claim (0-1)
    supports_claim: Optional[bool] = None  # True=supports, False=contradicts, None=neutral


@dataclass
class VerificationResult:
    """Result of verifying a single claim."""
    claim: Claim
    verdict: Verdict = Verdict.PENDING
    evidence: List[Evidence] = field(default_factory=list)
    reasoning: str = ""                 # Opus explanation of the verdict
    cross_video_matches: List[Dict] = field(default_factory=list)  # Matching claims from other videos
    payment_amount_usdc: float = 0.0
    payment_status: str = "pending"
    verified_at: float = 0.0
    verification_time_ms: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "claim_id": self.claim.claim_id,
            "claim_text": self.claim.text,
            "category": self.claim.category,
            "speaker": self.claim.speaker,
            "timestamp_seconds": self.claim.timestamp_seconds,
            "segment_index": self.claim.segment_index,
            "verdict": self.verdict.value,
            "reasoning": self.reasoning,
            "evidence_count": len(self.evidence),
            "evidence": [
                {
                    "url": e.source_url,
                    "title": e.source_title,
                    "excerpt": e.excerpt[:300],
                    "supports_claim": e.supports_claim,
                }
                for e in self.evidence[:5]
            ],
            "cross_video_matches": self.cross_video_matches[:5],
            "payment": {
                "amount_usdc": self.payment_amount_usdc,
                "status": self.payment_status,
            },
            "verified_at": self.verified_at,
            "verification_time_ms": round(self.verification_time_ms, 1),
        }


@dataclass
class FactCheckReport:
    """Full fact-check report for a video or set of segments."""
    video_id: str
    total_claims: int = 0
    verified_claims: int = 0
    results: List[VerificationResult] = field(default_factory=list)
    total_cost_usdc: float = 0.0
    total_time_seconds: float = 0.0
    summary: str = ""

    @property
    def contradictions(self) -> List[VerificationResult]:
        return [r for r in self.results if r.verdict == Verdict.CONTRADICTED]

    @property
    def stale(self) -> List[VerificationResult]:
        return [r for r in self.results if r.verdict == Verdict.STALE]

    @property
    def revised(self) -> List[VerificationResult]:
        return [r for r in self.results if r.verdict == Verdict.REVISED]

    def to_dict(self) -> Dict:
        return {
            "video_id": self.video_id,
            "total_claims": self.total_claims,
            "verified_claims": self.verified_claims,
            "verdicts": {
                v.value: len([r for r in self.results if r.verdict == v])
                for v in Verdict if v != Verdict.PENDING
            },
            "contradictions": [r.to_dict() for r in self.contradictions],
            "stale_claims": [r.to_dict() for r in self.stale],
            "revised_claims": [r.to_dict() for r in self.revised],
            "all_results": [r.to_dict() for r in self.results],
            "total_cost_usdc": round(self.total_cost_usdc, 4),
            "total_time_seconds": round(self.total_time_seconds, 1),
            "summary": self.summary,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Fact Verifier Agent
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FactVerifierConfig:
    """Configuration for the fact-verifier agent."""
    # Parallel.ai
    parallel_api_key: str = field(
        default_factory=lambda: os.environ.get("PARALLEL_API_KEY", "")
    )
    parallel_search_url: str = "https://api.parallel.ai/v1beta/search"
    parallel_extract_url: str = "https://api.parallel.ai/v1beta/extract"
    parallel_beta_header: str = "search-extract-2025-10-10"

    # x402
    x402_enabled: bool = field(
        default_factory=lambda: os.environ.get("X402_ENABLED", "true").lower() == "true"
    )
    x402_server_url: str = field(
        default_factory=lambda: os.environ.get("X402_SERVER_URL", "http://localhost:8402")
    )
    price_per_verification: float = 0.03  # $0.03 per claim verified

    # Weaviate for cross-referencing
    weaviate_url: str = field(
        default_factory=lambda: os.environ.get("WEAVIATE_URL", "http://weaviate:8080")
    )

    # Opus for claim extraction + verdict reasoning
    anthropic_model: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_MODEL_NAME", "claude-opus-4-6")
    )

    # Limits
    max_claims_per_video: int = 20
    max_concurrent_verifications: int = 5
    cache_ttl_seconds: int = 3600  # 1 hour


class FactVerifier:
    """
    CortexOS Fact-Verifier Subagent.

    Extracts verifiable claims from video transcripts, verifies them
    against live web data via Parallel.ai, cross-references against
    indexed videos in Weaviate, and bills per-verification via x402.

    Usage:
        verifier = FactVerifier(config)
        await verifier.initialize()

        # Verify claims in a set of transcript segments
        report = await verifier.verify_video(
            video_id="abc123",
            segments=segments_from_whisper,
            opus_plan=plan_from_opus_planner,
        )

        # Or verify a single claim
        result = await verifier.verify_claim(claim)
    """

    def __init__(self, config: Optional[FactVerifierConfig] = None):
        self.config = config or FactVerifierConfig()
        self._parallel = None   # Lazy-loaded ParallelClient
        self._payments = None   # Lazy-loaded X402PaymentAgent
        self._opus = None       # Lazy-loaded Opus planner
        self._cache: Dict[str, Dict] = {}
        self._stats = {
            "claims_extracted": 0,
            "claims_verified": 0,
            "contradictions_found": 0,
            "stale_found": 0,
            "revised_found": 0,
            "total_cost_usdc": 0.0,
            "errors": 0,
        }

    async def initialize(self):
        """Initialize connections to Parallel.ai, x402, and Weaviate."""
        # Parallel.ai client
        try:
            from agents.parallel_client import ParallelClient
            from config import ParallelAIConfig
            parallel_config = ParallelAIConfig()
            self._parallel = ParallelClient(parallel_config)
            logger.info("[FactVerifier] Parallel.ai client initialized")
        except ImportError:
            logger.warning("[FactVerifier] ParallelClient not available — web verification disabled")

        # x402 payment agent
        try:
            from agents.x402_payment_agent import X402PaymentAgent
            from config import X402Config
            x402_config = X402Config()
            self._payments = X402PaymentAgent(x402_config)
            logger.info("[FactVerifier] x402 payment agent initialized")
        except ImportError:
            logger.warning("[FactVerifier] X402PaymentAgent not available — billing disabled")

        # Opus planner for claim extraction
        try:
            from agents.opus_planner import OpusPlanner
            self._opus = OpusPlanner()
            logger.info("[FactVerifier] Opus planner connected for claim extraction")
        except ImportError:
            logger.warning("[FactVerifier] OpusPlanner not available — using regex claim extraction")

    # ─── Claim Extraction ───────────────────────────────────────────────

    async def extract_claims(
        self,
        segments: List[Dict],
        opus_plan: Optional[Dict] = None,
    ) -> List[Claim]:
        """
        Extract verifiable claims from transcript segments.

        Uses Opus 4.6 if available for intelligent extraction,
        falls back to keyword-based extraction.
        """
        claims = []

        if self._opus:
            claims = await self._extract_claims_opus(segments, opus_plan)
        else:
            claims = self._extract_claims_heuristic(segments)

        self._stats["claims_extracted"] += len(claims)
        return claims[:self.config.max_claims_per_video]

    async def _extract_claims_opus(
        self,
        segments: List[Dict],
        opus_plan: Optional[Dict] = None,
    ) -> List[Claim]:
        """Use Opus 4.6 to intelligently extract verifiable claims."""
        try:
            import anthropic
            client = anthropic.AsyncAnthropic()

            # Build transcript context
            transcript_text = ""
            for i, seg in enumerate(segments):
                text = seg.get("transcript", seg.get("text", ""))
                start = seg.get("start_seconds", seg.get("start", 0))
                transcript_text += f"\n[Segment {i} @ {start:.0f}s]: {text}\n"

            # Opus plan hints — which segments have verifiable claims
            plan_hint = ""
            if opus_plan and "segment_plans" in opus_plan:
                high_priority = [
                    sp for sp in opus_plan["segment_plans"]
                    if sp.get("enrichment_priority", 0) > 0.5
                    or sp.get("risk_score", 0) > 0.3
                ]
                if high_priority:
                    plan_hint = (
                        "\n\nThe Opus planner flagged these segments as high-priority "
                        "for verification:\n" +
                        "\n".join(f"- Segment {sp.get('segment_index', '?')}: "
                                 f"risk={sp.get('risk_score', 0):.1f}, "
                                 f"enrichment={sp.get('enrichment_priority', 0):.1f}"
                                 for sp in high_priority[:10])
                    )

            response = await client.messages.create(
                model=self.config.anthropic_model,
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": f"""Extract all verifiable factual claims from this video transcript.

For each claim, provide:
- text: The exact claim (quote or close paraphrase)
- segment_index: Which segment it came from
- timestamp_seconds: Approximate time in video
- category: One of [prediction, statistic, comparison, date, quote, price_target, policy]
- speaker: Who made the claim (if identifiable)
- confidence: How confident you are this is a verifiable claim (0.0-1.0)

Focus on claims that can be checked against external sources:
- Specific numbers, percentages, prices
- Predictions with timeframes ("BTC will hit $200K by June")
- Historical claims ("the Fed raised rates 3 times")
- Comparisons ("better than last quarter")
- Dates and deadlines

Skip opinions, hedged statements ("maybe", "could"), and obvious filler.
{plan_hint}

TRANSCRIPT:
{transcript_text[:8000]}

Respond ONLY with a JSON array of claims. No other text."""
                }],
            )

            # Parse response
            text = response.content[0].text.strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]

            raw_claims = json.loads(text)
            return [
                Claim(
                    text=c.get("text", ""),
                    segment_index=c.get("segment_index", 0),
                    timestamp_seconds=c.get("timestamp_seconds", 0),
                    category=c.get("category", "general"),
                    speaker=c.get("speaker", ""),
                    confidence=c.get("confidence", 0.5),
                    video_id="",  # Set by caller
                )
                for c in raw_claims
                if c.get("confidence", 0) >= 0.3
            ]

        except Exception as e:
            logger.error(f"[FactVerifier] Opus claim extraction failed: {e}")
            return self._extract_claims_heuristic(segments)

    def _extract_claims_heuristic(self, segments: List[Dict]) -> List[Claim]:
        """Fallback: keyword-based claim extraction."""
        import re
        claims = []
        # Patterns that indicate verifiable claims
        patterns = [
            (r'\$[\d,]+(?:\.\d+)?(?:\s*(?:billion|million|trillion|K|M|B))?', "price_target"),
            (r'\d+(?:\.\d+)?%', "statistic"),
            (r'(?:will|going to|expect|predict|forecast).*?(?:by|in|before)\s+\w+\s+\d{4}', "prediction"),
            (r'(?:rose|fell|dropped|increased|decreased|grew)\s+(?:by\s+)?\d', "statistic"),
            (r'(?:rate|gdp|inflation|unemployment|cpi)\s+(?:is|was|at)\s+\d', "statistic"),
        ]

        for i, seg in enumerate(segments):
            text = seg.get("transcript", seg.get("text", ""))
            start = seg.get("start_seconds", seg.get("start", 0))
            for pattern, category in patterns:
                matches = re.finditer(pattern, text, re.IGNORECASE)
                for m in matches:
                    # Extract sentence containing the match
                    sent_start = text.rfind(".", 0, m.start())
                    sent_end = text.find(".", m.end())
                    sentence = text[max(0, sent_start + 1):sent_end + 1 if sent_end > 0 else len(text)].strip()
                    if len(sentence) > 20:
                        claims.append(Claim(
                            text=sentence[:200],
                            segment_index=i,
                            timestamp_seconds=start,
                            category=category,
                            confidence=0.5,
                        ))

        # Deduplicate
        seen = set()
        unique = []
        for c in claims:
            key = c.text[:60].lower()
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique

    # ─── Claim Verification ─────────────────────────────────────────────

    async def verify_claim(self, claim: Claim) -> VerificationResult:
        """
        Verify a single claim against live web data.

        Flow:
          1. Check cache
          2. Pay via x402
          3. Search Parallel.ai for evidence
          4. Query Weaviate for cross-video matches
          5. Use Opus to reason about verdict
          6. Return structured result
        """
        t0 = time.perf_counter()
        result = VerificationResult(claim=claim)

        # Cache check
        cache_key = hashlib.md5(claim.text[:100].lower().encode()).hexdigest()
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if time.time() - cached["ts"] < self.config.cache_ttl_seconds:
                logger.info(f"[FactVerifier] Cache hit for claim: {claim.text[:60]}")
                return cached["result"]

        try:
            # ── Step 1: Pay for verification ─────────────────────────
            if self._payments:
                payment = await self._payments.authorize_and_pay(
                    action=f"fact_verify:{claim.claim_id}",
                    amount_usdc=self.config.price_per_verification,
                    resource_path="/api/v1/premium/fact-verification",
                )
                result.payment_amount_usdc = payment.amount_usdc
                result.payment_status = payment.status
            else:
                result.payment_status = "skipped"

            # ── Step 2: Search for evidence via Parallel.ai ──────────
            evidence = []
            if self._parallel:
                evidence = await self._search_evidence(claim)
                result.evidence = evidence

            # ── Step 3: Cross-reference with Weaviate ────────────────
            cross_matches = await self._cross_reference_weaviate(claim)
            result.cross_video_matches = cross_matches

            # ── Step 4: Opus reasons about the verdict ───────────────
            verdict, reasoning = await self._opus_verdict(claim, evidence, cross_matches)
            result.verdict = verdict
            result.reasoning = reasoning

            # Update stats
            self._stats["claims_verified"] += 1
            self._stats["total_cost_usdc"] += result.payment_amount_usdc
            if verdict == Verdict.CONTRADICTED:
                self._stats["contradictions_found"] += 1
            elif verdict == Verdict.STALE:
                self._stats["stale_found"] += 1
            elif verdict == Verdict.REVISED:
                self._stats["revised_found"] += 1

        except Exception as e:
            logger.error(f"[FactVerifier] Verification failed for '{claim.text[:60]}': {e}")
            result.verdict = Verdict.UNVERIFIABLE
            result.reasoning = f"Verification error: {str(e)}"
            self._stats["errors"] += 1

        result.verified_at = time.time()
        result.verification_time_ms = (time.perf_counter() - t0) * 1000

        # Cache result
        self._cache[cache_key] = {"result": result, "ts": time.time()}
        return result

    async def _search_evidence(self, claim: Claim) -> List[Evidence]:
        """Search Parallel.ai for evidence supporting or contradicting the claim."""
        evidence = []

        # Build targeted search queries based on claim category
        queries = [claim.text[:80]]
        if claim.category == "prediction":
            queries.append(f"{claim.speaker} prediction latest" if claim.speaker else claim.text[:40] + " latest")
        elif claim.category == "price_target":
            queries.append(f"{claim.speaker} price target 2025" if claim.speaker else claim.text[:40])
        elif claim.category == "statistic":
            queries.append(f"{claim.text[:40]} current data")

        try:
            result = await self._parallel.search(
                objective=f"Verify this claim: {claim.text[:200]}. Find supporting or contradicting evidence.",
                search_queries=queries[:3],
                max_results=5,
                max_chars_per_result=3000,
            )

            if "error" not in result:
                for r in result.get("results", [])[:5]:
                    evidence.append(Evidence(
                        source_url=r.get("url", ""),
                        source_title=r.get("title", ""),
                        excerpt=r.get("excerpt", r.get("text", ""))[:500],
                        relevance=r.get("relevance_score", 0.5),
                    ))

            # Deep extraction on top 2 sources for more context
            top_urls = [e.source_url for e in evidence[:2] if e.source_url]
            if top_urls:
                ext = await self._parallel.extract(
                    urls=top_urls,
                    objective=f"Evidence about: {claim.text[:100]}",
                )
                if "error" not in ext:
                    for i, r in enumerate(ext.get("results", [])[:2]):
                        if i < len(evidence):
                            evidence[i].excerpt = r.get("excerpt", evidence[i].excerpt)[:500]

        except Exception as e:
            logger.warning(f"[FactVerifier] Parallel.ai search failed: {e}")

        return evidence

    async def _cross_reference_weaviate(self, claim: Claim) -> List[Dict]:
        """Query Weaviate for similar claims in other indexed videos."""
        matches = []
        try:
            import weaviate
            client = weaviate.connect_to_local(
                host=self.config.weaviate_url.replace("http://", "").split(":")[0],
                port=int(self.config.weaviate_url.split(":")[-1]),
            )

            # Semantic search for similar transcript segments
            collection = client.collections.get("VideoChunks")
            response = collection.query.near_text(
                query=claim.text[:200],
                limit=5,
                return_metadata=["distance"],
            )

            for obj in response.objects:
                props = obj.properties
                # Skip matches from the same video
                if props.get("video_id") == claim.video_id:
                    continue

                distance = obj.metadata.distance if obj.metadata else 1.0
                if distance < 0.7:  # Only reasonably close matches
                    matches.append({
                        "video_id": props.get("video_id", ""),
                        "text": props.get("text", "")[:300],
                        "start_seconds": props.get("start_seconds", 0),
                        "title": props.get("title", ""),
                        "distance": round(distance, 3),
                    })

            client.close()
        except Exception as e:
            logger.debug(f"[FactVerifier] Weaviate cross-reference failed: {e}")

        return matches

    async def _opus_verdict(
        self,
        claim: Claim,
        evidence: List[Evidence],
        cross_matches: List[Dict],
    ) -> tuple:
        """Use Opus to reason about the verdict based on evidence."""
        if not evidence and not cross_matches:
            return Verdict.UNVERIFIABLE, "No evidence found to verify or contradict this claim."

        # If no Opus available, use simple heuristic
        if not self._opus:
            return self._heuristic_verdict(claim, evidence, cross_matches)

        try:
            import anthropic
            client = anthropic.AsyncAnthropic()

            evidence_text = "\n".join([
                f"- [{e.source_title}] ({e.source_url}): {e.excerpt[:200]}"
                for e in evidence[:5]
            ]) or "No web evidence found."

            cross_text = "\n".join([
                f"- Video {m['video_id']} @ {m['start_seconds']:.0f}s: {m['text'][:200]}"
                for m in cross_matches[:5]
            ]) or "No cross-video matches found."

            response = await client.messages.create(
                model=self.config.anthropic_model,
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": f"""You are a fact-checker. Determine the verdict for this claim.

CLAIM: "{claim.text}"
Speaker: {claim.speaker or 'Unknown'}
Category: {claim.category}
Video timestamp: {claim.timestamp_seconds:.0f}s

WEB EVIDENCE:
{evidence_text}

SIMILAR CLAIMS FROM OTHER INDEXED VIDEOS:
{cross_text}

Determine the verdict:
- CONFIRMED: Evidence supports the claim
- CONTRADICTED: Evidence directly contradicts the claim
- STALE: Claim was once true but is now outdated
- REVISED: The same speaker has since changed their position
- UNVERIFIABLE: Insufficient evidence

Respond with JSON only:
{{"verdict": "...", "reasoning": "Brief 1-2 sentence explanation"}}"""
                }],
            )

            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]

            parsed = json.loads(text)
            verdict_str = parsed.get("verdict", "unverifiable").lower()
            reasoning = parsed.get("reasoning", "")

            verdict_map = {
                "confirmed": Verdict.CONFIRMED,
                "contradicted": Verdict.CONTRADICTED,
                "stale": Verdict.STALE,
                "revised": Verdict.REVISED,
                "unverifiable": Verdict.UNVERIFIABLE,
            }
            return verdict_map.get(verdict_str, Verdict.UNVERIFIABLE), reasoning

        except Exception as e:
            logger.warning(f"[FactVerifier] Opus verdict failed: {e}")
            return self._heuristic_verdict(claim, evidence, cross_matches)

    def _heuristic_verdict(
        self, claim: Claim, evidence: List[Evidence], cross_matches: List[Dict],
    ) -> tuple:
        """Simple keyword-based verdict when Opus is unavailable."""
        claim_lower = claim.text.lower()

        # Check cross-video contradictions
        for m in cross_matches:
            m_text = m.get("text", "").lower()
            # Very basic: if same topic but different numbers, likely contradiction
            if m.get("distance", 1.0) < 0.4:
                return Verdict.CONTRADICTED, f"Conflicting claim found in video {m['video_id']}"

        if evidence:
            return Verdict.CONFIRMED, "Supporting evidence found via web search."

        return Verdict.UNVERIFIABLE, "Insufficient evidence to verify."

    # ─── High-Level API ─────────────────────────────────────────────────

    async def verify_video(
        self,
        video_id: str,
        segments: List[Dict],
        opus_plan: Optional[Dict] = None,
        progress_callback: Optional[Callable] = None,
    ) -> FactCheckReport:
        """
        Full fact-check pipeline for a video.

        Args:
            video_id: The video to fact-check
            segments: Transcript segments from Whisper
            opus_plan: Optional Opus planning output
            progress_callback: async fn(progress: float, message: str)

        Returns:
            FactCheckReport with all verification results
        """
        t0 = time.perf_counter()
        report = FactCheckReport(video_id=video_id)

        # Step 1: Extract claims
        if progress_callback:
            await progress_callback(0.1, "Extracting verifiable claims from transcript…")

        claims = await self.extract_claims(segments, opus_plan)
        for c in claims:
            c.video_id = video_id
        report.total_claims = len(claims)

        if not claims:
            report.summary = "No verifiable claims found in this video."
            return report

        logger.info(f"[FactVerifier] Extracted {len(claims)} claims from video {video_id}")

        # Step 2: Verify claims concurrently
        sem = asyncio.Semaphore(self.config.max_concurrent_verifications)

        async def _verify_bounded(i: int, claim: Claim) -> VerificationResult:
            async with sem:
                if progress_callback:
                    pct = 0.1 + 0.8 * (i / len(claims))
                    await progress_callback(pct, f"Verifying claim {i + 1}/{len(claims)}: {claim.text[:50]}…")
                return await self.verify_claim(claim)

        results = await asyncio.gather(
            *[_verify_bounded(i, c) for i, c in enumerate(claims)],
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, VerificationResult):
                report.results.append(r)
                report.total_cost_usdc += r.payment_amount_usdc
            else:
                logger.error(f"[FactVerifier] Verification exception: {r}")
                self._stats["errors"] += 1

        report.verified_claims = len(report.results)
        report.total_time_seconds = time.perf_counter() - t0

        # Step 3: Generate summary
        if progress_callback:
            await progress_callback(0.95, "Generating fact-check summary…")

        report.summary = self._generate_summary(report)

        logger.info(
            f"[FactVerifier] Video {video_id}: {report.total_claims} claims, "
            f"{len(report.contradictions)} contradictions, "
            f"{len(report.stale)} stale, "
            f"${report.total_cost_usdc:.4f} cost, "
            f"{report.total_time_seconds:.1f}s"
        )

        return report

    def _generate_summary(self, report: FactCheckReport) -> str:
        """Generate human-readable summary of the fact-check."""
        parts = [f"Analyzed {report.total_claims} claims from video {report.video_id}."]

        verdicts = {}
        for r in report.results:
            verdicts[r.verdict.value] = verdicts.get(r.verdict.value, 0) + 1

        parts.append(
            f"Verdicts: {', '.join(f'{v}: {c}' for v, c in sorted(verdicts.items()))}."
        )

        if report.contradictions:
            parts.append(f"\n⚠️ {len(report.contradictions)} CONTRADICTIONS found:")
            for r in report.contradictions[:3]:
                parts.append(f"  • {r.claim.text[:100]} — {r.reasoning}")

        if report.stale:
            parts.append(f"\n⏰ {len(report.stale)} STALE claims:")
            for r in report.stale[:3]:
                parts.append(f"  • {r.claim.text[:100]} — {r.reasoning}")

        if report.revised:
            parts.append(f"\n🔄 {len(report.revised)} REVISED positions:")
            for r in report.revised[:3]:
                parts.append(f"  • {r.claim.text[:100]} — {r.reasoning}")

        parts.append(f"\nTotal verification cost: ${report.total_cost_usdc:.4f} USDC")
        return "\n".join(parts)

    # ─── Stats & Cleanup ────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        return dict(self._stats)

    async def close(self):
        if self._parallel:
            await self._parallel.close()
        if self._payments:
            await self._payments.close()


# ═══════════════════════════════════════════════════════════════════════════
# Auto-Discovery: register_routes(app)
# ═══════════════════════════════════════════════════════════════════════════
# Drop this file in agents/ and main.py auto-discovers it.

_verifier_instance: Optional[FactVerifier] = None

def _get_verifier() -> FactVerifier:
    """Lazy singleton for the fact-verifier agent."""
    global _verifier_instance
    if _verifier_instance is None:
        _verifier_instance = FactVerifier()
    return _verifier_instance


def register_routes(app):
    """Auto-discovered by main.py — registers fact-verifier API endpoints."""
    from fastapi import Query as FQuery
    from pydantic import BaseModel
    from typing import Optional as Opt, List as Lst

    class VerifyRequest(BaseModel):
        video_id: str
        claim_text: Opt[str] = None           # Verify single claim
        segment_indices: Opt[Lst[int]] = None  # Verify specific segments
        max_claims: int = 20

    class VerifyResponse(BaseModel):
        video_id: str
        total_claims: int
        verified_claims: int
        contradictions: int
        stale: int
        revised: int
        total_cost_usdc: float
        summary: str

    @app.post("/api/v1/verify", response_model=VerifyResponse, tags=["fact-verifier"])
    async def verify_video(req: VerifyRequest):
        """
        Fact-check a previously ingested video.

        Extracts verifiable claims from transcript segments,
        verifies each against live web data via Parallel.ai,
        cross-references with other indexed videos in Weaviate,
        and bills per-verification via x402.
        """
        verifier = _get_verifier()
        await verifier.initialize()

        # Load transcript segments from the job output
        from pathlib import Path
        data_dir = Path(os.getenv("DATA_DIR", "/data"))
        json_path = data_dir / "out" / req.video_id / "snippets_with_transcripts.json"

        if not json_path.exists():
            from fastapi import HTTPException
            raise HTTPException(404, f"No transcript found for video {req.video_id}. Ingest it first.")

        segments = json.loads(json_path.read_text())
        if isinstance(segments, dict):
            segments = segments.get("snippets", segments.get("segments", []))

        # Filter to specific segments if requested
        if req.segment_indices:
            segments = [s for i, s in enumerate(segments) if i in req.segment_indices]

        # Load Opus plan if available
        opus_plan = None
        plan_path = data_dir / "out" / req.video_id / "opus_plan.json"
        if plan_path.exists():
            try:
                opus_plan = json.loads(plan_path.read_text())
            except Exception:
                pass

        # Override max claims
        verifier.config.max_claims_per_video = req.max_claims

        # Run verification
        report = await verifier.verify_video(
            video_id=req.video_id,
            segments=segments,
            opus_plan=opus_plan,
        )

        return VerifyResponse(
            video_id=report.video_id,
            total_claims=report.total_claims,
            verified_claims=report.verified_claims,
            contradictions=len(report.contradictions),
            stale=len(report.stale),
            revised=len(report.revised),
            total_cost_usdc=report.total_cost_usdc,
            summary=report.summary,
        )

    @app.get("/api/v1/verify/{video_id}/report", tags=["fact-verifier"])
    async def get_verify_report(video_id: str):
        """Get the full fact-check report with all evidence and verdicts."""
        verifier = _get_verifier()
        await verifier.initialize()

        from pathlib import Path
        data_dir = Path(os.getenv("DATA_DIR", "/data"))
        json_path = data_dir / "out" / video_id / "snippets_with_transcripts.json"

        if not json_path.exists():
            from fastapi import HTTPException
            raise HTTPException(404, f"No transcript found for video {video_id}")

        segments = json.loads(json_path.read_text())
        if isinstance(segments, dict):
            segments = segments.get("snippets", segments.get("segments", []))

        report = await verifier.verify_video(video_id=video_id, segments=segments)
        return report.to_dict()

    @app.post("/api/v1/verify/claim", tags=["fact-verifier"])
    async def verify_single_claim(
        text: str = FQuery(..., description="The claim to verify"),
        speaker: str = FQuery("", description="Who made the claim"),
        category: str = FQuery("general", description="Claim category"),
        video_id: str = FQuery("", description="Source video ID"),
    ):
        """Verify a single claim against web data and indexed videos."""
        verifier = _get_verifier()
        await verifier.initialize()

        claim = Claim(
            text=text,
            segment_index=0,
            category=category,
            speaker=speaker,
            video_id=video_id,
        )
        result = await verifier.verify_claim(claim)
        return result.to_dict()

    @app.get("/api/v1/verify/stats", tags=["fact-verifier"])
    async def verify_stats():
        """Fact-verifier agent statistics."""
        verifier = _get_verifier()
        return {
            "agent": "fact-verifier",
            "status": "active",
            **verifier.get_stats(),
        }

    logger.info("[FactVerifier] Registered routes: /api/v1/verify, /verify/{id}/report, /verify/claim, /verify/stats")
