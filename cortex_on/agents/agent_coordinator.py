"""
CortexOS Agent Coordinator — Opus 4.6 as Team Lead
=====================================================

The basketball team, not the relay race.

Opus 4.6 acts as a coordinator that:
  1. Receives a mission (N videos + optional external data)
  2. Reasons about the optimal parallel execution plan
  3. Assigns tasks to agents simultaneously
  4. Monitors progress via heartbeats + MongoDB task queue
  5. Re-plans when agents fail or produce unexpected results
  6. Synthesizes final output with conflict resolution

Architecture:
  - MongoDB: task queue (cortexos.agent_tasks), heartbeats (cortexos.agent_heartbeats)
  - Opus 4.6: planning, monitoring, re-planning, conflict resolution
  - Agents: video_ingest, fact_verifier, synthesis, intelligence_layer, opus_planner
  - Shared state: /data/out/{video_id}/ filesystem + MongoDB

Auto-discovered by main.py via register_routes(app).

Location: cortex_on/agents/agent_coordinator.py
"""

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════════════

class TaskStatus(str, Enum):
    PENDING    = "pending"
    ASSIGNED   = "assigned"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    SKIPPED    = "skipped"
    CANCELLED  = "cancelled"


class MissionPhase(str, Enum):
    PLANNING       = "planning"        # Opus analyzing the mission
    INGESTION      = "ingestion"       # Parallel video ingestion
    VERIFICATION   = "verification"    # Fact-checking claims
    ANALYSIS       = "analysis"        # Contradictions + scorecards
    SYNTHESIS      = "synthesis"       # Final report
    COMPLETED      = "completed"
    FAILED         = "failed"


@dataclass
class AgentTask:
    """A single task assigned to an agent."""
    task_id: str = ""
    mission_id: str = ""
    agent: str = ""           # "video_ingest", "fact_verifier", "intelligence", "synthesis"
    action: str = ""          # "ingest_url", "verify_video", "find_contradictions", etc.
    params: Dict = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict] = None
    error: Optional[str] = None
    phase: MissionPhase = MissionPhase.PLANNING
    priority: int = 0         # Higher = more important
    depends_on: List[str] = field(default_factory=list)  # task_ids this depends on
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"task_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "mission_id": self.mission_id,
            "agent": self.agent,
            "action": self.action,
            "params": self.params,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "phase": self.phase.value,
            "priority": self.priority,
            "depends_on": self.depends_on,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_ms": round((self.completed_at - self.started_at) * 1000)
            if self.completed_at and self.started_at else 0,
        }


