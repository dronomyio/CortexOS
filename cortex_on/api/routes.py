"""
VidEx API Routes
================

Full capability set:

  POST /api/v1/ingest          — Upload & process video (segment → transcribe → index)
  POST /api/v1/process         — Process pre-segmented video data
  POST /api/v1/qa              — Ask questions with web-verified answers
  POST /api/v1/enrich          — Enrich a single segment

  GET  /api/v1/search          — Search indexed videos (hybrid/transcript/visual)
  POST /api/v1/clip            — Extract a clip by timestamp range

  GET  /api/v1/status          — Agent status + Weaviate + x402 spend
  GET  /api/v1/payments        — x402 payment ledger
  GET  /api/v1/payments/daily  — Today's spend vs guardrail limits
"""
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..agents.orchestrator import AgentOrchestrator
from ..config import AppConfig

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["videx"])

_orchestrator: Optional[AgentOrchestrator] = None


def init_orchestrator(config: AppConfig) -> AgentOrchestrator:
    global _orchestrator
    _orchestrator = AgentOrchestrator(config)
    return _orchestrator


def get_orchestrator() -> AgentOrchestrator:
    if _orchestrator is None:
        raise HTTPException(503, "Not initialized")
    return _orchestrator


# ─── Request Models ──────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    video_id: str
    segments: List[Dict[str, Any]]

class QARequest(BaseModel):
    question: str
    video_id: Optional[str] = None
    verify_with_web: bool = True

class EnrichRequest(BaseModel):
    video_id: str
    segment_index: int = 0
    analysis: Dict[str, Any] = Field(default_factory=dict)
    transcript: str = ""

class SearchRequest(BaseModel):
    query: str
    video_id: Optional[str] = None
    mode: str = "hybrid"   # hybrid | transcript | visual
    limit: int = 10

class ClipRequest(BaseModel):
    video_id: str
    start_seconds: float
    end_seconds: float
    source_path: Optional[str] = None  # Override if not ingested via /ingest

class SynthesizeRequest(BaseModel):
    question: str
    video_id: Optional[str] = None
    search_mode: str = "hybrid"        # hybrid | transcript | visual
    enrich_on_demand: bool = True      # Fresh Parallel.ai call for the question?
    limit: int = 10


# ─── Video Upload & Ingestion ────────────────────────────────────────────

@router.post("/ingest")
async def ingest_video(
    file: UploadFile = File(...),
    video_id: Optional[str] = Form(None),
):
    """
    Upload and fully process a video.

    Pipeline: FFmpeg segment → Whisper transcribe → Vision analyze
              → Weaviate index (segments + keyframes) → Parallel.ai enrich

    After ingestion, the video is fully searchable via /search and /qa.
    Clip extraction available via /clip.

    Payment: Billed per minute of video via x402.
    """
    orch = get_orchestrator()

    # Save uploaded file to temp location
    tmp_dir = tempfile.mkdtemp(prefix="videx_upload_")
    tmp_path = os.path.join(tmp_dir, file.filename or "video.mp4")
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        result = await orch.ingest_video(tmp_path, video_id)
        return result
    except Exception as e:
        raise HTTPException(500, f"Ingestion failed: {e}")


@router.post("/ingest/path")
async def ingest_video_by_path(video_path: str = Form(...), video_id: Optional[str] = Form(None)):
    """
    Ingest a video from a local filesystem path.
    For server-side processing without upload overhead.
    """
    orch = get_orchestrator()
    if not os.path.exists(video_path):
        raise HTTPException(404, f"Video file not found: {video_path}")
    return await orch.ingest_video(video_path, video_id)


# ─── Search ──────────────────────────────────────────────────────────────

@router.get("/search")
async def search_videos(
    q: str,
    video_id: Optional[str] = None,
    mode: str = "hybrid",
    limit: int = 10,
):
    """
    Search indexed videos using text queries.

    Modes:
      - hybrid: Searches both transcripts AND visual keyframes, merged & ranked
      - transcript: Semantic text search over transcripts (sentence-transformer)
      - visual: CLIP text→image search over keyframes (finds visual matches)

    Returns ranked results with timestamps, match sources, and confidence scores.
    """
    orch = get_orchestrator()
    return await orch.search(q, video_id, mode, limit)


@router.post("/search")
async def search_videos_post(req: SearchRequest):
    """Search (POST variant for complex queries)."""
    orch = get_orchestrator()
    return await orch.search(req.query, req.video_id, req.mode, req.limit)


