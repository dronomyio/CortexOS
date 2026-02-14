"""
embedding_service.py — Shared local embedding singleton

Loads a sentence-transformers model once per process and exposes
embed_text / embed_batch helpers used by ingest + query scripts.

No OpenAI dependency.  No API key required.

Default model: all-MiniLM-L6-v2  (384-d, fast, good quality)
Override with env EMBEDDING_MODEL or constructor arg.
"""

from __future__ import annotations

import os
from typing import List, Optional

import numpy as np

_SINGLETON: Optional["LocalEmbeddingService"] = None


class LocalEmbeddingService:
    """Thin wrapper around sentence-transformers with lazy init."""

    def __init__(self, model_name: Optional[str] = None):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name or os.getenv(
            "EMBEDDING_MODEL", "all-MiniLM-L6-v2"
        )
        print(f"[embedding_service] Loading model: {self.model_name} …")
        self.model = SentenceTransformer(self.model_name)
        self.dimensions: int = int(self.model.get_sentence_embedding_dimension())
        print(
            f"[embedding_service] ✓ Ready — dimensions={self.dimensions}"
        )

    # ── single text ──────────────────────────────────────────────
    def embed_text(self, text: str) -> List[float]:
        vec = self.model.encode(text, convert_to_numpy=True)
        return vec.tolist()

    # ── batch ────────────────────────────────────────────────────
    def embed_batch(
        self, texts: List[str], batch_size: int = 64
    ) -> List[List[float]]:
        vecs = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=True,
            convert_to_numpy=True,
        )
        return vecs.tolist()


def get_embedding_service(model_name: Optional[str] = None) -> LocalEmbeddingService:
    """Return (or create) the process-wide singleton."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = LocalEmbeddingService(model_name)
    return _SINGLETON
