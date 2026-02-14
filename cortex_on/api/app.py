"""
VidEx Server
============

Video understanding API with search, clip extraction, and agent enrichment.

Start: uvicorn videx.api.app:app --reload --port 8001
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import router, init_orchestrator
from ..config import AppConfig

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize orchestrator, connect Weaviate, and teardown on shutdown."""
    config = AppConfig()
    orchestrator = init_orchestrator(config)
    await orchestrator.startup()

    logging.info(
        f"VidEx started | "
        f"Vision: {config.vision.backend.value} | "
        f"Weaviate: {'connected' if orchestrator.indexer.connected else 'unavailable'} | "
        f"Parallel.ai: {'configured' if config.parallel.api_key else 'no key'} | "
        f"x402: {'enabled' if config.x402.enabled else 'disabled'}"
    )
    yield
    await orchestrator.shutdown()
    logging.info("VidEx stopped")


app = FastAPI(
    title="VidEx API",
    description=(
        "Domain-agnostic video understanding with search, clip extraction, "
        "and AI-powered enrichment. Open-source vision models first, "
        "paid fallbacks configurable. Billed via x402 micropayments."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "videx",
        "capabilities": {
            "video_ingestion": "FFmpeg segment + Whisper transcribe + keyframe extract",
            "search": "Weaviate hybrid (transcript + CLIP visual)",
            "clip_extraction": "FFmpeg precise seek + cut",
            "vision": "Qwen2.5-VL / Video-LLaVA (open-source) + GPT-4o/Gemini fallback",
            "enrichment": "Parallel.ai web intelligence",
            "qa": "Video QA with web-verified answers",
            "payments": "x402 micropayments via Circle Wallets",
        },
    }