# ─── Synthesize (Search + Enrich + LLM Answer) ──────────────────────────

@router.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    """
    The flagship endpoint: search + enrich + synthesize into a cited answer.

    Pipeline:
      1. Search Weaviate (hybrid/transcript/visual)
      2. Pull stored enrichments for matched segments
      3. Optionally call Parallel.ai fresh for the specific question
      4. Local Qwen2.5-7B (or paid fallback) synthesizes a cited narrative

    Returns a human-readable answer with timestamps and source citations,
    not raw JSON. This is what end users should call.

    Billed: $0.03 per synthesis via x402.
    """
    orch = get_orchestrator()
    result = await orch.synthesize(
        req.question, req.video_id, req.search_mode, req.enrich_on_demand, req.limit,
    )
    return {
        "status": result.status.value,
        "data": result.data,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }


@router.get("/synthesize")
async def synthesize_get(
    q: str,
    video_id: Optional[str] = None,
    mode: str = "hybrid",
    enrich: bool = True,
):
    """Synthesize (GET variant for simple queries)."""
    orch = get_orchestrator()
    result = await orch.synthesize(q, video_id, mode, enrich)
    return {
        "status": result.status.value,
        "data": result.data,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }


# ─── Clip Extraction ────────────────────────────────────────────────────

@router.post("/clip")
async def extract_clip(req: ClipRequest):
    """
    Extract a video clip between start and end timestamps.

    The video must have been ingested via /ingest first (or provide source_path).
    Returns the clip file path; use /clip/download to get the file.
    """
    orch = get_orchestrator()
    clip_path = await orch.extract_clip(
        req.video_id, req.start_seconds, req.end_seconds, req.source_path,
    )
    if clip_path is None:
        raise HTTPException(404, "Clip extraction failed — video not found or FFmpeg error")
    return {"clip_path": clip_path, "start": req.start_seconds, "end": req.end_seconds}


@router.get("/clip/download/{video_id}")
async def download_clip(video_id: str, start: float, end: float):
    """
    Download a clip as an MP4 file.

    Extracts on-the-fly and streams back to the caller.
    """
    orch = get_orchestrator()
    clip_path = await orch.extract_clip(video_id, start, end)
    if clip_path is None or not os.path.exists(clip_path):
        raise HTTPException(404, "Clip not available")
    return FileResponse(clip_path, media_type="video/mp4", filename=os.path.basename(clip_path))


# ─── Existing Endpoints (unchanged) ─────────────────────────────────────

@router.post("/process")
async def process_video(req: ProcessRequest):
    """Process pre-segmented video data: vision + enrichment + x402 billing."""
    orch = get_orchestrator()
    return await orch.process_video(req.video_id, req.segments)


@router.post("/qa")
async def video_qa(req: QARequest):
    """Ask a question about video content with web-verified answers. Billed via x402."""
    orch = get_orchestrator()
    result = await orch.ask(req.question, req.video_id, req.verify_with_web)
    return {
        "status": result.status.value, "data": result.data,
        "error": result.error, "duration_ms": result.duration_ms,
    }


@router.post("/enrich")
async def enrich_segment(req: EnrichRequest):
    """Enrich a single segment with Parallel.ai web context. Billed via x402."""
    orch = get_orchestrator()
    agent = orch.enrichment_agent
    result = await agent.run_with_timeout(req.model_dump(), timeout=orch.config.agent_timeout_seconds)
    return {
        "status": result.status.value, "data": result.data,
        "error": result.error, "duration_ms": result.duration_ms,
    }


@router.get("/status")
async def status():
    """Agent status + Weaviate connection + x402 spend summary."""
    return get_orchestrator().get_status()


@router.get("/payments")
async def payments(limit: int = 50):
    """x402 payment ledger — real transaction history."""
    return {"ledger": get_orchestrator().get_payment_ledger(limit)}


@router.get("/payments/daily")
async def daily_spend():
    """Today's x402 spend vs. guardrail limits."""
    return get_orchestrator().payments.get_daily_spend()


@router.get("/metrics")
async def metrics(limit: int = 100):
    """Opik observability metrics — Opus planning decisions, latencies, costs."""
    from ..agents.observability import get_metrics
    return {"metrics": get_metrics(limit)}


@router.get("/eval")
async def evaluation_summary():
    """Evaluation summary — Opus planning efficiency and synthesis quality scores."""
    from ..agents.observability import VidExEvaluator
    evaluator = VidExEvaluator()
    return evaluator.get_summary()
