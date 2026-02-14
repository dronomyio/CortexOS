"""
CortexVid Autonomous Agent Loop
=================================

Runs continuously and autonomously:
  1. WATCH  — Monitor YouTube channels/playlists for new videos
  2. INGEST — Download + Opus-planned processing
  3. ANALYZE — Cross-reference claims across all videos
  4. ALERT  — Flag contradictions, stale predictions, risks
  5. VERIFY — Periodically re-check claims against live data

Usage:
    python agent_loop.py --channels "UCxxxxx" --interval 300

    Or via API:
    POST /api/v1/agent/start   → Start autonomous monitoring
    POST /api/v1/agent/stop    → Stop monitoring
    GET  /api/v1/agent/status  → Current agent state + findings
    GET  /api/v1/agent/alerts  → All generated alerts
"""
import asyncio
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# ── Agent State ──────────────────────────────────────────────────────────────

@dataclass
class AgentAlert:
    alert_type: str          # "contradiction", "stale_prediction", "high_risk", "new_claim"
    severity: str            # "info", "warning", "critical"
    title: str
    detail: str
    video_ids: List[str]
    timestamps: List[float]
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class AgentState:
    running: bool = False
    mode: str = "idle"       # "idle", "watching", "ingesting", "analyzing", "verifying"
    channels: List[str] = field(default_factory=list)
    check_interval_seconds: int = 300
    videos_ingested: int = 0
    claims_tracked: int = 0
    alerts_generated: int = 0
    contradictions_found: int = 0
    last_check: Optional[str] = None
    last_ingest: Optional[str] = None
    started_at: Optional[str] = None
    cycle_count: int = 0


