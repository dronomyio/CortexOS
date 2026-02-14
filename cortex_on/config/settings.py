"""
VidEx Configuration
===================
Open-source first, paid configurable fallbacks.
"""
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class VisionBackend(Enum):
    """Vision model priority: open-source first, paid fallback."""
    QWEN25_VL = "qwen2.5-vl"          # Best open-source, 7B runs on RTX 3070
    VIDEO_LLAVA = "video-llava"         # HuggingFace native, 7B
    VIDEOLLAMA2 = "videollama2"         # Good temporal understanding
    LLAVA_NEXT_VIDEO = "llava-next"     # Strong zero-shot video
    # Paid fallbacks (only used if configured)
    GPT4O = "gpt-4o"
    GEMINI_PRO_VISION = "gemini-pro"
    CLAUDE_SONNET = "claude-sonnet"


@dataclass
class VisionConfig:
    """Vision model configuration — open source by default."""
    # Primary: local open-source model
    backend: VisionBackend = VisionBackend.QWEN25_VL
    model_id: str = "Qwen/Qwen2.5-VL-7B-Instruct"
    device: str = "cuda"        # cuda | cpu | mps
    torch_dtype: str = "float16"  # float16 | bfloat16 | float32
    max_frames: int = 32        # Frames sampled per segment
    max_new_tokens: int = 512
    # Paid fallback (only if PARALLEL_VISION_PAID_FALLBACK=true)
    paid_fallback_enabled: bool = field(
        default_factory=lambda: os.environ.get("VISION_PAID_FALLBACK", "false").lower() == "true"
    )
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    paid_backend: VisionBackend = VisionBackend.GPT4O


@dataclass
class ParallelAIConfig:
    """Parallel.ai API configuration."""
    api_key: str = field(default_factory=lambda: os.environ.get("PARALLEL_API_KEY", ""))
    search_endpoint: str = "https://api.parallel.ai/v1beta/search"
    extract_endpoint: str = "https://api.parallel.ai/v1beta/extract"
    task_endpoint: str = "https://api.parallel.ai/v1beta/task"
    beta_header: str = "search-extract-2025-10-10"
    max_results: int = 10
    max_chars_per_result: int = 10000


@dataclass
class X402Config:
    """x402 micropayment configuration — real payment flows."""
    enabled: bool = field(
        default_factory=lambda: os.environ.get("X402_ENABLED", "true").lower() == "true"
    )
    server_url: str = field(
        default_factory=lambda: os.environ.get("X402_SERVER_URL", "http://localhost:8402")
    )
    wallet_id: str = field(default_factory=lambda: os.environ.get("CIRCLE_WALLET_ID", ""))
    # Guardrails
    max_payment_usdc: float = 1.0       # Max single payment
    daily_limit_usdc: float = 50.0      # Daily spend cap
    # Pricing schedule
    price_per_minute_video: float = 0.10
    price_per_enrichment_query: float = 0.01
    price_per_qa_question: float = 0.05
    price_per_insight_event: float = 0.005
    price_per_report: float = 0.25
    price_per_synthesis: float = 0.03   # Per synthesized answer


@dataclass
class WeaviateConfig:
    host: str = field(default_factory=lambda: os.environ.get("WEAVIATE_HOST", "localhost"))
    port: int = 8080
    grpc_port: int = 50051


@dataclass
class TextGenConfig:
    """Local text generation model configuration."""
    model_id: str = "Qwen/Qwen2.5-7B-Instruct"
    local_path: str = field(
        default_factory=lambda: os.environ.get("TEXT_MODEL_PATH", "Qwen/Qwen2.5-7B-Instruct")
    )
    device: str = "cuda"
    torch_dtype: str = "float16"
    max_new_tokens: int = 1024
    paid_fallback: bool = field(
        default_factory=lambda: os.environ.get("TEXT_PAID_FALLBACK", "false").lower() == "true"
    )
    paid_model: str = "claude-opus-4-6"


@dataclass
class AppConfig:
    """Master configuration."""
    vision: VisionConfig = field(default_factory=VisionConfig)
    parallel: ParallelAIConfig = field(default_factory=ParallelAIConfig)
    x402: X402Config = field(default_factory=X402Config)
    weaviate: WeaviateConfig = field(default_factory=WeaviateConfig)
    text_gen: TextGenConfig = field(default_factory=TextGenConfig)
    max_concurrent_agents: int = 4
    agent_timeout_seconds: int = 120
    segment_duration_seconds: int = 60