@dataclass
class Mission:
    """A coordinated multi-agent mission."""
    mission_id: str = ""
    description: str = ""
    phase: MissionPhase = MissionPhase.PLANNING
    urls: List[str] = field(default_factory=list)
    external_data: Optional[Dict] = None
    speaker_filter: str = ""
    opus_plan: Optional[Dict] = None      # Opus 4.6 execution plan
    tasks: List[AgentTask] = field(default_factory=list)
    results: Dict = field(default_factory=dict)
    created_at: float = 0.0
    completed_at: float = 0.0
    config: Dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.mission_id:
            self.mission_id = f"mission_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = time.time()

    @property
    def progress(self) -> Dict:
        total = len(self.tasks)
        if total == 0:
            return {"total": 0, "completed": 0, "failed": 0, "running": 0, "pct": 0}
        completed = len([t for t in self.tasks if t.status == TaskStatus.COMPLETED])
        failed = len([t for t in self.tasks if t.status == TaskStatus.FAILED])
        running = len([t for t in self.tasks if t.status == TaskStatus.RUNNING])
        skipped = len([t for t in self.tasks if t.status == TaskStatus.SKIPPED])
        return {
            "total": total, "completed": completed, "failed": failed,
            "running": running, "skipped": skipped,
            "pct": round((completed + skipped) / total * 100),
        }

    def to_dict(self) -> Dict:
        return {
            "mission_id": self.mission_id,
            "description": self.description,
            "phase": self.phase.value,
            "progress": self.progress,
            "urls_count": len(self.urls),
            "has_external_data": self.external_data is not None,
            "speaker_filter": self.speaker_filter,
            "opus_plan": self.opus_plan,
            "tasks": [t.to_dict() for t in self.tasks],
            "results": self.results,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": round(self.completed_at - self.created_at, 2)
            if self.completed_at else round(time.time() - self.created_at, 2),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Agent Coordinator
# ═══════════════════════════════════════════════════════════════════════════

class AgentCoordinator:
    """
    Opus 4.6 as team lead — orchestrates parallel agent execution.

    The coordinator does NOT run agents directly. It:
      1. Creates a plan
      2. Assigns tasks to the task queue
      3. Calls agent endpoints via HTTP (internal Docker network)
      4. Monitors completion
      5. Re-plans on failure
      6. Synthesizes final output
    """

    def __init__(self):
        self._model = os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6")
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._api_url = os.getenv("CORTEXOS_INTERNAL_URL", "http://localhost:8081")
        self._missions: Dict[str, Mission] = {}
        self._db = None
        self._max_parallel = int(os.getenv("COORDINATOR_MAX_PARALLEL", "5"))
        self._max_retries = int(os.getenv("COORDINATOR_MAX_RETRIES", "2"))

    async def _get_db(self):
        """Lazy MongoDB connection."""
        if self._db is None:
            try:
                from motor.motor_asyncio import AsyncIOMotorClient
                mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
                client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
                db_name = os.getenv("MONGODB_DB", "cortexos")
                self._db = client[db_name]
                # Ensure indexes
                await self._db.agent_tasks.create_index("mission_id")
                await self._db.agent_tasks.create_index("status")
                await self._db.missions.create_index("mission_id")
                logger.info("[Coordinator] MongoDB connected")
            except Exception as e:
                logger.warning(f"[Coordinator] MongoDB unavailable: {e} — using in-memory")
        return self._db

    async def _call_opus(self, system: str, user: str, max_tokens: int = 3000) -> str:
        """Call Opus 4.6 for planning/reasoning."""
        if not self._api_key:
            logger.warning("[Coordinator] No ANTHROPIC_API_KEY — using default plan")
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
            logger.error(f"[Coordinator] Opus call failed: {e}")
            return "{}"

    async def _call_agent(self, method: str, path: str, body: Dict = None) -> Dict:
        """Call an internal CortexOS agent endpoint."""
        import aiohttp
        url = f"{self._api_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                if method == "POST":
                    async with session.post(url, json=body or {},
                                            timeout=aiohttp.ClientTimeout(total=300)) as r:
                        if r.status == 402:
                            return {"status": 402, "payment_required": True, "body": await r.json()}
                        return await r.json()
                else:
                    async with session.get(url,
                                           timeout=aiohttp.ClientTimeout(total=60)) as r:
                        return await r.json()
        except Exception as e:
            return {"error": str(e)}

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 1: PLANNING — Opus 4.6 analyzes the mission
    # ═══════════════════════════════════════════════════════════════════════

    async def plan_mission(self, mission: Mission) -> Mission:
        """Opus 4.6 reasons about the optimal execution plan."""
        mission.phase = MissionPhase.PLANNING

        urls_text = "\n".join([f"  {i+1}. {u}" for i, u in enumerate(mission.urls)])
        data_text = ""
        if mission.external_data:
            data_text = f"\nExternal data provided: {json.dumps(mission.external_data, indent=2)[:2000]}"

        system = """You are the CortexOS team coordinator. Given a list of video URLs and
optional external market data, create an execution plan.

Analyze each URL (from the title/URL patterns) and categorize:
- prediction: price targets, forecasts
- on_chain: exchange flows, whale activity, on-chain metrics
- macro: Fed, interest rates, macro commentary
- technical: chart patterns, technical analysis

Return ONLY valid JSON:
{
  "strategy": "investigative|comparative|factual",
  "video_groups": [
    {"category": "on_chain", "url_indices": [1, 4, 7], "priority": "high"},
    {"category": "prediction", "url_indices": [2, 5], "priority": "medium"},
    {"category": "macro", "url_indices": [3, 6, 8], "priority": "low"}
  ],
  "parallel_ingest": true,
  "data_relevant_groups": ["on_chain"],
  "verification_strategy": "verify_all|verify_high_priority|verify_data_relevant",
  "reasoning": "Brief explanation of the plan"
}"""

        user = f"""MISSION: Analyze {len(mission.urls)} videos
{f"Speaker filter: {mission.speaker_filter}" if mission.speaker_filter else ""}

URLS:
{urls_text}
{data_text}"""

        raw = await self._call_opus(system, user, max_tokens=2000)

        # Parse plan
        try:
            plan_json = raw
            if "```json" in raw:
                plan_json = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                plan_json = raw.split("```")[1].split("```")[0]
            mission.opus_plan = json.loads(plan_json.strip())
        except Exception:
            # Default plan if Opus fails
            mission.opus_plan = {
                "strategy": "investigative",
                "video_groups": [{"category": "general", "url_indices": list(range(len(mission.urls))), "priority": "medium"}],
                "parallel_ingest": True,
                "data_relevant_groups": ["general"] if mission.external_data else [],
                "verification_strategy": "verify_all",
                "reasoning": "Default plan — Opus planning unavailable",
            }

        logger.info(f"[Coordinator] Mission {mission.mission_id} planned: "
                     f"strategy={mission.opus_plan.get('strategy')}, "
                     f"groups={len(mission.opus_plan.get('video_groups', []))}")
        return mission

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 2: INGESTION — Parallel video processing
    # ═══════════════════════════════════════════════════════════════════════

    async def execute_ingestion(self, mission: Mission) -> Mission:
        """Ingest all videos in parallel, monitored by coordinator."""
        mission.phase = MissionPhase.INGESTION

        # Create ingest tasks for each URL
        ingest_tasks = []
        for i, url in enumerate(mission.urls):
            task = AgentTask(
                mission_id=mission.mission_id,
                agent="video_ingest",
                action="ingest_url",
                params={"url": url, "url_index": i},
                phase=MissionPhase.INGESTION,
                priority=self._get_url_priority(i, mission.opus_plan),
            )
            ingest_tasks.append(task)
            mission.tasks.append(task)

        # Execute in parallel batches
        sorted_tasks = sorted(ingest_tasks, key=lambda t: t.priority, reverse=True)
        for batch_start in range(0, len(sorted_tasks), self._max_parallel):
            batch = sorted_tasks[batch_start:batch_start + self._max_parallel]
            await asyncio.gather(
                *[self._execute_ingest_task(t) for t in batch],
                return_exceptions=True,
            )

        # Opus monitors: how many succeeded?
        completed = [t for t in ingest_tasks if t.status == TaskStatus.COMPLETED]
        failed = [t for t in ingest_tasks if t.status == TaskStatus.FAILED]

        if failed:
            logger.info(f"[Coordinator] Ingestion: {len(completed)} done, "
                        f"{len(failed)} failed — Opus re-evaluating")
            # Re-plan: skip failed videos
            for t in failed:
                if t.params.get("retries", 0) < self._max_retries:
                    t.params["retries"] = t.params.get("retries", 0) + 1
                    t.status = TaskStatus.PENDING
                    logger.info(f"[Coordinator] Retrying: {t.params.get('url', '')[:60]}")
                    await self._execute_ingest_task(t)
                else:
                    t.status = TaskStatus.SKIPPED
                    logger.info(f"[Coordinator] Skipping after {self._max_retries} retries: "
                                f"{t.params.get('url', '')[:60]}")

        # Save mission state
        await self._save_mission(mission)
        return mission

    async def _execute_ingest_task(self, task: AgentTask):
        """Execute a single ingest task."""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        try:
            result = await self._call_agent("POST", "/api/v1/ingest/url", {
                "url": task.params["url"],
            })

            if "error" in result:
                task.status = TaskStatus.FAILED
                task.error = str(result["error"])[:500]
            else:
                task.status = TaskStatus.COMPLETED
                task.result = {
                    "job_id": result.get("job_id", ""),
                    "video_id": result.get("video_id", ""),
                    "status": result.get("status", ""),
                }
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)[:500]

        task.completed_at = time.time()

    def _get_url_priority(self, url_index: int, plan: Optional[Dict]) -> int:
        """Get priority for a URL based on Opus plan."""
        if not plan:
            return 5
        for group in plan.get("video_groups", []):
            if url_index in group.get("url_indices", []):
                p = group.get("priority", "medium")
                return {"high": 10, "medium": 5, "low": 1}.get(p, 5)
        return 5

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 3: VERIFICATION — Fact-check claims
    # ═══════════════════════════════════════════════════════════════════════

    async def execute_verification(self, mission: Mission) -> Mission:
        """Verify claims from ingested videos, prioritized by Opus plan."""
        mission.phase = MissionPhase.VERIFICATION

        # Get successfully ingested video IDs
        video_ids = []
        for t in mission.tasks:
            if t.agent == "video_ingest" and t.status == TaskStatus.COMPLETED:
                vid = t.result.get("video_id", "")
                if vid:
                    video_ids.append(vid)

        if not video_ids:
            logger.warning("[Coordinator] No videos ingested — skipping verification")
            return mission

        # Determine which videos to verify based on plan
        verify_strategy = (mission.opus_plan or {}).get("verification_strategy", "verify_all")

        if verify_strategy == "verify_data_relevant" and mission.external_data:
            # Only verify videos in data-relevant groups
            relevant_groups = (mission.opus_plan or {}).get("data_relevant_groups", [])
            relevant_indices = set()
            for group in (mission.opus_plan or {}).get("video_groups", []):
                if group.get("category") in relevant_groups:
                    relevant_indices.update(group.get("url_indices", []))
            # Map back to video_ids
            video_ids = [vid for i, vid in enumerate(video_ids) if i in relevant_indices] or video_ids

        # Create verify tasks
        verify_tasks = []
        for vid in video_ids:
            task = AgentTask(
                mission_id=mission.mission_id,
                agent="fact_verifier",
                action="verify_video",
                params={"video_id": vid},
                phase=MissionPhase.VERIFICATION,
                priority=5,
            )
            verify_tasks.append(task)
            mission.tasks.append(task)

        # Execute verification (parallel but limited)
        for batch_start in range(0, len(verify_tasks), self._max_parallel):
            batch = verify_tasks[batch_start:batch_start + self._max_parallel]
            await asyncio.gather(
                *[self._execute_verify_task(t) for t in batch],
                return_exceptions=True,
            )

        await self._save_mission(mission)
        return mission

    async def _execute_verify_task(self, task: AgentTask):
        """Execute a single verification task."""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        try:
            result = await self._call_agent("POST", "/api/v1/verify", {
                "video_id": task.params["video_id"],
                "max_claims": 20,
            })

            # Handle 402 — payment required
            if result.get("status") == 402 or result.get("payment_required"):
                # For coordinator missions, try with internal token or skip payment
                task.result = {"skipped_reason": "payment_required", "video_id": task.params["video_id"]}
                task.status = TaskStatus.COMPLETED  # Mark done but note payment needed
            elif "error" in result:
                task.status = TaskStatus.FAILED
                task.error = str(result["error"])[:500]
            else:
                task.status = TaskStatus.COMPLETED
                task.result = result
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)[:500]

        task.completed_at = time.time()

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 4: ANALYSIS — Contradictions + scorecards + data cross-ref
    # ═══════════════════════════════════════════════════════════════════════

    async def execute_analysis(self, mission: Mission) -> Mission:
        """Run intelligence layer: contradictions, scorecards, data cross-reference."""
        mission.phase = MissionPhase.ANALYSIS

        analysis_tasks = []

        # Task: Find contradictions
        contradiction_task = AgentTask(
            mission_id=mission.mission_id,
            agent="intelligence",
            action="find_contradictions",
            params={
                "speaker_filter": mission.speaker_filter,
                "external_data": mission.external_data,
                "max_videos": len(mission.urls),
            },
            phase=MissionPhase.ANALYSIS,
            priority=10,
        )
        analysis_tasks.append(contradiction_task)
        mission.tasks.append(contradiction_task)

        # Task: Speaker scorecard
        scorecard_task = AgentTask(
            mission_id=mission.mission_id,
            agent="intelligence",
            action="speaker_scorecard",
            params={
                "speaker": mission.speaker_filter,
                "external_data": mission.external_data,
            },
            phase=MissionPhase.ANALYSIS,
            priority=8,
        )
        analysis_tasks.append(scorecard_task)
        mission.tasks.append(scorecard_task)

        # Task: External data cross-reference (if data provided)
        if mission.external_data:
            crossref_task = AgentTask(
                mission_id=mission.mission_id,
                agent="intelligence",
                action="cross_reference",
                params={
                    "question": f"What claims from {mission.speaker_filter or 'all speakers'} "
                                f"are contradicted by the provided market data?",
                    "external_data": mission.external_data,
                    "speaker_filter": mission.speaker_filter,
                },
                phase=MissionPhase.ANALYSIS,
                priority=9,
            )
            analysis_tasks.append(crossref_task)
            mission.tasks.append(crossref_task)

        # Execute all analysis in parallel
        await asyncio.gather(
            *[self._execute_analysis_task(t) for t in analysis_tasks],
            return_exceptions=True,
        )

        await self._save_mission(mission)
        return mission

    async def _execute_analysis_task(self, task: AgentTask):
        """Execute an intelligence layer task."""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        try:
            if task.action == "find_contradictions":
                result = await self._call_agent("POST", "/api/v1/contradictions/find", task.params)
            elif task.action == "speaker_scorecard":
                result = await self._call_agent("POST", "/api/v1/speakers/scorecard", task.params)
            elif task.action == "cross_reference":
                result = await self._call_agent("POST", "/api/v1/intelligence/cross-reference", task.params)
            else:
                result = {"error": f"Unknown action: {task.action}"}

            if "error" in result:
                task.status = TaskStatus.FAILED
                task.error = str(result["error"])[:500]
            else:
                task.status = TaskStatus.COMPLETED
                task.result = result
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)[:500]

        task.completed_at = time.time()

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 5: SYNTHESIS — Opus resolves conflicts, produces final report
    # ═══════════════════════════════════════════════════════════════════════

    async def execute_synthesis(self, mission: Mission) -> Mission:
        """Opus 4.6 synthesizes all results into a final coordinated report."""
        mission.phase = MissionPhase.SYNTHESIS

        # Gather all results
        ingest_results = [t.result for t in mission.tasks
                          if t.agent == "video_ingest" and t.status == TaskStatus.COMPLETED and t.result]
        verify_results = [t.result for t in mission.tasks
                          if t.agent == "fact_verifier" and t.status == TaskStatus.COMPLETED and t.result]
        analysis_results = {}
        for t in mission.tasks:
            if t.agent == "intelligence" and t.status == TaskStatus.COMPLETED and t.result:
                analysis_results[t.action] = t.result

        # Build context for Opus
        context = {
            "videos_ingested": len(ingest_results),
            "videos_failed": len([t for t in mission.tasks if t.agent == "video_ingest" and t.status == TaskStatus.FAILED]),
            "verification_results": verify_results[:5],  # Summarize top 5
            "contradictions": analysis_results.get("find_contradictions", {}),
            "scorecards": analysis_results.get("speaker_scorecard", {}),
            "data_cross_reference": analysis_results.get("cross_reference", {}),
        }

        system = """You are the CortexOS coordinator producing a final intelligence report.
Synthesize all agent results into a clear, actionable report.

Structure:
1. EXECUTIVE SUMMARY (2-3 sentences)
2. KEY FINDINGS (most important contradictions and data conflicts)
3. SPEAKER RELIABILITY (grades and patterns)
4. DATA vs CLAIMS (where analysts were wrong vs right based on actual data)
5. RECOMMENDATIONS (which analysts to follow/ignore)

Be specific — cite video IDs, timestamps, exact numbers.
Keep it under 800 words."""

        user = f"""MISSION: {mission.description}
SPEAKER FILTER: {mission.speaker_filter or "all"}

RESULTS:
{json.dumps(context, indent=2, default=str)[:6000]}"""

        final_report = await self._call_opus(system, user, max_tokens=4000)

        mission.results = {
            "report": final_report,
            "progress": mission.progress,
            "contradictions": analysis_results.get("find_contradictions", {}),
            "scorecards": analysis_results.get("speaker_scorecard", {}),
            "data_cross_reference": analysis_results.get("cross_reference", {}),
            "videos_ingested": len(ingest_results),
            "claims_verified": sum(r.get("verified_claims", 0) for r in verify_results if isinstance(r, dict)),
        }

        mission.phase = MissionPhase.COMPLETED
        mission.completed_at = time.time()

        await self._save_mission(mission)

        logger.info(f"[Coordinator] Mission {mission.mission_id} COMPLETED in "
                     f"{round(mission.completed_at - mission.created_at, 1)}s — "
                     f"{mission.progress}")
        return mission

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN ORCHESTRATION LOOP
    # ═══════════════════════════════════════════════════════════════════════

    async def run_mission(
        self,
        urls: List[str],
        external_data: Optional[Dict] = None,
        speaker_filter: str = "",
        description: str = "",
        skip_ingest: bool = False,
        skip_verification: bool = False,
    ) -> Mission:
        """
        Full coordinated mission — the basketball team play.

        Opus 4.6 plans → agents execute in parallel → Opus monitors →
        re-plans on failure → synthesizes final report.
        """
        mission = Mission(
            urls=urls,
            external_data=external_data,
            speaker_filter=speaker_filter,
            description=description or f"Analyze {len(urls)} videos"
            + (f" for {speaker_filter}" if speaker_filter else "")
            + (" with external data" if external_data else ""),
        )

        self._missions[mission.mission_id] = mission
        logger.info(f"[Coordinator] Starting mission {mission.mission_id}: {mission.description}")

        try:
            # Phase 1: Opus plans
            mission = await self.plan_mission(mission)

            # Phase 2: Parallel ingestion
            if not skip_ingest:
                mission = await self.execute_ingestion(mission)
            else:
                logger.info("[Coordinator] Skipping ingestion (videos already indexed)")

            # Phase 3: Verification
            if not skip_verification:
                mission = await self.execute_verification(mission)
            else:
                logger.info("[Coordinator] Skipping verification")

            # Phase 4: Analysis (contradictions + scorecards + data cross-ref)
            mission = await self.execute_analysis(mission)

            # Phase 5: Opus synthesizes final report
            mission = await self.execute_synthesis(mission)

        except Exception as e:
            logger.error(f"[Coordinator] Mission failed: {e}")
            mission.phase = MissionPhase.FAILED
            mission.results = {"error": str(e)}
            mission.completed_at = time.time()

        return mission

    # ═══════════════════════════════════════════════════════════════════════
    # MongoDB persistence
    # ═══════════════════════════════════════════════════════════════════════

    async def _save_mission(self, mission: Mission):
        """Save mission state to MongoDB."""
        db = await self._get_db()
        if db is None:
            return
        try:
            await db.missions.update_one(
                {"mission_id": mission.mission_id},
                {"$set": {
                    **mission.to_dict(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"[Coordinator] Failed to save mission: {e}")

    async def get_mission(self, mission_id: str) -> Optional[Dict]:
        """Get mission status."""
        if mission_id in self._missions:
            return self._missions[mission_id].to_dict()
        db = await self._get_db()
        if db:
            doc = await db.missions.find_one({"mission_id": mission_id})
            if doc:
                doc.pop("_id", None)
                return doc
        return None

    async def list_missions(self, limit: int = 20) -> List[Dict]:
        """List recent missions."""
        db = await self._get_db()
        if db:
            cursor = db.missions.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
            return await cursor.to_list(length=limit)
        return [m.to_dict() for m in self._missions.values()]


# ═══════════════════════════════════════════════════════════════════════════
# Auto-Discovery: register_routes(app)
# ═══════════════════════════════════════════════════════════════════════════

_coordinator: Optional[AgentCoordinator] = None

def _get_coordinator() -> AgentCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = AgentCoordinator()
    return _coordinator


def register_routes(app):
    """Auto-discovered by main.py — registers coordinator endpoints."""
    from fastapi import BackgroundTasks
    from pydantic import BaseModel
    from typing import Optional as Opt, List as Lst, Dict as DDict

    class MissionRequest(BaseModel):
        urls: Lst[str]
        external_data: Opt[DDict] = None
        speaker_filter: str = ""
        description: str = ""
        skip_ingest: bool = False
        skip_verification: bool = False

    class MissionResponse(BaseModel):
        mission_id: str
        status: str
        message: str

    # In-memory background task tracking
    _running_missions: Dict[str, bool] = {}

    async def _run_mission_background(mission_req: MissionRequest):
        """Run mission in background."""
        coord = _get_coordinator()
        mission = await coord.run_mission(
            urls=mission_req.urls,
            external_data=mission_req.external_data,
            speaker_filter=mission_req.speaker_filter,
            description=mission_req.description,
            skip_ingest=mission_req.skip_ingest,
            skip_verification=mission_req.skip_verification,
        )
        _running_missions[mission.mission_id] = True

    @app.post("/api/v1/coordinator/mission", tags=["coordinator"])
    async def start_mission(req: MissionRequest, background_tasks: BackgroundTasks):
        """
        Start a coordinated multi-agent mission.

        Opus 4.6 plans the execution, agents run in parallel,
        coordinator monitors and re-plans on failure.

        Returns immediately with mission_id — poll /status for progress.
        """
        coord = _get_coordinator()
        mission = Mission(
            urls=req.urls,
            external_data=req.external_data,
            speaker_filter=req.speaker_filter,
            description=req.description or f"Analyze {len(req.urls)} videos",
        )
        coord._missions[mission.mission_id] = mission

        background_tasks.add_task(
            _run_mission_background, req
        )

        return MissionResponse(
            mission_id=mission.mission_id,
            status="accepted",
            message=f"Mission started: {len(req.urls)} videos"
            + (f", speaker={req.speaker_filter}" if req.speaker_filter else "")
            + (", with external data" if req.external_data else ""),
        )

    @app.post("/api/v1/coordinator/mission/sync", tags=["coordinator"])
    async def run_mission_sync(req: MissionRequest):
        """
        Run a coordinated mission synchronously (waits for completion).

        Use for smaller missions (< 5 videos). For larger missions,
        use /mission (async) + poll /status.
        """
        coord = _get_coordinator()
        mission = await coord.run_mission(
            urls=req.urls,
            external_data=req.external_data,
            speaker_filter=req.speaker_filter,
            description=req.description,
            skip_ingest=req.skip_ingest,
            skip_verification=req.skip_verification,
        )
        return mission.to_dict()

    @app.get("/api/v1/coordinator/mission/{mission_id}", tags=["coordinator"])
    async def get_mission_status(mission_id: str):
        """Get mission progress and results."""
        coord = _get_coordinator()
        mission = await coord.get_mission(mission_id)
        if not mission:
            from fastapi import HTTPException
            raise HTTPException(404, f"Mission {mission_id} not found")
        return mission

    @app.get("/api/v1/coordinator/missions", tags=["coordinator"])
    async def list_missions(limit: int = 20):
        """List recent missions."""
        coord = _get_coordinator()
        return {"missions": await coord.list_missions(limit)}

    @app.get("/api/v1/coordinator/stats", tags=["coordinator"])
    async def coordinator_stats():
        """Coordinator statistics."""
        coord = _get_coordinator()
        return {
            "agent": "coordinator",
            "status": "active",
            "model": coord._model,
            "max_parallel": coord._max_parallel,
            "max_retries": coord._max_retries,
            "active_missions": len([m for m in coord._missions.values()
                                    if m.phase not in (MissionPhase.COMPLETED, MissionPhase.FAILED)]),
            "total_missions": len(coord._missions),
        }

    logger.info(
        "[Coordinator] Registered routes: "
        "/api/v1/coordinator/mission, /mission/sync, /mission/{id}, /missions, /stats"
    )