class CortexVidAgent:
    """
    Autonomous agent that monitors video sources, ingests new content,
    cross-references claims, and generates alerts.

    This is what makes CortexVid a TRUE agent — not just a tool you call,
    but a system that acts on its own.
    """

    def __init__(
        self,
        api_base: str = "http://localhost:8093",
        data_dir: str = "/data",
        channels: Optional[List[str]] = None,
        playlists: Optional[List[str]] = None,
        check_interval: int = 300,
    ):
        self.api_base = api_base
        self.data_dir = Path(data_dir)
        self.clips_dir = self.data_dir / "agent_clips"
        self.clips_dir.mkdir(parents=True, exist_ok=True)

        self.state = AgentState(
            channels=channels or [],
            check_interval_seconds=check_interval,
        )
        self.alerts: List[AgentAlert] = []
        self.claims_db: List[Dict] = []  # All tracked claims across videos
        self.known_video_ids: set = set()  # Already ingested YouTube IDs

        self._task: Optional[asyncio.Task] = None
        self._planner = None
        self._stop_event = asyncio.Event()

    # ── Agent Lifecycle ──────────────────────────────────────────────────

    async def start(self):
        """Start the autonomous agent loop."""
        if self.state.running:
            return {"status": "already running"}

        self.state.running = True
        self.state.started_at = datetime.now(timezone.utc).isoformat()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._agent_loop())

        logger.info(f"[Agent] Started — monitoring {len(self.state.channels)} channels, "
                    f"interval={self.state.check_interval_seconds}s")
        return {"status": "started", "state": asdict(self.state)}

    async def stop(self):
        """Stop the autonomous agent loop."""
        self.state.running = False
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            self._task = None

        logger.info("[Agent] Stopped")
        return {"status": "stopped", "state": asdict(self.state)}

    def get_status(self) -> Dict:
        return asdict(self.state)

    def get_alerts(self, severity: Optional[str] = None, limit: int = 50) -> List[Dict]:
        alerts = self.alerts
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        return [asdict(a) for a in alerts[-limit:]]

    # ── The Core Agent Loop ──────────────────────────────────────────────

    async def _agent_loop(self):
        """
        The autonomous loop:
          watch → ingest → analyze → verify → alert → sleep → repeat
        """
        logger.info("[Agent] Entering autonomous loop")

        while self.state.running:
            try:
                self.state.cycle_count += 1
                cycle_start = time.time()

                # ── Phase 1: WATCH — Check for new videos ────────────
                self.state.mode = "watching"
                new_videos = await self._check_for_new_videos()

                if new_videos:
                    logger.info(f"[Agent] Found {len(new_videos)} new videos")

                    # ── Phase 2: INGEST — Download + process ─────────
                    self.state.mode = "ingesting"
                    for video_info in new_videos:
                        await self._ingest_video(video_info)

                # ── Phase 3: ANALYZE — Cross-reference claims ────────
                self.state.mode = "analyzing"
                await self._cross_reference_claims()

                # ── Phase 4: VERIFY — Re-check old claims ────────────
                self.state.mode = "verifying"
                await self._verify_stale_claims()

                self.state.last_check = datetime.now(timezone.utc).isoformat()
                cycle_time = int(time.time() - cycle_start)
                logger.info(f"[Agent] Cycle {self.state.cycle_count} complete ({cycle_time}s). "
                           f"Claims: {self.state.claims_tracked}, "
                           f"Alerts: {self.state.alerts_generated}")

                # ── Sleep until next cycle ────────────────────────────
                self.state.mode = "idle"
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.state.check_interval_seconds
                    )
                    break  # Stop event received
                except asyncio.TimeoutError:
                    continue  # Normal timeout, continue loop

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Agent] Loop error: {e}")
                self._add_alert("error", "warning", f"Agent error: {str(e)[:100]}",
                               str(e), [], [])
                await asyncio.sleep(60)  # Back off on error

        self.state.running = False
        self.state.mode = "idle"

    # ── Phase 1: Watch for New Videos ────────────────────────────────────

    async def _check_for_new_videos(self) -> List[Dict]:
        """Check YouTube channels/playlists for new videos."""
        new_videos = []

        for channel in self.state.channels:
            try:
                # Use yt-dlp to check for recent uploads
                cmd = [
                    "yt-dlp", "--flat-playlist", "--dump-json",
                    "--playlist-end", "5",  # Check last 5 videos
                    f"https://www.youtube.com/channel/{channel}/videos"
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, _ = await proc.communicate()

                for line in out.decode().strip().split("\n"):
                    if not line:
                        continue
                    try:
                        info = json.loads(line)
                        yt_id = info.get("id", "")
                        if yt_id and yt_id not in self.known_video_ids:
                            new_videos.append({
                                "youtube_id": yt_id,
                                "title": info.get("title", "Unknown"),
                                "url": info.get("url", f"https://youtube.com/watch?v={yt_id}"),
                                "duration": info.get("duration", 0),
                                "channel": channel,
                            })
                    except json.JSONDecodeError:
                        continue

            except Exception as e:
                logger.warning(f"[Agent] Channel check failed for {channel}: {e}")

        return new_videos

    # ── Phase 2: Ingest New Videos ───────────────────────────────────────

    async def _ingest_video(self, video_info: Dict):
        """Download and ingest a new video."""
        yt_id = video_info["youtube_id"]
        title = video_info["title"]

        try:
            logger.info(f"[Agent] Downloading: {title} ({yt_id})")

            # Download with yt-dlp
            clip_path = self.clips_dir / f"{yt_id}.mp4"
            if not clip_path.exists():
                cmd = [
                    "yt-dlp",
                    "-f", "worst[ext=mp4]",  # Smallest quality — faster
                    "-o", str(clip_path),
                    video_info["url"],
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()

                if not clip_path.exists():
                    logger.warning(f"[Agent] Download failed: {yt_id}")
                    return

            # Upload to CortexVid API
            logger.info(f"[Agent] Ingesting: {title}")
            import aiohttp
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field("file",
                              open(clip_path, "rb"),
                              filename=f"{yt_id}.mp4",
                              content_type="video/mp4")

                async with session.post(
                    f"{self.api_base}/api/v1/videos/upload",
                    data=data
                ) as resp:
                    result = await resp.json()
                    job_id = result.get("job_id", "")
                    video_id = result.get("video_id", "")

            # Wait for processing to complete
            logger.info(f"[Agent] Waiting for processing: {job_id[:8]}...")
            for _ in range(120):  # Max 10 min wait
                await asyncio.sleep(5)
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{self.api_base}/api/v1/jobs/{job_id}"
                    ) as resp:
                        status = await resp.json()
                        if status.get("status") == "ready":
                            break
                        if status.get("status") == "failed":
                            logger.warning(f"[Agent] Processing failed: {title}")
                            return

            self.known_video_ids.add(yt_id)
            self.state.videos_ingested += 1
            self.state.last_ingest = datetime.now(timezone.utc).isoformat()

            logger.info(f"[Agent] ✓ Ingested: {title} → video_id={video_id}")

            # Check for Opus plan findings
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_base}/api/v1/jobs/{job_id}/plan"
                ) as resp:
                    plan = await resp.json()
                    adaptive = plan.get("adaptive_analysis", [])
                    for finding in adaptive:
                        if finding.get("risk_score", 0) > 0.5:
                            self._add_alert(
                                "high_risk", "warning",
                                f"High risk content in: {title}",
                                f"Risk score {finding['risk_score']}: "
                                f"{finding.get('discrepancies', ['Unknown'])[0]}",
                                [video_id], [finding.get("segment", 0) * 60],
                            )

        except Exception as e:
            logger.error(f"[Agent] Ingest failed for {yt_id}: {e}")

    # ── Phase 3: Cross-Reference Claims ──────────────────────────────────

    async def _cross_reference_claims(self):
        """
        The autonomous brain: find contradictions across all videos.

        Asks Opus: "Looking at all claims from all videos, are there any
        contradictions, revised predictions, or inconsistencies?"
        """
        if self.state.videos_ingested < 2:
            return  # Need at least 2 videos to cross-reference

        try:
            import aiohttp
            # Ask Opus to find contradictions
            question = (
                "Compare all financial predictions and claims across these videos. "
                "Are there any contradictions where the speaker changed their position? "
                "List specific numbers and dates."
            )

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_base}/api/v1/synthesize",
                    json={"question": question, "search_mode": "hybrid", "top_k": 15},
                ) as resp:
                    result = await resp.json()

            synthesis = result.get("synthesis", "")
            strategy = result.get("strategy", "")

            # If Opus chose investigative strategy, there are likely contradictions
            if strategy == "investigative" or "contradict" in synthesis.lower():
                self.state.contradictions_found += 1
                self._add_alert(
                    "contradiction", "critical",
                    "Contradiction detected across videos",
                    synthesis[:500],
                    [], [],
                )

            # Extract and track claims
            confidence = result.get("confidence", "medium")
            if confidence == "low":
                self._add_alert(
                    "low_confidence", "info",
                    "Low confidence in cross-reference analysis",
                    "Consider ingesting more videos for better coverage",
                    [], [],
                )

        except Exception as e:
            logger.warning(f"[Agent] Cross-reference failed: {e}")

    # ── Phase 4: Verify Stale Claims ─────────────────────────────────────

    async def _verify_stale_claims(self):
        """
        Re-verify old claims against current data.

        Example: Tom Lee said "BTC $200K by end 2026" in Jan 2025.
        Agent checks: Is that still his position? What's BTC now?
        """
        if not self.claims_db:
            return

        try:
            import aiohttp

            # Check claims older than 7 days
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            stale = [c for c in self.claims_db if c.get("verified_at", "") < cutoff]

            for claim in stale[:5]:  # Max 5 per cycle
                question = (
                    f"Verify this claim: '{claim.get('text', '')}'. "
                    f"Is this still accurate? What's the current data?"
                )

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.api_base}/api/v1/synthesize",
                        json={"question": question, "search_mode": "hybrid"},
                    ) as resp:
                        result = await resp.json()

                synthesis = result.get("synthesis", "")
                if any(word in synthesis.lower() for word in
                       ["outdated", "revised", "incorrect", "no longer", "changed"]):
                    self._add_alert(
                        "stale_prediction", "warning",
                        f"Stale prediction: {claim.get('text', '')[:100]}",
                        synthesis[:300],
                        claim.get("video_ids", []),
                        claim.get("timestamps", []),
                    )

                claim["verified_at"] = datetime.now(timezone.utc).isoformat()

        except Exception as e:
            logger.warning(f"[Agent] Verification failed: {e}")

    # ── Alert Management ─────────────────────────────────────────────────

    def _add_alert(self, alert_type: str, severity: str, title: str,
                   detail: str, video_ids: List[str], timestamps: List[float]):
        alert = AgentAlert(
            alert_type=alert_type,
            severity=severity,
            title=title,
            detail=detail,
            video_ids=video_ids,
            timestamps=timestamps,
        )
        self.alerts.append(alert)
        self.state.alerts_generated += 1
        logger.info(f"[Agent] ALERT [{severity}] {title}")


# ── Global agent instance ────────────────────────────────────────────────────
_agent: Optional[CortexVidAgent] = None


def get_agent() -> CortexVidAgent:
    global _agent
    if _agent is None:
        channels = os.getenv("AGENT_CHANNELS", "").split(",")
        channels = [c.strip() for c in channels if c.strip()]
        interval = int(os.getenv("AGENT_CHECK_INTERVAL", "300"))

        _agent = CortexVidAgent(
            channels=channels,
            check_interval=interval,
        )
    return _agent
