"""
CortexVid — Opus 4.6 Autonomous Video Intelligence Agent
==========================================================

Three ingest modes (human feeds the agent):
  POST /api/v1/videos/upload        → upload video file
  POST /api/v1/ingest/url           → ingest from YouTube URL
  POST /api/v1/ingest/urls          → batch ingest multiple URLs
  POST /api/v1/ingest/folder        → ingest all videos in a folder

Autonomous analysis (Opus 4.6 takes over):
  POST /api/v1/synthesize           → Opus-planned cited synthesis
  GET  /api/v1/videos/{id}/search   → semantic search over indexed video
  GET  /api/v1/jobs/{job_id}        → poll job status / progress
  GET  /api/v1/jobs/{job_id}/plan   → Opus planning details

Agent control:
  POST /api/v1/agent/start          → start autonomous monitoring loop
  POST /api/v1/agent/stop           → stop monitoring
  GET  /api/v1/agent/status         → agent state + findings
  GET  /api/v1/agent/alerts         → all generated alerts

Observability:
  GET  /api/v1/health               → health + Opus status
  GET  /api/v1/status               → full planner stats
  GET  /api/v1/metrics              → Opik traces
  GET  /api/v1/eval                 → evaluation scores

Opus 4.6 is the autonomous brain:
  - Plans which segments need vision analysis vs skip
  - Detects discrepancies between speech and visuals
  - Cross-references claims across ALL ingested videos
  - Chooses synthesis strategy (direct/comparative/investigative/timeline)
  - Generates alerts when contradictions or stale predictions found
"""

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ── Observability Setup ──────────────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "video_scripts"))

try:
    from observability import init_opik, metrics, trace_span, evaluate_search_quality
    OBSERVABILITY_AVAILABLE = init_opik()
except ImportError:
    OBSERVABILITY_AVAILABLE = False
    class DummyMetrics:
        def record(self, *args, **kwargs): pass
        def get_stats(self, *args, **kwargs): return {}
    metrics = DummyMetrics()
    from contextlib import contextmanager
    @contextmanager
    def trace_span(name, metadata=None):
        yield type('obj', (object,), {'set_metadata': lambda self, x: None})()
    def evaluate_search_quality(*args, **kwargs):
        return {"passed": True, "issues": []}

# ── CortexVid Observability (Opus planner traces) ────────────────────────────
try:
    from agents.observability import (
        init_opik as init_cortexvid_opik,
        get_metrics as get_cortexvid_metrics,
        VidExEvaluator,
    )
    init_cortexvid_opik()
    _evaluator = VidExEvaluator()
except ImportError:
    get_cortexvid_metrics = lambda limit=100: []
    _evaluator = None

# ── Opus Planner Setup ───────────────────────────────────────────────────────
_opus_planner = None

def _get_opus_planner():
    """Lazy-init the Opus planner — requires Anthropic API key."""
    global _opus_planner
    if _opus_planner is not None:
        return _opus_planner

    try:
        from agents.opus_planner import OpusPlanner
        from models.text_generator import TextGenerator

        paid_model = os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6")
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        use_paid = os.getenv("TEXT_PAID_FALLBACK", "true").lower() == "true"

        text_gen = TextGenerator(
            model_id="Qwen/Qwen2.5-7B-Instruct",
            local_path=os.getenv("TEXT_MODEL_PATH", "Qwen/Qwen2.5-7B-Instruct"),
            device="cpu",
            torch_dtype="float32",
            max_new_tokens=1024,
            paid_fallback=use_paid,
            paid_model=paid_model,
        )

        _opus_planner = OpusPlanner(text_gen)
        print(f"[CortexVid] Opus planner initialized — model={paid_model}, paid_fallback={use_paid}")
        return _opus_planner
    except Exception as e:
        print(f"[CortexVid] Opus planner init failed: {e} — running without planning")
        return None


