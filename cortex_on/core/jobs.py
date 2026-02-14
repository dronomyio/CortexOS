"""
core/jobs.py — Single source of truth for video processing jobs.

Both FastAPI routes and MCP tools call these functions.
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── In-memory store (swap with MongoDB for production) ───────────────────
_jobs: Dict[str, Dict[str, Any]] = {}

DATA_DIR  = Path(os.getenv("DATA_DIR", "/data"))
UPLOADS   = DATA_DIR / "uploads"
OUT_DIR   = DATA_DIR / "out"
CLIPS_DIR = OUT_DIR / "clips"

for _d in (UPLOADS, OUT_DIR, CLIPS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def create_job(video_id: str, video_path: str) -> Dict[str, Any]:
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    j = dict(
        job_id=job_id,
        video_id=video_id,
        status="pending",
        progress=0.0,
        message="Queued",
        video_path=video_path,
        index_json=None,
        created_at=now,
        updated_at=now,
    )
    _jobs[job_id] = j
    return j


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _jobs.get(job_id)


def list_jobs() -> List[Dict[str, Any]]:
    return sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)


def find_ready_jobs(video_id: str) -> List[Dict[str, Any]]:
    return [j for j in _jobs.values() if j["video_id"] == video_id and j["status"] == "ready"]


def update_job(job_id: str, **kw) -> None:
    if job_id in _jobs:
        _jobs[job_id].update(**kw, updated_at=datetime.now(timezone.utc).isoformat())
