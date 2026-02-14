"""
Local Model Manager
===================

Manages local model lifecycle:
  - Download & cache models from HuggingFace
  - Load/unload for VRAM management
  - GPU memory tracking
  - Multi-GPU distribution

Supported models:

  Text Generation (for synthesis):
    - Qwen2.5-7B-Instruct (default) — same family as vision model, shared weights
    - Mistral-7B-Instruct-v0.3 — strong instruction following
    - Llama-3.1-8B-Instruct — Meta's latest

  Vision (managed by VisionEngine, listed here for VRAM tracking):
    - Qwen2.5-VL-7B-Instruct

  Embeddings (managed by WeaviateIndexer):
    - openai/clip-vit-base-patch32

Directory structure:
  ~/.videx/models/
    ├── qwen2.5-7b-instruct/
    ├── qwen2.5-vl-7b-instruct/
    ├── clip-vit-base-patch32/
    └── model_registry.json
"""
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ModelRole(Enum):
    """What the model is used for."""
    TEXT_GENERATION = "text_generation"   # Synthesis, QA answers
    VISION = "vision"                     # Video understanding
    EMBEDDING = "embedding"               # CLIP, sentence-transformers


class ModelStatus(Enum):
    NOT_DOWNLOADED = "not_downloaded"
    DOWNLOADED = "downloaded"
    LOADING = "loading"
    LOADED = "loaded"
    ERROR = "error"


@dataclass
class ModelInfo:
    """Metadata about a registered model."""
    model_id: str                          # HuggingFace ID
    name: str                              # Short display name
    role: ModelRole
    vram_gb: float                         # Estimated VRAM in float16
    description: str = ""
    local_path: Optional[str] = None       # Set after download
    status: ModelStatus = ModelStatus.NOT_DOWNLOADED
    is_default: bool = False

    def to_dict(self) -> Dict:
        return {
            "model_id": self.model_id,
            "name": self.name,
            "role": self.role.value,
            "vram_gb": self.vram_gb,
            "description": self.description,
            "local_path": self.local_path,
            "status": self.status.value,
            "is_default": self.is_default,
        }


# ─── Default Model Registry ─────────────────────────────────────────────

DEFAULT_MODELS = [
    ModelInfo(
        model_id="Qwen/Qwen2.5-7B-Instruct",
        name="qwen2.5-7b-instruct",
        role=ModelRole.TEXT_GENERATION,
        vram_gb=14.0,
        description="Qwen 2.5 7B text model — same family as vision model, strong reasoning and instruction following",
        is_default=True,
    ),
    ModelInfo(
        model_id="mistralai/Mistral-7B-Instruct-v0.3",
        name="mistral-7b-instruct",
        role=ModelRole.TEXT_GENERATION,
        vram_gb=14.0,
        description="Mistral 7B — excellent instruction following, fast inference",
    ),
    ModelInfo(
        model_id="meta-llama/Meta-Llama-3.1-8B-Instruct",
        name="llama-3.1-8b-instruct",
        role=ModelRole.TEXT_GENERATION,
        vram_gb=16.0,
        description="Llama 3.1 8B — Meta's latest, strong multilingual + reasoning",
    ),
    ModelInfo(
        model_id="Qwen/Qwen2.5-VL-7B-Instruct",
        name="qwen2.5-vl-7b",
        role=ModelRole.VISION,
        vram_gb=14.0,
        description="Qwen 2.5 VL — best open-source video understanding",
        is_default=True,
    ),
    ModelInfo(
        model_id="openai/clip-vit-base-patch32",
        name="clip-vit-base-patch32",
        role=ModelRole.EMBEDDING,
        vram_gb=0.6,
        description="CLIP ViT-B/32 — text-image embeddings for visual search",
        is_default=True,
    ),
]