# ── Directories ──────────────────────────────────────────────────────────────
DATA_DIR   = Path(os.getenv("DATA_DIR", "/data"))
UPLOADS    = DATA_DIR / "uploads"
OUT_DIR    = DATA_DIR / "out"
CLIPS_DIR  = OUT_DIR / "clips"
for _d in (UPLOADS, OUT_DIR, CLIPS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

WEAVIATE_URL = os.getenv("WEAVIATE_URL", "http://weaviate:8080")

# ── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="CortexVid — Opus 4.6 Video Intelligence",
    version="2.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Agent Auto-Discovery ────────────────────────────────────────────────────
# Drop a .py file in agents/ with a register_routes(app) function and it
# auto-registers its endpoints on startup. No main.py edits needed.
# ─────────────────────────────────────────────────────────────────────────────

import importlib
import pathlib

_AGENTS_DIR = pathlib.Path(__file__).parent / "agents"
_discovered_agents: Dict[str, Any] = {}

def discover_agents():
    """Auto-discover and register agents that expose register_routes(app)."""
    if not _AGENTS_DIR.is_dir():
        return

    for agent_file in sorted(_AGENTS_DIR.glob("*.py")):
        name = agent_file.stem
        # Skip private files, __init__, base classes
        if name.startswith("_") or name in ("__init__", "base_agent"):
            continue
        try:
            mod = importlib.import_module(f"agents.{name}")
            if hasattr(mod, "register_routes"):
                mod.register_routes(app)
                _discovered_agents[name] = {
                    "status": "registered",
                    "has_routes": True,
                    "module": name,
                }
                print(f"[CortexOS] ✓ Auto-registered agent: {name}")
            else:
                _discovered_agents[name] = {
                    "status": "loaded",
                    "has_routes": False,
                    "module": name,
                }
        except Exception as e:
            _discovered_agents[name] = {
                "status": "failed",
                "has_routes": False,
                "error": str(e)[:200],
            }
            print(f"[CortexOS] ✗ Skipped agent {name}: {e}")

# Run discovery at import time
discover_agents()

# ── In-memory job store ──────────────────────────────────────────────────────
jobs: Dict[str, Dict[str, Any]] = {}


# ── Pydantic schemas ────────────────────────────────────────────────────────
class JobStatus(BaseModel):
    job_id: str
    video_id: str
    status: str
    progress: float
    message: str
    created_at: str
    updated_at: str

class SearchHit(BaseModel):
    text: str
    start_seconds: float
    end_seconds: float
    distance: Optional[float] = None
    snippet_index: Optional[int] = None
    chunk_index: Optional[int] = None
    source: Optional[str] = "transcript"

class SearchResult(BaseModel):
    query: str
    video_id: str
    hits: List[SearchHit]
    best_window: Optional[Dict[str, Any]] = None
    clip_url: Optional[str] = None
    answer: Optional[str] = None
    visual_hits_count: Optional[int] = 0
    transcript_hits_count: Optional[int] = 0

class SynthesizeRequest(BaseModel):
    question: str
    video_id: Optional[str] = None
    search_mode: str = "hybrid"
    top_k: int = 8

class IngestUrlRequest(BaseModel):
    url: str
    title: Optional[str] = None
    start_time: Optional[str] = None    # e.g. "1:30" or "90"
    end_time: Optional[str] = None      # e.g. "2:15" or "135"

class IngestUrlsRequest(BaseModel):
    urls: List[Dict[str, Any]]          # [{"url": "...", "title": "...", "start_time": "1:30", "end_time": "2:15"}, ...]


# ── Helpers ──────────────────────────────────────────────────────────────────
def _job_status(j: Dict) -> JobStatus:
    return JobStatus(**{k: j[k] for k in JobStatus.model_fields})


async def _run(cmd: List[str], cwd: Optional[str] = None):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    out, err = await proc.communicate()
    return subprocess.CompletedProcess(
        args=cmd, returncode=proc.returncode,
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
    )


# ── Opus-Planned Video Processing Pipeline ───────────────────────────────────
async def _process_video(job_id: str, video_path: Path, video_id: str):
    """
    Video processing with Opus 4.6 planning.

    After transcription, Opus examines each segment and decides:
    - Which segments need CLIP visual analysis (skip filler/intros)
    - Which segments have verifiable claims (prioritize enrichment)
    - Which segments are clip-worthy
    - Risk score for misleading content
    """
    j = jobs[job_id]
    t0 = time.time()
    planner = _get_opus_planner()

    try:
        # ── Step 1: Slice + Transcribe ───────────────────────────────────
        j.update(status="processing", progress=0.05,
                 message="Step 1/5: Splitting video into segments with ffmpeg…",
                 updated_at=datetime.now(timezone.utc).isoformat())

        vid_out = OUT_DIR / video_id
        vid_out.mkdir(parents=True, exist_ok=True)

        whisper_model = os.getenv("WHISPER_MODEL", "tiny")
        j.update(progress=0.10,
                 message=f"Step 1/5: Running Whisper ({whisper_model}) transcription…",
                 updated_at=datetime.now(timezone.utc).isoformat())

        r = await _run([
            "python3", "/app/video_scripts/yt_slice_chatgpt.py",
            "--input", str(video_path),
            "--outdir", str(vid_out),
            "--whisper-model", whisper_model,
        ])
        elapsed = int(time.time() - t0)
        if r.returncode != 0:
            raise RuntimeError(f"Slice/transcribe failed ({elapsed}s): {r.stderr[:500]}")

        json_path = vid_out / "snippets_with_transcripts.json"
        if not json_path.exists():
            cands = list(vid_out.rglob("snippets_with_transcripts.json"))
            if not cands:
                raise RuntimeError("Slicer produced no snippets JSON")
            json_path = cands[0]

        j["index_json"] = str(json_path)

        # Parse segments
        snippets_data = json.loads(json_path.read_text(encoding="utf-8"))
        segments = snippets_data.get("segments", [])

        elapsed = int(time.time() - t0)
        j.update(progress=0.40,
                 message=f"Step 2/5: Transcription done ({elapsed}s). Opus 4.6 planning…",
                 updated_at=datetime.now(timezone.utc).isoformat())

        # ── Step 2: Opus Planning ────────────────────────────────────────
        opus_plan = None
        if planner and len(segments) > 0:
            try:
                plan_segments = [
                    {
                        "index": seg.get("index", i),
                        "transcript": seg.get("transcript", ""),
                        "start_seconds": seg.get("start_seconds", 0),
                        "end_seconds": seg.get("end_seconds", 0),
                        "keyframe_count": len(seg.get("frames", {}).get("frame_files", [])),
                    }
                    for i, seg in enumerate(segments)
                ]

                ingest_plans = await planner.plan_ingest(plan_segments)
                opus_plan = {
                    "total_segments": len(segments),
                    "vision_planned": sum(1 for p in ingest_plans if p.vision_analysis),
                    "enrichment_planned": sum(1 for p in ingest_plans if p.enrichment),
                    "clip_worthy": sum(1 for p in ingest_plans if p.clip_worthy),
                    "skipped": [
                        {"segment": p.segment_index, "reason": p.skip_reason}
                        for p in ingest_plans if p.skip_reason
                    ],
                    "plans": [p.to_dict() for p in ingest_plans],
                }

                print(f"[Opus] Plan: {opus_plan['vision_planned']}/{len(segments)} vision, "
                      f"{opus_plan['enrichment_planned']}/{len(segments)} enrichment, "
                      f"{opus_plan['clip_worthy']} clip-worthy")

            except Exception as e:
                print(f"[Opus] Planning failed: {e} — processing all segments")
                ingest_plans = None
        else:
            ingest_plans = None

        j["opus_plan"] = opus_plan

        elapsed = int(time.time() - t0)
        plan_msg = (f"Opus planned: {opus_plan['vision_planned']}/{len(segments)} need vision"
                    if opus_plan else "No Opus plan — processing all")
        j.update(progress=0.50,
                 message=f"Step 3/5: {plan_msg}. Ingesting transcripts ({elapsed}s)…",
                 updated_at=datetime.now(timezone.utc).isoformat())

        # ── Step 3: Weaviate Transcript Ingest ───────────────────────────
        r = await _run([
            "python3", "/app/video_scripts/weaviate_ingest.py",
            "--json", str(json_path),
            "--video-id", video_id,
            "--collection", "VideoChunks",
        ])
        if r.returncode != 0:
            raise RuntimeError(f"Weaviate ingest failed: {r.stderr[:500]}")

        elapsed = int(time.time() - t0)
        j.update(progress=0.65,
                 message=f"Step 4/5: Transcripts indexed ({elapsed}s). CLIP keyframes (Opus-selective)…",
                 updated_at=datetime.now(timezone.utc).isoformat())

        # ── Step 4: Opus-Selective CLIP Keyframe Extraction ──────────────
        all_keyframe_jsons: list[Path] = []

        for i, seg in enumerate(segments):
            # Check if Opus says to skip vision for this segment
            if ingest_plans and i < len(ingest_plans):
                plan = ingest_plans[i]
                if not plan.vision_analysis:
                    print(f"[Opus] Skipping CLIP for segment {i}: {plan.skip_reason}")
                    continue

            seg_idx = seg.get("index", i)
            seg_video = seg.get("video_path", "")
            seg_start = seg.get("start_seconds", 0.0)
            seg_end = seg.get("end_seconds", 0.0)
            frames_info = seg.get("frames", {})
            frames_dir = frames_info.get("frames_dir", "")

            if not seg_video or not Path(seg_video).exists():
                continue

            kf_json = vid_out / f"keyframes_seg_{seg_idx:03d}.json"

            # Determine frame count based on Opus priority
            k_frames = "4"
            if ingest_plans and i < len(ingest_plans):
                priority = ingest_plans[i].vision_priority
                if priority == "high":
                    k_frames = "6"
                elif priority == "low":
                    k_frames = "2"

            kf_cmd = [
                "python3", "/app/video_scripts/keyframes_describe.py",
                "--out", str(kf_json),
                "--fps", "1",
                "--k", k_frames,
                "--max-hamming", "6",
                "--clip-id", f"{video_id}_seg{seg_idx}",
                "--t1", str(seg_start),
                "--t2", str(seg_end),
                "--no-llm", "1",
            ]

            if frames_dir and Path(frames_dir).exists():
                kf_cmd += ["--frames-dir", frames_dir]
            else:
                kf_cmd += ["--clip", seg_video]

            r = await _run(kf_cmd)
            if r.returncode == 0 and kf_json.exists():
                all_keyframe_jsons.append(kf_json)

        # ── Step 4b: Opus Adaptive Analysis (post-vision) ────────────────
        adaptive_results = []
        if planner and ingest_plans:
            for i, seg in enumerate(segments):
                if i < len(ingest_plans) and ingest_plans[i].vision_analysis:
                    kf_json = vid_out / f"keyframes_seg_{seg.get('index', i):03d}.json"
                    if kf_json.exists():
                        try:
                            kf_data = json.loads(kf_json.read_text())
                            analysis = await planner.analyze_segment_deep(
                                transcript=seg.get("transcript", ""),
                                vision_output={"keyframes": kf_data.get("keyframes", [])},
                                video_id=video_id,
                                start_seconds=seg.get("start_seconds", 0),
                                end_seconds=seg.get("end_seconds", 0),
                            )
                            if analysis.discrepancies or analysis.risk_score > 0.5:
                                adaptive_results.append({
                                    "segment": i,
                                    "risk_score": analysis.risk_score,
                                    "discrepancies": analysis.discrepancies,
                                    "claims_to_verify": analysis.claims_to_verify,
                                })
                                print(f"[Opus] Segment {i}: risk={analysis.risk_score}, "
                                      f"discrepancies={len(analysis.discrepancies)}")
                        except Exception as e:
                            print(f"[Opus] Adaptive analysis failed for segment {i}: {e}")

        j["adaptive_analysis"] = adaptive_results

        elapsed = int(time.time() - t0)
        j.update(progress=0.85,
                 message=f"Step 5/5: CLIP done ({len(all_keyframe_jsons)} segments). Indexing visuals ({elapsed}s)…",
                 updated_at=datetime.now(timezone.utc).isoformat())

        # ── Step 5: Ingest Keyframes into Weaviate ───────────────────────
        kf_ingested = 0
        for kf_json in all_keyframe_jsons:
            clip_id = kf_json.stem
            r = await _run([
                "python3", "/app/video_scripts/weaviate_ingest_keyframes.py",
                "--json", str(kf_json),
                "--video-id", video_id,
                "--clip-id", clip_id,
                "--collection", "VideoKeyframe",
            ])
            if r.returncode == 0:
                kf_ingested += 1

        elapsed = int(time.time() - t0)

        # Build completion message
        opus_summary = ""
        if opus_plan:
            skipped = len(segments) - opus_plan["vision_planned"]
            opus_summary = f" | Opus: skipped {skipped} segments, "
            if adaptive_results:
                opus_summary += f"found {len(adaptive_results)} risk segments"
            else:
                opus_summary += "no risks detected"

        j.update(status="ready", progress=1.0,
                 message=(f"Done ✓ ({elapsed}s) — {kf_ingested} visual + transcript indexed"
                          f"{opus_summary}"),
                 updated_at=datetime.now(timezone.utc).isoformat())

        # Score with evaluator
        if _evaluator and opus_plan:
            _evaluator.score_ingest({
                "segments_count": len(segments),
                "opus_plan": opus_plan,
                "discrepancies_found": sum(len(a.get("discrepancies", [])) for a in adaptive_results),
                "high_risk_segments": [a["segment"] for a in adaptive_results if a.get("risk_score", 0) > 0.5],
                "pipeline_duration_seconds": elapsed,
                "payment": {"amount_usdc": 0},
            })

    except Exception as exc:
        elapsed = int(time.time() - t0)
        j.update(status="failed", progress=0.0,
                 message=f"Failed after {elapsed}s: {str(exc)[:400]}",
                 updated_at=datetime.now(timezone.utc).isoformat())


# ═══════════════════════════════════════════════════════════════════════════
# API v1 routes
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/health")
async def health():
    planner = _get_opus_planner()
    return {
        "status": "ok",
        "service": "cortexvid",
        "version": "2.0.0",
        "opus_planner": "active" if planner else "inactive",
        "model": os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
        "ts": datetime.now(timezone.utc).isoformat(),
        "discovered_agents": {
            name: info["status"]
            for name, info in _discovered_agents.items()
        },
        "observability": {
            "enabled": OBSERVABILITY_AVAILABLE,
            "opik_url": os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api"),
            "project": os.getenv("OPIK_PROJECT_NAME", "CortexVid"),
        }
    }

@app.get("/health")
async def health_compat():
    return await health()


@app.get("/api/v1/agents")
async def list_agents():
    """List all discovered agents and their registration status."""
    return {
        "agents": _discovered_agents,
        "total": len(_discovered_agents),
        "registered": len([a for a in _discovered_agents.values() if a.get("has_routes")]),
    }


@app.get("/api/v1/status")
async def status():
    """Full CortexVid status including Opus planner stats."""
    planner = _get_opus_planner()
    return {
        "opus_planner": planner.get_stats() if planner else {"status": "not initialized"},
        "model": os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
        "paid_fallback": os.getenv("TEXT_PAID_FALLBACK", "false"),
        "whisper_model": os.getenv("WHISPER_MODEL", "tiny"),
        "active_jobs": len(jobs),
        "completed_jobs": len([j for j in jobs.values() if j["status"] == "ready"]),
        "videos_with_opus_plans": len([j for j in jobs.values() if j.get("opus_plan")]),
        "total_risks_detected": sum(
            len(j.get("adaptive_analysis", [])) for j in jobs.values()
        ),
    }


@app.get("/api/v1/metrics")
async def get_metrics_endpoint():
    """Observability metrics — Opus planning + search latencies."""
    return {
        "opus": get_cortexvid_metrics(100),
        "search": {
            "clip_text_embed": metrics.get_stats("clip_text_embed_latency_ms"),
            "clip_image_embed": metrics.get_stats("clip_image_embed_latency_ms"),
            "weaviate_search": metrics.get_stats("weaviate_search_latency_ms"),
            "search_e2e": metrics.get_stats("search_e2e_latency_ms"),
        },
    }


@app.get("/api/v1/eval")
async def eval_summary():
    """Evaluation summary — Opus planning efficiency and synthesis quality."""
    if _evaluator:
        return _evaluator.get_summary()
    return {"error": "Evaluator not initialized"}


@app.get("/api/v1/debug")
async def debug_info():
    import shutil
    return {
        "active_jobs": len(jobs),
        "jobs_summary": [
            {"id": j["job_id"][:8], "video": j["video_id"], "status": j["status"],
             "progress": j["progress"], "msg": j["message"][:100],
             "opus_plan": bool(j.get("opus_plan")),
             "risks": len(j.get("adaptive_analysis", []))}
            for j in jobs.values()
        ],
        "disk_uploads": len(list(UPLOADS.glob("*"))) if UPLOADS.exists() else 0,
        "disk_out": len(list(OUT_DIR.glob("*"))) if OUT_DIR.exists() else 0,
        "whisper_model": os.getenv("WHISPER_MODEL", "tiny"),
        "model": os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
        "weaviate_url": WEAVIATE_URL,
        "ffmpeg": shutil.which("ffmpeg") is not None,
    }


# ── Upload ───────────────────────────────────────────────────────────────────
@app.post("/api/v1/videos/upload", response_model=JobStatus)
async def upload_video(file: UploadFile = File(...)):
    video_id = uuid.uuid4().hex[:12]
    job_id   = uuid.uuid4().hex
    ext      = Path(file.filename or "v.mp4").suffix or ".mp4"

    # Ensure upload directory exists (may not survive container restarts)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dest     = UPLOADS / f"{video_id}{ext}"

    with dest.open("wb") as fout:
        shutil.copyfileobj(file.file, fout)

    # Verify file was actually written
    if not dest.exists() or dest.stat().st_size == 0:
        raise HTTPException(status_code=500, detail=f"File save failed — check /data/uploads permissions")
    print(f"[Upload] Saved {dest} ({dest.stat().st_size / 1e6:.1f}MB)")

    now = datetime.now(timezone.utc).isoformat()
    jobs[job_id] = dict(
        job_id=job_id, video_id=video_id, status="pending",
        progress=0.0, message="Queued — Opus 4.6 will plan processing",
        video_path=str(dest), index_json=None,
        opus_plan=None, adaptive_analysis=[],
        created_at=now, updated_at=now,
    )
    asyncio.create_task(_process_video(job_id, dest, video_id))
    return _job_status(jobs[job_id])


# ── URL Download Helper ──────────────────────────────────────────────────────
async def _download_url(url: str, dest: Path, start_time: Optional[str] = None,
                        end_time: Optional[str] = None) -> bool:
    """Download video from URL using yt-dlp. Optionally extract a time range."""
    cmd = ["yt-dlp", "-f", "worst[ext=mp4]/worst", "-o", str(dest)]

    # Time-range extraction: download only the specified section
    if start_time and end_time:
        cmd += ["--download-sections", f"*{start_time}-{end_time}"]
    elif start_time:
        cmd += ["--download-sections", f"*{start_time}-"]

    cmd.append(url)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()

    if proc.returncode != 0:
        # Log error but check if file exists (yt-dlp sometimes returns non-zero but succeeds)
        print(f"[yt-dlp] Warning: {err.decode()[:300]}")

    # yt-dlp may add suffix, find the actual file
    if not dest.exists():
        candidates = list(dest.parent.glob(f"{dest.stem}*"))
        if candidates:
            candidates[0].rename(dest)

    return dest.exists()


# ── Ingest from URL ──────────────────────────────────────────────────────────
@app.post("/api/v1/ingest/url")
async def ingest_url(req: IngestUrlRequest):
    """
    Ingest a video from YouTube URL (or any yt-dlp supported URL).
    Optionally specify start_time and end_time to extract a clip.

    Example:
      {"url": "https://youtube.com/watch?v=xxx", "title": "Tom Lee BTC $200K", "start_time": "1:30", "end_time": "2:15"}
    """
    video_id = uuid.uuid4().hex[:12]
    job_id = uuid.uuid4().hex
    dest = UPLOADS / f"{video_id}.mp4"

    now = datetime.now(timezone.utc).isoformat()
    jobs[job_id] = dict(
        job_id=job_id, video_id=video_id, status="downloading",
        progress=0.0, message=f"Downloading: {req.title or req.url[:60]}…",
        video_path=str(dest), index_json=None,
        opus_plan=None, adaptive_analysis=[],
        source_url=req.url, title=req.title,
        created_at=now, updated_at=now,
    )

    async def _download_and_process():
        j = jobs[job_id]
        try:
            j.update(progress=0.02,
                     message=f"Downloading from {req.url[:60]}…",
                     updated_at=datetime.now(timezone.utc).isoformat())

            success = await _download_url(req.url, dest, req.start_time, req.end_time)
            if not success:
                j.update(status="failed", progress=0.0,
                         message=f"Download failed: {req.url[:100]}",
                         updated_at=datetime.now(timezone.utc).isoformat())
                return

            j.update(progress=0.05,
                     message="Downloaded ✓ Starting Opus-planned processing…",
                     updated_at=datetime.now(timezone.utc).isoformat())

            await _process_video(job_id, dest, video_id)

        except Exception as e:
            j.update(status="failed", progress=0.0,
                     message=f"Failed: {str(e)[:300]}",
                     updated_at=datetime.now(timezone.utc).isoformat())

    asyncio.create_task(_download_and_process())

    return {
        "job_id": job_id,
        "video_id": video_id,
        "status": "downloading",
        "message": f"Queued: {req.title or req.url[:60]}",
        "source_url": req.url,
    }


# ── Batch Ingest from URLs ──────────────────────────────────────────────────
@app.post("/api/v1/ingest/urls")
async def ingest_urls(req: IngestUrlsRequest):
    """
    Batch ingest multiple videos from URLs.

    Example:
      {"urls": [
        {"url": "https://youtube.com/watch?v=xxx", "title": "Tom Lee Jan 2025", "start_time": "1:30", "end_time": "2:15"},
        {"url": "https://youtube.com/watch?v=yyy", "title": "Tom Lee Mar 2025"},
        {"url": "https://youtube.com/watch?v=zzz", "title": "Tom Lee Jun 2025", "start_time": "5:00", "end_time": "5:45"}
      ]}
    """
    results = []
    for item in req.urls:
        url = item.get("url", "")
        if not url:
            results.append({"url": "", "error": "Missing URL"})
            continue

        try:
            sub_req = IngestUrlRequest(
                url=url,
                title=item.get("title"),
                start_time=item.get("start_time"),
                end_time=item.get("end_time"),
            )
            result = await ingest_url(sub_req)
            results.append({
                "url": url,
                "title": item.get("title"),
                "job_id": result["job_id"],
                "video_id": result["video_id"],
                "status": "queued",
            })
        except Exception as e:
            results.append({"url": url, "error": str(e)[:200]})

    queued = len([r for r in results if r.get("status") == "queued"])
    failed = len([r for r in results if r.get("error")])

    return {
        "total": len(req.urls),
        "queued": queued,
        "failed": failed,
        "results": results,
    }


# ── Ingest from Local Folder ────────────────────────────────────────────────
@app.post("/api/v1/ingest/folder")
async def ingest_folder(folder: str = Query(..., description="Path to folder of video clips")):
    """
    Batch ingest all video files in a local folder.
    Use for pre-loading demo clips.

    Example: POST /api/v1/ingest/folder?folder=/data/clips
    """
    folder_path = Path(folder)
    if not folder_path.exists():
        raise HTTPException(404, f"Folder not found: {folder}")

    video_files = sorted(
        f for f in folder_path.iterdir()
        if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".avi", ".mov")
    )

    if not video_files:
        raise HTTPException(404, f"No video files found in {folder}")

    results = []
    for vf in video_files:
        video_id = uuid.uuid4().hex[:12]
        job_id = uuid.uuid4().hex
        dest = UPLOADS / f"{video_id}{vf.suffix}"

        # Copy to uploads dir
        shutil.copy2(str(vf), str(dest))

        now = datetime.now(timezone.utc).isoformat()
        jobs[job_id] = dict(
            job_id=job_id, video_id=video_id, status="pending",
            progress=0.0, message=f"Queued: {vf.name}",
            video_path=str(dest), index_json=None,
            opus_plan=None, adaptive_analysis=[],
            source_file=str(vf), title=vf.stem,
            created_at=now, updated_at=now,
        )
        asyncio.create_task(_process_video(job_id, dest, video_id))

        results.append({
            "file": vf.name,
            "job_id": job_id,
            "video_id": video_id,
            "status": "queued",
        })

    return {
        "total": len(video_files),
        "queued": len(results),
        "results": results,
    }


