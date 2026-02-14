"""
clip_embedding_service.py — CLIP-based embedding service for images AND text

Uses OpenAI's CLIP model to embed images and text into the same 512-dimensional
vector space, enabling cross-modal search (text query → image matches).

No OpenAI API key required — runs entirely locally using HuggingFace transformers.

Default model: openai/clip-vit-base-patch32 (512-d)
Override with env CLIP_MODEL or constructor arg.

Observability: Integrated with Opik for tracing (if available).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional, Union

import numpy as np
import torch
from PIL import Image

# Import observability (gracefully handle if not available)
try:
    from observability import trace_clip_image, trace_clip_text, metrics, trace_span
    OBSERVABILITY_ENABLED = True
except ImportError:
    OBSERVABILITY_ENABLED = False
    # Provide no-op decorators
    def trace_clip_image(f): return f
    def trace_clip_text(f): return f
    class DummyMetrics:
        def record(self, *args, **kwargs): pass
    metrics = DummyMetrics()
    from contextlib import contextmanager
    @contextmanager
    def trace_span(name, metadata=None):
        yield type('obj', (object,), {'set_metadata': lambda self, x: None})()

_SINGLETON: Optional["CLIPEmbeddingService"] = None


class CLIPEmbeddingService:
    """CLIP embedding service for both images and text."""

    def __init__(self, model_name: Optional[str] = None):
        from transformers import CLIPModel, CLIPProcessor

        self.model_name = model_name or os.getenv(
            "CLIP_MODEL", "openai/clip-vit-base-patch32"
        )
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        
        print(f"[clip_embedding_service] Loading model: {self.model_name} on {self.device}…")
        self.model = CLIPModel.from_pretrained(self.model_name).to(self.device).eval()
        self.processor = CLIPProcessor.from_pretrained(self.model_name)
        self.dimensions: int = 512  # CLIP ViT-B/32 outputs 512-d vectors
        print(f"[clip_embedding_service] ✓ Ready — dimensions={self.dimensions}")

    # ── Image embedding ──────────────────────────────────────────
    def embed_image(self, image: Union[str, Path, Image.Image]) -> List[float]:
        """Embed a single image. Accepts path or PIL Image."""
        start_time = time.time()
        
        if isinstance(image, (str, Path)):
            image = Image.open(image).convert("RGB")
        elif not isinstance(image, Image.Image):
            raise TypeError(f"Expected path or PIL Image, got {type(image)}")
        
        inputs = self.processor(images=image, return_tensors="pt").to(self.device)
        with torch.no_grad():
            feat = self.model.get_image_features(**inputs)
            feat = feat / feat.norm(dim=-1, keepdim=True)  # L2 normalize
        
        latency_ms = (time.time() - start_time) * 1000
        metrics.record("clip_image_embed_latency_ms", latency_ms)
        
        return feat.squeeze(0).cpu().numpy().tolist()

    def embed_images_batch(
        self, images: List[Union[str, Path, Image.Image]], batch_size: int = 16
    ) -> List[List[float]]:
        """Embed multiple images."""
        results: List[List[float]] = []
        
        for i in range(0, len(images), batch_size):
            batch = images[i:i + batch_size]
            pil_images = []
            for img in batch:
                if isinstance(img, (str, Path)):
                    pil_images.append(Image.open(img).convert("RGB"))
                else:
                    pil_images.append(img.convert("RGB") if img.mode != "RGB" else img)
            
            inputs = self.processor(images=pil_images, return_tensors="pt", padding=True).to(self.device)
            with torch.no_grad():
                feats = self.model.get_image_features(**inputs)
                # Handle different return types from transformers versions
                if hasattr(feats, 'pooler_output'):
                    feats = feats.pooler_output
                elif hasattr(feats, 'last_hidden_state'):
                    feats = feats.last_hidden_state[:, 0, :]
                feats = feats / feats.norm(dim=-1, keepdim=True)
            results.extend(feats.cpu().numpy().tolist())
        
        return results

    # ── Text embedding ───────────────────────────────────────────
    @trace_clip_text
    def embed_text(self, text: str) -> List[float]:
        """Embed a single text query for cross-modal search."""
        start_time = time.time()
        
        inputs = self.processor(text=[text], return_tensors="pt", padding=True, truncation=True).to(self.device)
        with torch.no_grad():
            feat = self.model.get_text_features(**inputs)
            # Handle different return types from transformers versions
            if hasattr(feat, 'pooler_output'):
                feat = feat.pooler_output
            elif hasattr(feat, 'last_hidden_state'):
                feat = feat.last_hidden_state[:, 0, :]
            feat = feat / feat.norm(dim=-1, keepdim=True)
        
        latency_ms = (time.time() - start_time) * 1000
        metrics.record("clip_text_embed_latency_ms", latency_ms, {"query_length": str(len(text))})
        
        return feat.squeeze(0).cpu().numpy().tolist()

    def embed_texts_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """Embed multiple text queries."""
        results: List[List[float]] = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            inputs = self.processor(text=batch, return_tensors="pt", padding=True, truncation=True).to(self.device)
            with torch.no_grad():
                feats = self.model.get_text_features(**inputs)
                # Handle different return types from transformers versions
                if hasattr(feats, 'pooler_output'):
                    feats = feats.pooler_output
                elif hasattr(feats, 'last_hidden_state'):
                    feats = feats.last_hidden_state[:, 0, :]
                feats = feats / feats.norm(dim=-1, keepdim=True)
            results.extend(feats.cpu().numpy().tolist())
        
        return results


def get_clip_embedding_service(model_name: Optional[str] = None) -> CLIPEmbeddingService:
    """Return (or create) the process-wide singleton."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = CLIPEmbeddingService(model_name)
    return _SINGLETON