class LocalModelManager:
    """
    Manages local model downloads, caching, and GPU memory tracking.

    Usage:
        manager = LocalModelManager()
        manager.scan_local()                        # Check what's already downloaded
        await manager.download("qwen2.5-7b-instruct")  # Download if needed
        info = manager.get_model_info("qwen2.5-7b-instruct")
        print(info.local_path)                      # ~/.videx/models/qwen2.5-7b-instruct/
    """

    def __init__(self, models_dir: Optional[str] = None):
        self.models_dir = models_dir or os.path.expanduser("~/.videx/models")
        os.makedirs(self.models_dir, exist_ok=True)

        self._registry: Dict[str, ModelInfo] = {}
        self._registry_path = os.path.join(self.models_dir, "model_registry.json")

        # Initialize with defaults
        for model in DEFAULT_MODELS:
            self._registry[model.name] = model

        # Load saved state
        self._load_registry()
        self.scan_local()

    # ─── Registry Persistence ────────────────────────────────────────────

    def _load_registry(self):
        """Load model registry from disk."""
        if os.path.exists(self._registry_path):
            try:
                with open(self._registry_path) as f:
                    saved = json.load(f)
                for name, data in saved.items():
                    if name in self._registry:
                        self._registry[name].local_path = data.get("local_path")
                        status = data.get("status", "not_downloaded")
                        self._registry[name].status = ModelStatus(status)
            except Exception as e:
                logger.warning(f"Failed to load model registry: {e}")

    def _save_registry(self):
        """Persist model registry to disk."""
        data = {
            name: {"local_path": info.local_path, "status": info.status.value}
            for name, info in self._registry.items()
        }
        with open(self._registry_path, "w") as f:
            json.dump(data, f, indent=2)

    # ─── Scanning ────────────────────────────────────────────────────────

    def scan_local(self):
        """Check which models are already downloaded locally."""
        for name, info in self._registry.items():
            expected_path = os.path.join(self.models_dir, name)
            if os.path.isdir(expected_path):
                # Check for model files (config.json or pytorch_model.bin or model.safetensors)
                has_config = os.path.exists(os.path.join(expected_path, "config.json"))
                has_weights = any(
                    os.path.exists(os.path.join(expected_path, f))
                    for f in ["pytorch_model.bin", "model.safetensors",
                              "pytorch_model.bin.index.json", "model.safetensors.index.json"]
                )
                if has_config or has_weights:
                    info.local_path = expected_path
                    if info.status != ModelStatus.LOADED:
                        info.status = ModelStatus.DOWNLOADED
                    logger.info(f"Found local model: {name} at {expected_path}")

        self._save_registry()

    # ─── Download ────────────────────────────────────────────────────────

    async def download(self, model_name: str, force: bool = False) -> ModelInfo:
        """
        Download a model from HuggingFace Hub.

        Uses snapshot_download for resumable, cached downloads.
        """
        if model_name not in self._registry:
            raise ValueError(f"Unknown model: {model_name}. Available: {list(self._registry.keys())}")

        info = self._registry[model_name]

        if info.status == ModelStatus.DOWNLOADED and not force:
            logger.info(f"Model {model_name} already downloaded at {info.local_path}")
            return info

        logger.info(f"Downloading {info.model_id}...")

        try:
            from huggingface_hub import snapshot_download

            local_dir = os.path.join(self.models_dir, model_name)
            os.makedirs(local_dir, exist_ok=True)

            # Run download in executor to not block event loop
            import asyncio
            loop = asyncio.get_event_loop()
            path = await loop.run_in_executor(
                None,
                lambda: snapshot_download(
                    info.model_id,
                    local_dir=local_dir,
                    local_dir_use_symlinks=False,
                ),
            )

            info.local_path = local_dir
            info.status = ModelStatus.DOWNLOADED
            self._save_registry()
            logger.info(f"Downloaded {model_name} to {local_dir}")
            return info

        except Exception as e:
            info.status = ModelStatus.ERROR
            self._save_registry()
            raise RuntimeError(f"Download failed for {model_name}: {e}")

    # ─── Queries ─────────────────────────────────────────────────────────

    def get_model_info(self, model_name: str) -> Optional[ModelInfo]:
        return self._registry.get(model_name)

    def get_default(self, role: ModelRole) -> Optional[ModelInfo]:
        """Get the default model for a given role."""
        for info in self._registry.values():
            if info.role == role and info.is_default:
                return info
        return None

    def list_models(self, role: Optional[ModelRole] = None) -> List[ModelInfo]:
        models = list(self._registry.values())
        if role:
            models = [m for m in models if m.role == role]
        return models

    def get_gpu_budget(self) -> Dict[str, Any]:
        """Estimate GPU VRAM usage for loaded/planned models."""
        try:
            import torch
            if torch.cuda.is_available():
                total_vram = torch.cuda.get_device_properties(0).total_mem / (1024**3)
                allocated = torch.cuda.memory_allocated(0) / (1024**3)
                reserved = torch.cuda.memory_reserved(0) / (1024**3)
            else:
                total_vram = 0
                allocated = 0
                reserved = 0
        except ImportError:
            total_vram = 0
            allocated = 0
            reserved = 0

        loaded_models = [m for m in self._registry.values() if m.status == ModelStatus.LOADED]
        planned_vram = sum(m.vram_gb for m in loaded_models)

        return {
            "gpu_total_gb": round(total_vram, 1),
            "gpu_allocated_gb": round(allocated, 1),
            "gpu_reserved_gb": round(reserved, 1),
            "loaded_models": [m.name for m in loaded_models],
            "loaded_vram_estimate_gb": round(planned_vram, 1),
            "available_gb": round(total_vram - allocated, 1),
        }

    # ─── Lifecycle ───────────────────────────────────────────────────────

    def mark_loaded(self, model_name: str):
        if model_name in self._registry:
            self._registry[model_name].status = ModelStatus.LOADED
            self._save_registry()

    def mark_unloaded(self, model_name: str):
        if model_name in self._registry:
            self._registry[model_name].status = ModelStatus.DOWNLOADED
            self._save_registry()

    def delete_model(self, model_name: str):
        """Delete a downloaded model to free disk space."""
        if model_name not in self._registry:
            return
        info = self._registry[model_name]
        if info.local_path and os.path.isdir(info.local_path):
            shutil.rmtree(info.local_path)
            logger.info(f"Deleted model: {model_name}")
        info.local_path = None
        info.status = ModelStatus.NOT_DOWNLOADED
        self._save_registry()
