"""
CortexOS Intelligence Layer — Cross-Video Analysis
====================================================

Builds on fact_verifier.py to provide:
  1. Cross-video contradiction detection (end-to-end)
  2. Speaker reliability scorecards
  3. External data cross-referencing (ETH prices, exchange flows, etc.)

Auto-discovered by main.py via register_routes(app).

Location: cortex_on/agents/intelligence_layer.py
"""

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ContradictionPair:
    """Two claims from the same or different speakers that contradict each other."""
    claim_a: Dict
    claim_b: Dict
    contradiction_type: str  # "self_contradiction", "cross_speaker", "data_contradiction"
    severity: str            # "high", "medium", "low"
    explanation: str
    data_evidence: Optional[Dict] = None  # External data that proves the contradiction

    def to_dict(self) -> Dict:
        return {
            "claim_a": self.claim_a,
            "claim_b": self.claim_b,
            "contradiction_type": self.contradiction_type,
            "severity": self.severity,
            "explanation": self.explanation,
            "data_evidence": self.data_evidence,
        }


@dataclass
class SpeakerScore:
    """Accumulated reliability score for a speaker."""
    speaker: str
    total_claims: int = 0
    verified: int = 0
    contradicted: int = 0
    stale: int = 0
    self_contradictions: int = 0
    unverifiable: int = 0
    data_contradictions: int = 0   # Claims contradicted by external data
    videos_analyzed: int = 0
    claims_by_category: Dict[str, int] = field(default_factory=dict)
    worst_misses: List[Dict] = field(default_factory=list)  # Top 5 worst contradictions

    @property
    def accuracy_rate(self) -> float:
        testable = self.verified + self.contradicted + self.stale + self.data_contradictions
        if testable == 0:
            return 0.0
        return round(self.verified / testable, 3)

    @property
    def reliability_grade(self) -> str:
        r = self.accuracy_rate
        if r >= 0.8: return "A"
        if r >= 0.65: return "B"
        if r >= 0.5: return "C+"
        if r >= 0.35: return "C"
        if r >= 0.2: return "D"
        return "F"

    @property
    def pattern_summary(self) -> str:
        patterns = []
        if self.self_contradictions > 1:
            patterns.append(f"Revises positions without acknowledgment ({self.self_contradictions} times)")
        if self.data_contradictions > 2:
            patterns.append(f"Frequently cites incorrect data ({self.data_contradictions} times)")
        if self.stale > 2:
            patterns.append(f"Claims become outdated quickly ({self.stale} stale)")
        # Check category patterns
        if self.claims_by_category.get("prediction", 0) > self.total_claims * 0.4:
            patterns.append("Heavy on predictions (hard to verify)")
        if not patterns:
            patterns.append("Insufficient data for pattern detection")
        return ". ".join(patterns)

    def to_dict(self) -> Dict:
        return {
            "speaker": self.speaker,
            "total_claims": self.total_claims,
            "verified": self.verified,
            "contradicted": self.contradicted,
            "stale": self.stale,
            "self_contradictions": self.self_contradictions,
            "data_contradictions": self.data_contradictions,
            "unverifiable": self.unverifiable,
            "videos_analyzed": self.videos_analyzed,
            "accuracy_rate": self.accuracy_rate,
            "reliability_grade": self.reliability_grade,
            "pattern": self.pattern_summary,
            "claims_by_category": self.claims_by_category,
            "worst_misses": self.worst_misses[:5],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Intelligence Layer
# ═══════════════════════════════════════════════════════════════════════════

class IntelligenceLayer:
    """
    Orchestrates cross-video analysis using fact_verifier + Opus 4.6.

    Three capabilities:
      1. find_contradictions — scan all indexed videos for conflicting claims
      2. speaker_scorecard — reliability scores per speaker
      3. cross_reference_data — compare claims against external data (ETH prices, etc.)
    """

    def __init__(self):
        self._verifier = None
        self._opus_client = None
        self._weaviate_client = None
        self._model = os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6")
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "")

    async def initialize(self):
        """Lazy-load fact verifier and connections."""
        if self._verifier is None:
            try:
                from agents.fact_verifier import FactVerifier
                self._verifier = FactVerifier()
                await self._verifier.initialize()
                logger.info("[IntelligenceLayer] FactVerifier connected")
            except Exception as e:
                logger.warning(f"[IntelligenceLayer] FactVerifier unavailable: {e}")

    # ─── Helper: Get all indexed videos ─────────────────────────────────

    def _get_indexed_videos(self) -> List[Dict]:
        """Scan /data/out/ for all ingested videos with transcripts."""
        data_dir = os.getenv("DATA_DIR", "/data")
        out_dir = os.path.join(data_dir, "out")
        videos = []

        if not os.path.isdir(out_dir):
            return videos

        for video_id in os.listdir(out_dir):
            video_dir = os.path.join(out_dir, video_id)
            transcript_path = os.path.join(video_dir, "snippets_with_transcripts.json")
            if os.path.isfile(transcript_path):
                meta = {"video_id": video_id, "transcript_path": transcript_path}
                # Load Opus plan if available
                plan_path = os.path.join(video_dir, "opus_plan.json")
                if os.path.isfile(plan_path):
                    try:
                        meta["opus_plan"] = json.loads(open(plan_path).read())
                    except Exception:
                        pass
                # Load metadata if available
                meta_path = os.path.join(video_dir, "metadata.json")
                if os.path.isfile(meta_path):
                    try:
                        meta.update(json.loads(open(meta_path).read()))
                    except Exception:
                        pass
                videos.append(meta)

        return videos

    def _load_segments(self, transcript_path: str) -> List[Dict]:
        """Load transcript segments from JSON."""
        try:
            data = json.loads(open(transcript_path).read())
            if isinstance(data, dict):
                return data.get("snippets", data.get("segments", []))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    # ─── Helper: Call Opus 4.6 ──────────────────────────────────────────

    async def _call_opus(self, system: str, user: str, max_tokens: int = 4000) -> str:
        """Call Opus 4.6 for reasoning tasks."""
        if not self._api_key:
            return "{}"

        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=self._api_key)
            response = await client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return response.content[0].text if response.content else "{}"
        except Exception as e:
            logger.error(f"[IntelligenceLayer] Opus call failed: {e}")
            return "{}"

    # ═══════════════════════════════════════════════════════════════════════
    # 1. CONTRADICTION DETECTOR
    # ═══════════════════════════════════════════════════════════════════════

    async def find_contradictions(
        self,
        speaker_filter: str = "",
        topic_filter: str = "",
        since_date: str = "",
        max_videos: int = 20,
        external_data: Optional[Dict] = None,
    ) -> Dict:
        """
        Scan all indexed videos for contradicting claims.

        Returns:
          - self_contradictions: same speaker, different claims
          - cross_speaker: different speakers, conflicting claims
          - data_contradictions: claims contradicted by external_data
        """
        await self.initialize()
        start = time.time()

        videos = self._get_indexed_videos()[:max_videos]
        if not videos:
            return {"contradictions": [], "videos_analyzed": 0, "message": "No indexed videos found"}

        # Extract claims from all videos
        all_claims = []
        for v in videos:
            segments = self._load_segments(v["transcript_path"])
            if not segments:
                continue

            if self._verifier and self._verifier._opus:
                claims = await self._verifier.extract_claims(segments, v.get("opus_plan"))
            else:
                claims = self._verifier._extract_claims_heuristic(segments) if self._verifier else []

            for c in claims:
                c.video_id = v["video_id"]
                if not c.speaker and v.get("title"):
                    c.speaker = self._guess_speaker(v.get("title", ""))
                all_claims.append({
                    "claim_id": c.claim_id,
                    "text": c.text,
                    "speaker": c.speaker or "unknown",
                    "category": c.category,
                    "video_id": v["video_id"],
                    "timestamp_seconds": c.timestamp_seconds,
                    "title": v.get("title", ""),
                    "date": v.get("upload_date", v.get("date", "")),
                })

        if not all_claims:
            return {"contradictions": [], "videos_analyzed": len(videos), "total_claims": 0}

        # Apply filters
        if speaker_filter:
            sf = speaker_filter.lower()
            all_claims = [c for c in all_claims if sf in c["speaker"].lower()]
        if topic_filter:
            tf = topic_filter.lower()
            all_claims = [c for c in all_claims if tf in c["text"].lower()]

        # Use Opus to find contradictions
        contradictions = await self._opus_find_contradictions(all_claims, external_data)

        elapsed = time.time() - start
        return {
            "videos_analyzed": len(videos),
            "total_claims": len(all_claims),
            "contradictions": [c.to_dict() for c in contradictions],
            "contradiction_count": len(contradictions),
            "self_contradictions": len([c for c in contradictions if c.contradiction_type == "self_contradiction"]),
            "cross_speaker": len([c for c in contradictions if c.contradiction_type == "cross_speaker"]),
            "data_contradictions": len([c for c in contradictions if c.contradiction_type == "data_contradiction"]),
            "elapsed_seconds": round(elapsed, 2),
            "external_data_provided": external_data is not None,
        }

    async def _opus_find_contradictions(
        self, claims: List[Dict], external_data: Optional[Dict] = None
    ) -> List[ContradictionPair]:
        """Use Opus 4.6 to identify contradictions among claims."""

        # Build claims text block
        claims_text = "\n".join([
            f"[{i}] Speaker: {c['speaker']} | Date: {c.get('date','')} | "
            f"Video: {c['video_id'][:8]} | Category: {c['category']}\n"
            f"    \"{c['text']}\""
            for i, c in enumerate(claims)
        ])

        data_block = ""
        if external_data:
            data_block = f"""

EXTERNAL DATA PROVIDED:
```json
{json.dumps(external_data, indent=2)[:3000]}
```

Also check claims against this external data. If a speaker claims something
about exchange flows, prices, volumes, etc. and the data contradicts them,
flag it as "data_contradiction" with HIGH severity.
"""

        system = """You are an investigative analyst. Given a list of claims from video transcripts,
identify ALL contradictions: self-contradictions (same speaker, conflicting claims),
cross-speaker contradictions, and data contradictions (claims contradicted by provided data).

Return ONLY valid JSON array:
[
  {
    "claim_a_index": 0,
    "claim_b_index": 3,
    "type": "self_contradiction|cross_speaker|data_contradiction",
    "severity": "high|medium|low",
    "explanation": "Brief explanation of the contradiction"
  }
]

Rules:
- Only flag REAL contradictions, not differences of opinion on future events
- Self-contradiction: same speaker says opposite things at different times
- Data contradiction: speaker states a fact that the external data disproves
- High severity: direct numerical or factual conflict
- Medium: directional conflict (bullish vs bearish on same metric)
- Low: subtle inconsistency
- Return empty array [] if no contradictions found
"""

        user = f"CLAIMS:\n{claims_text}{data_block}"

        raw = await self._call_opus(system, user, max_tokens=3000)

        # Parse response
        contradictions = []
        try:
            # Extract JSON from response
            json_str = raw
            if "```json" in raw:
                json_str = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                json_str = raw.split("```")[1].split("```")[0]
            items = json.loads(json_str.strip())

            for item in items:
                a_idx = item.get("claim_a_index", 0)
                b_idx = item.get("claim_b_index", 0)
                if a_idx < len(claims) and b_idx < len(claims):
                    data_ev = None
                    if item.get("type") == "data_contradiction" and external_data:
                        data_ev = {"source": "user_provided", "data": external_data.get("summary_metrics", {})}
                    contradictions.append(ContradictionPair(
                        claim_a=claims[a_idx],
                        claim_b=claims[b_idx] if b_idx != a_idx else {"text": "External data", "speaker": "data"},
                        contradiction_type=item.get("type", "cross_speaker"),
                        severity=item.get("severity", "medium"),
                        explanation=item.get("explanation", ""),
                        data_evidence=data_ev,
                    ))
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"[IntelligenceLayer] Opus contradiction parse failed: {e}")

        return contradictions

    def _guess_speaker(self, title: str) -> str:
        """Guess speaker name from video title."""
        known = ["Tom Lee", "Raoul Pal", "Michael Saylor", "Cathie Wood",
                 "Peter Schiff", "Jim Cramer", "Anthony Pompliano"]
        for name in known:
            if name.lower() in title.lower():
                return name
        return ""

    # ═══════════════════════════════════════════════════════════════════════
    # 2. SPEAKER SCORECARD
    # ═══════════════════════════════════════════════════════════════════════

    async def speaker_scorecard(
        self,
        speaker: str = "",
        max_videos: int = 50,
        external_data: Optional[Dict] = None,
    ) -> Dict:
        """
        Build reliability scorecards for speakers across all indexed videos.

        If speaker is specified, returns detailed scorecard for that person.
        Otherwise returns scorecards for all speakers found.
        """
        await self.initialize()
        start = time.time()

        videos = self._get_indexed_videos()[:max_videos]
        if not videos:
            return {"scorecards": [], "message": "No indexed videos found"}

        # Extract and verify claims from all videos
        speaker_data: Dict[str, SpeakerScore] = defaultdict(lambda: SpeakerScore(speaker=""))
        all_claims_for_contradiction = []

        for v in videos:
            segments = self._load_segments(v["transcript_path"])
            if not segments:
                continue

            if self._verifier and self._verifier._opus:
                claims = await self._verifier.extract_claims(segments, v.get("opus_plan"))
            else:
                claims = self._verifier._extract_claims_heuristic(segments) if self._verifier else []

            for c in claims:
                c.video_id = v["video_id"]
                if not c.speaker:
                    c.speaker = self._guess_speaker(v.get("title", ""))
                if not c.speaker:
                    c.speaker = "unknown"
                if speaker and speaker.lower() not in c.speaker.lower():
                    continue

                sp = c.speaker
                score = speaker_data[sp]
                score.speaker = sp
                score.total_claims += 1
                score.claims_by_category[c.category] = score.claims_by_category.get(c.category, 0) + 1

                # Verify claim if verifier available
                if self._verifier:
                    result = await self._verifier.verify_claim(c)
                    verdict = result.verdict.value if hasattr(result.verdict, 'value') else str(result.verdict)

                    if verdict == "confirmed":
                        score.verified += 1
                    elif verdict == "contradicted":
                        score.contradicted += 1
                        score.worst_misses.append({
                            "claim": c.text[:200],
                            "verdict": verdict,
                            "video_id": c.video_id,
                            "reasoning": result.reasoning[:200] if hasattr(result, 'reasoning') else "",
                        })
                    elif verdict == "stale":
                        score.stale += 1
                    else:
                        score.unverifiable += 1

                all_claims_for_contradiction.append({
                    "text": c.text, "speaker": sp, "video_id": v["video_id"],
                    "category": c.category, "date": v.get("date", ""),
                })

            # Track videos per speaker
            for sp in speaker_data:
                score = speaker_data[sp]
                if v["video_id"] not in [m.get("video_id") for m in score.worst_misses]:
                    score.videos_analyzed += 1

        # Check for data contradictions if external data provided
        if external_data and all_claims_for_contradiction:
            data_contradictions = await self._check_data_contradictions(
                all_claims_for_contradiction, external_data
            )
            for dc in data_contradictions:
                sp = dc.get("speaker", "unknown")
                if sp in speaker_data:
                    speaker_data[sp].data_contradictions += 1

        # Find self-contradictions
        for sp, score in speaker_data.items():
            sp_claims = [c for c in all_claims_for_contradiction if c["speaker"] == sp]
            self_contradictions = await self._find_self_contradictions(sp_claims)
            score.self_contradictions = len(self_contradictions)

        elapsed = time.time() - start
        scorecards = sorted(speaker_data.values(), key=lambda s: s.total_claims, reverse=True)

        return {
            "scorecards": [s.to_dict() for s in scorecards],
            "total_speakers": len(scorecards),
            "videos_analyzed": len(videos),
            "elapsed_seconds": round(elapsed, 2),
        }

    async def _find_self_contradictions(self, claims: List[Dict]) -> List[Dict]:
        """Find contradictions within a single speaker's claims."""
        if len(claims) < 2 or not self._api_key:
            return []

        claims_text = "\n".join([
            f"[{i}] Date: {c.get('date','')} | \"{c['text']}\""
            for i, c in enumerate(claims[:30])  # Limit for context window
        ])

        system = """Find self-contradictions in these claims from the SAME speaker.
Return JSON array: [{"claim_a_index": 0, "claim_b_index": 3, "explanation": "..."}]
Only flag REAL contradictions. Return [] if none."""

        raw = await self._call_opus(system, claims_text, max_tokens=2000)
        try:
            json_str = raw
            if "```json" in raw:
                json_str = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                json_str = raw.split("```")[1].split("```")[0]
            return json.loads(json_str.strip())
        except Exception:
            return []

    async def _check_data_contradictions(
        self, claims: List[Dict], external_data: Dict
    ) -> List[Dict]:
        """Check claims against external data using Opus."""
        if not self._api_key:
            return []

        # Filter to claims that might reference the data
        data_keywords = []
        if "asset" in external_data:
            data_keywords.append(external_data["asset"].lower())
        if "exchange" in external_data:
            data_keywords.append(external_data["exchange"].lower())
        # Add common terms
        data_keywords.extend(["price", "volume", "flow", "exchange", "outflow", "inflow", "reserve"])

        relevant_claims = []
        for c in claims:
            text_lower = c["text"].lower()
            if any(kw in text_lower for kw in data_keywords):
                relevant_claims.append(c)

        if not relevant_claims:
            return []

        claims_text = "\n".join([
            f"[{i}] Speaker: {c['speaker']} | Date: {c.get('date','')} | \"{c['text']}\""
            for i, c in enumerate(relevant_claims[:20])
        ])

        data_text = json.dumps(external_data, indent=2)[:3000]

        system = """Compare these analyst claims against the provided market data.
Flag any claim where the speaker states a FACT that is contradicted by the data.
Do NOT flag predictions (future events) — only flag factual assertions that are provably wrong.

Return JSON array:
[{"claim_index": 0, "speaker": "name", "data_field": "field_name",
  "claimed_value": "what they said", "actual_value": "what data shows",
  "explanation": "..."}]

Return [] if no data contradictions found."""

        raw = await self._call_opus(system, f"CLAIMS:\n{claims_text}\n\nDATA:\n{data_text}", max_tokens=2000)
        try:
            json_str = raw
            if "```json" in raw:
                json_str = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                json_str = raw.split("```")[1].split("```")[0]
            return json.loads(json_str.strip())
        except Exception:
            return []

    # ═══════════════════════════════════════════════════════════════════════
    # 3. EXTERNAL DATA CROSS-REFERENCE (for synthesis agent)
    # ═══════════════════════════════════════════════════════════════════════

    async def cross_reference_external(
        self,
        question: str,
        external_data: Dict,
        speaker_filter: str = "",
        max_videos: int = 10,
    ) -> Dict:
        """
        Answer a question by cross-referencing indexed videos against external data.

        This is NOT web search — it takes YOUR data (ETH prices, exchange flows)
        and compares it against what analysts said in their videos.
        """
        await self.initialize()
        start = time.time()

        videos = self._get_indexed_videos()[:max_videos]
        if not videos:
            return {"answer": "No indexed videos found", "sources": []}

        # Extract relevant claims from all videos
        relevant_claims = []
        for v in videos:
            segments = self._load_segments(v["transcript_path"])
            if not segments:
                continue

            if self._verifier and self._verifier._opus:
                claims = await self._verifier.extract_claims(segments, v.get("opus_plan"))
            else:
                claims = self._verifier._extract_claims_heuristic(segments) if self._verifier else []

            for c in claims:
                c.video_id = v["video_id"]
                if not c.speaker:
                    c.speaker = self._guess_speaker(v.get("title", ""))
                if speaker_filter and speaker_filter.lower() not in (c.speaker or "").lower():
                    continue
                relevant_claims.append({
                    "text": c.text, "speaker": c.speaker or "unknown",
                    "video_id": v["video_id"], "category": c.category,
                    "timestamp_seconds": c.timestamp_seconds,
                    "date": v.get("date", ""),
                })

        if not relevant_claims:
            return {"answer": "No relevant claims found in indexed videos", "sources": []}

        # Use Opus to synthesize answer
        claims_text = "\n".join([
            f"- [{c['speaker']}] ({c.get('date','')}, {c['video_id'][:8]}): \"{c['text']}\""
            for c in relevant_claims[:30]
        ])

        data_text = json.dumps(external_data, indent=2)[:4000]

        system = """You are a financial analyst assistant. Answer the user's question by
cross-referencing analyst claims from video transcripts with the provided market data.

For each claim:
1. State who said what and when
2. Compare against the actual data
3. Note if the claim is VERIFIED, CONTRADICTED, or STALE

Cite specific data points. Be precise about numbers.
End with a brief summary of which analysts were most/least accurate."""

        user = f"""QUESTION: {question}

ANALYST CLAIMS FROM VIDEOS:
{claims_text}

EXTERNAL MARKET DATA:
{data_text}"""

        answer = await self._call_opus(system, user, max_tokens=4000)

        elapsed = time.time() - start
        return {
            "question": question,
            "answer": answer,
            "claims_analyzed": len(relevant_claims),
            "videos_referenced": len(set(c["video_id"] for c in relevant_claims)),
            "speakers": list(set(c["speaker"] for c in relevant_claims)),
            "external_data_summary": external_data.get("summary_metrics", {}),
            "elapsed_seconds": round(elapsed, 2),
            "sources": relevant_claims[:20],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Auto-Discovery: register_routes(app)
# ═══════════════════════════════════════════════════════════════════════════

_intel_instance: Optional[IntelligenceLayer] = None

def _get_intel() -> IntelligenceLayer:
    global _intel_instance
    if _intel_instance is None:
        _intel_instance = IntelligenceLayer()
    return _intel_instance


def register_routes(app):
    """Auto-discovered by main.py — registers intelligence layer endpoints."""
    from fastapi import Query as FQuery
    from pydantic import BaseModel
    from typing import Optional as Opt, Dict as DDict

    # ── Contradiction Detector ──────────────────────────────────────

    class ContradictionRequest(BaseModel):
        speaker_filter: str = ""
        topic_filter: str = ""
        since_date: str = ""
        max_videos: int = 20
        external_data: Opt[DDict] = None

    @app.post("/api/v1/contradictions/find", tags=["intelligence"])
    async def find_contradictions(req: ContradictionRequest):
        """
        Scan all indexed videos for contradictions between claims.

        Optionally provide external_data (e.g. ETH exchange prices)
        to find claims that conflict with real data.
        """
        intel = _get_intel()
        return await intel.find_contradictions(
            speaker_filter=req.speaker_filter,
            topic_filter=req.topic_filter,
            since_date=req.since_date,
            max_videos=req.max_videos,
            external_data=req.external_data,
        )

    @app.get("/api/v1/contradictions/stats", tags=["intelligence"])
    async def contradiction_stats():
        """Intelligence layer stats."""
        intel = _get_intel()
        videos = intel._get_indexed_videos()
        return {
            "agent": "intelligence-layer",
            "status": "active",
            "indexed_videos": len(videos),
            "opus_available": bool(intel._api_key),
        }

    # ── Speaker Scorecard ───────────────────────────────────────────

    class ScorecardRequest(BaseModel):
        speaker: str = ""
        max_videos: int = 50
        external_data: Opt[DDict] = None

    @app.post("/api/v1/speakers/scorecard", tags=["intelligence"])
    async def get_scorecard(req: ScorecardRequest):
        """
        Build reliability scorecards for all speakers (or a specific speaker).

        Returns accuracy rate, reliability grade, pattern analysis,
        and worst misses for each speaker.
        """
        intel = _get_intel()
        return await intel.speaker_scorecard(
            speaker=req.speaker,
            max_videos=req.max_videos,
            external_data=req.external_data,
        )

    @app.get("/api/v1/speakers/{speaker}/score", tags=["intelligence"])
    async def get_speaker_score(speaker: str, max_videos: int = 50):
        """Quick scorecard for a specific speaker."""
        intel = _get_intel()
        result = await intel.speaker_scorecard(speaker=speaker, max_videos=max_videos)
        cards = result.get("scorecards", [])
        if cards:
            return cards[0]
        return {"speaker": speaker, "message": "No claims found for this speaker"}

    # ── External Data Cross-Reference ───────────────────────────────

    class CrossRefRequest(BaseModel):
        question: str
        external_data: DDict
        speaker_filter: str = ""
        max_videos: int = 10

    @app.post("/api/v1/intelligence/cross-reference", tags=["intelligence"])
    async def cross_reference(req: CrossRefRequest):
        """
        Cross-reference analyst claims against YOUR external data.

        Send ETH prices, exchange flows, volume data, etc. and CortexOS
        will compare what analysts said in their videos against the real numbers.

        This is NOT web search — this uses the data YOU provide.
        """
        intel = _get_intel()
        return await intel.cross_reference_external(
            question=req.question,
            external_data=req.external_data,
            speaker_filter=req.speaker_filter,
            max_videos=req.max_videos,
        )

    logger.info(
        "[IntelligenceLayer] Registered routes: "
        "/api/v1/contradictions/find, /contradictions/stats, "
        "/api/v1/speakers/scorecard, /speakers/{speaker}/score, "
        "/api/v1/intelligence/cross-reference"
    )