# ── Jobs ─────────────────────────────────────────────────────────────────────
@app.get("/api/v1/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return _job_status(jobs[job_id])

@app.get("/api/v1/jobs")
async def list_jobs():
    return [_job_status(j) for j in
            sorted(jobs.values(), key=lambda x: x["created_at"], reverse=True)]

@app.get("/api/v1/jobs/{job_id}/plan")
async def get_job_plan(job_id: str):
    """Get Opus planning details for a job."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    j = jobs[job_id]
    return {
        "job_id": job_id,
        "video_id": j["video_id"],
        "opus_plan": j.get("opus_plan"),
        "adaptive_analysis": j.get("adaptive_analysis", []),
    }


# ── Synthesize (Opus-planned) ────────────────────────────────────────────────
@app.post("/api/v1/synthesize")
async def synthesize(req: SynthesizeRequest):
    """
    Opus-planned synthesis: search → plan strategy → generate cited answer.

    Opus 4.6 examines search results and decides:
    - Are results sufficient or need more searches?
    - Direct answer or investigative analysis?
    - Fresh web verification needed?
    """
    planner = _get_opus_planner()
    if not planner:
        raise HTTPException(503, "Opus planner not available — check ANTHROPIC_API_KEY")

    # Step 1: Search Weaviate
    sys.path.insert(0, "/app/video_scripts")
    try:
        from query_weaviate import _connect_weaviate, _fetch_hits
    except ImportError as e:
        raise HTTPException(500, f"Import error: {e}")

    client = _connect_weaviate()
    try:
        transcript_hits = _fetch_hits(client, "VideoChunks", req.question, req.top_k,
                                       req.video_id)
        visual_hits = []
        try:
            visual_hits = _fetch_hits(client, "VideoKeyframe", req.question,
                                       min(req.top_k, 6), req.video_id)
        except Exception:
            pass
    finally:
        try: client.close()
        except: pass

    # Build search results for planner
    merged = []
    for h in transcript_hits:
        merged.append({
            "video_id": req.video_id or "unknown",
            "start_seconds": h.start_s,
            "end_seconds": h.end_s,
            "text": h.text,
            "score": 1 - (h.distance or 0),
            "match_source": "transcript",
        })
    for h in visual_hits:
        merged.append({
            "video_id": req.video_id or "unknown",
            "start_seconds": h.start_s,
            "text": h.text,
            "score": 1 - (h.distance or 0),
            "match_source": "visual",
        })

    search_results = {"merged": merged, "mode": req.search_mode,
                      "total_results": len(merged)}

    # Step 2: Opus plans synthesis strategy
    plan = await planner.plan_synthesis(req.question, search_results, [])

    # Step 3: Generate synthesis using Opus 4.6
    from agents.orchestrator import SYNTHESIS_STRATEGIES

    strategy_prompt = SYNTHESIS_STRATEGIES.get(plan.strategy, SYNTHESIS_STRATEGIES["direct"])

    # Build context for synthesis
    video_context = "\n\n".join([
        f"[{r['match_source']}] {r.get('start_seconds', 0):.0f}s: {r['text'][:300]}"
        for r in merged[:8]
    ])

    synthesis_prompt = f"""## User Question
{req.question}

## Video Content Found ({len(merged)} matches)
{video_context or "No matching video content found."}

## Instructions
Synthesize a clear answer with timestamps and citations."""

    try:
        synthesis_text = await planner.text_gen.generate(
            system=strategy_prompt,
            user=synthesis_prompt,
            max_tokens=1024,
            temperature=0.2,
        )
    except Exception as e:
        synthesis_text = f"Synthesis generation failed: {e}. Found {len(merged)} matches."

    result = {
        "question": req.question,
        "synthesis": synthesis_text,
        "opus_plan": plan.to_dict(),
        "strategy": plan.strategy,
        "confidence": plan.confidence,
        "search_results": len(merged),
        "transcript_matches": len(transcript_hits),
        "visual_matches": len(visual_hits),
    }

    # Score with evaluator
    if _evaluator:
        _evaluator.score_synthesis({
            "synthesis": synthesis_text,
            "sources": [],
            "opus_plan": plan.to_dict(),
            "payment": {"amount_usdc": 0.03},
        })

    return result


# ── Search ───────────────────────────────────────────────────────────────────
@app.get("/api/v1/videos/{video_id}/search", response_model=SearchResult)
async def search_video(
    video_id: str,
    q: str = Query(..., description="Natural-language query"),
    top_k: int = Query(8, ge=1, le=50),
    collection: str = Query("VideoChunks"),
    include_visual: bool = Query(True),
):
    search_start = time.time()

    with trace_span("chartseek_search", {"video_id": video_id, "query": q, "top_k": top_k}) as span:
        sys.path.insert(0, "/app/video_scripts")
        try:
            from query_weaviate import (
                _connect_weaviate, _fetch_hits,
                _merge_hits_into_windows, _pick_best_window,
            )
        except ImportError as e:
            raise HTTPException(500, f"Import error: {e}")

        client = _connect_weaviate()
    try:
        hits = _fetch_hits(client, collection, q, top_k, video_id)

        visual_hits_raw = []
        if include_visual:
            try:
                visual_hits_raw = _fetch_hits(client, "VideoKeyframe", q, min(top_k, 6), video_id)
            except Exception:
                pass
    finally:
        try: client.close()
        except: pass

    search_hits = [
        SearchHit(text=h.text, start_seconds=h.start_s, end_seconds=h.end_s,
                  distance=h.distance, snippet_index=h.snippet_index,
                  chunk_index=h.chunk_index, source="transcript")
        for h in hits
    ]
    transcript_count = len(search_hits)

    visual_count = 0
    for vh in visual_hits_raw:
        t = vh.start_s
        search_hits.append(
            SearchHit(
                text=f"[Visual] {vh.text}",
                start_seconds=max(0, t - 3.0),
                end_seconds=t + 5.0,
                distance=vh.distance,
                source="visual",
            )
        )
        visual_count += 1

    search_hits.sort(key=lambda h: (h.distance if h.distance is not None else 9999.0))

    all_raw_hits = list(hits) + list(visual_hits_raw)
    best_window = None
    clip_url = None

    if all_raw_hits:
        windows = _merge_hits_into_windows(all_raw_hits, 8.0, 140.0)
        bw = _pick_best_window(windows)
        if bw:
            t1, t2, wh = bw
            best_window = {"start_seconds": max(0, t1 - 2), "end_seconds": t2 + 2,
                           "hit_count": len(wh)}

            ready = [j for j in jobs.values()
                     if j["video_id"] == video_id and j["status"] == "ready"]
            if ready:
                idx_json = ready[0].get("index_json")
                if idx_json and Path(idx_json).exists():
                    clip_name = f"{video_id}_{int(t1)}_{int(t2)}.mp4"
                    clip_path = CLIPS_DIR / clip_name
                    try:
                        from get_clip import get_clip as make_clip
                        make_clip(Path(idx_json), max(0, t1-2), t2+2, clip_path)
                        clip_url = f"/api/v1/clips/{clip_name}"
                    except Exception:
                        pass

        search_latency_ms = (time.time() - search_start) * 1000
        metrics.record("search_e2e_latency_ms", search_latency_ms, {
            "video_id": video_id,
            "visual_hits": str(visual_count),
            "transcript_hits": str(transcript_count)
        })
        span.set_metadata({
            "visual_hits": visual_count,
            "transcript_hits": transcript_count,
            "total_hits": len(search_hits),
            "latency_ms": search_latency_ms
        })

    return SearchResult(query=q, video_id=video_id, hits=search_hits,
                        best_window=best_window, clip_url=clip_url,
                        visual_hits_count=visual_count,
                        transcript_hits_count=transcript_count)


# ── Direct clip extraction ───────────────────────────────────────────────────
@app.get("/api/v1/videos/{video_id}/clip")
async def extract_clip(
    video_id: str,
    t1: float = Query(..., ge=0),
    t2: float = Query(..., ge=0),
    reencode: bool = Query(False),
):
    if t2 <= t1:
        raise HTTPException(400, f"t2 ({t2}) must be greater than t1 ({t1})")

    ready = [j for j in jobs.values()
             if j["video_id"] == video_id and j["status"] == "ready"]
    if not ready:
        raise HTTPException(404, f"No processed video found for video_id={video_id}")

    idx_json = ready[0].get("index_json")
    if not idx_json or not Path(idx_json).exists():
        raise HTTPException(404, "Segment index not found")

    clip_name = f"{video_id}_{int(t1)}_{int(t2)}.mp4"
    clip_path = CLIPS_DIR / clip_name

    if not clip_path.exists():
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent / "video_scripts"))
            from get_clip import get_clip as make_clip
            make_clip(Path(idx_json), t1, t2, clip_path, reencode=reencode)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(500, f"Clip generation failed: {e}")

    return {
        "video_id": video_id,
        "t1": t1, "t2": t2,
        "duration_seconds": round(t2 - t1, 3),
        "clip_url": f"/api/v1/clips/{clip_name}",
    }


# ── Clip & file serving ─────────────────────────────────────────────────────
@app.get("/api/v1/clips/{clip_name}")
async def serve_clip(clip_name: str):
    p = CLIPS_DIR / clip_name
    if not p.exists():
        raise HTTPException(404, "Clip not found")
    return FileResponse(str(p), media_type="video/mp4", filename=clip_name)

@app.get("/api/v1/videos/{video_id}/file")
async def serve_video(video_id: str):
    m = [j for j in jobs.values() if j["video_id"] == video_id]
    if not m:
        raise HTTPException(404, "Video not found")
    vp = Path(m[0]["video_path"])
    if not vp.exists():
        raise HTTPException(404, "File missing")
    return FileResponse(str(vp), media_type="video/mp4")


# ═══════════════════════════════════════════════════════════════════════════
# Agent Control — Autonomous Monitoring Loop
# ═══════════════════════════════════════════════════════════════════════════
try:
    from agent_loop import get_agent

    @app.post("/api/v1/agent/start")
    async def start_agent(
        channels: str = Query("", description="Comma-separated YouTube channel IDs"),
        interval: int = Query(300, description="Check interval in seconds"),
    ):
        """
        Start the autonomous agent loop.
        The agent will monitor YouTube channels, auto-ingest new videos,
        cross-reference claims, and generate alerts.
        """
        agent = get_agent()
        if channels:
            agent.state.channels = [c.strip() for c in channels.split(",") if c.strip()]
        agent.state.check_interval_seconds = interval
        return await agent.start()

    @app.post("/api/v1/agent/stop")
    async def stop_agent():
        """Stop the autonomous agent loop."""
        return await get_agent().stop()

    @app.get("/api/v1/agent/status")
    async def agent_status():
        """
        Current agent state:
        - What mode it's in (watching/ingesting/analyzing/verifying/idle)
        - How many videos ingested
        - How many contradictions found
        - How many alerts generated
        """
        return get_agent().get_status()

    @app.get("/api/v1/agent/alerts")
    async def agent_alerts(
        severity: Optional[str] = Query(None, description="Filter: info/warning/critical"),
        limit: int = Query(50),
    ):
        """All alerts generated by the autonomous agent."""
        return get_agent().get_alerts(severity=severity, limit=limit)

    @app.post("/api/v1/agent/analyze")
    async def agent_analyze_now():
        """
        Trigger immediate cross-reference analysis across all ingested videos.
        Doesn't wait for the next cycle — runs NOW.
        """
        agent = get_agent()
        await agent._cross_reference_claims()
        return {
            "status": "analysis complete",
            "contradictions_found": agent.state.contradictions_found,
            "alerts": agent.state.alerts_generated,
        }

except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════════════════
# Legacy endpoints (backward compatibility)
# ═══════════════════════════════════════════════════════════════════════════
try:
    from instructor import SystemInstructor

    async def generate_response(task: str, websocket: Optional[WebSocket] = None):
        orchestrator: SystemInstructor = SystemInstructor()
        return await orchestrator.run(task, websocket)

    @app.get("/agent/chat")
    async def agent_chat(task: str) -> List:
        return await generate_response(task)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        while True:
            data = await websocket.receive_text()
            await generate_response(data, websocket)
except ImportError:
    pass
