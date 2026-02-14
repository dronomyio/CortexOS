"""
observability.py — Opik-based observability for ChartSeek/CortexON

Provides decorators and utilities for tracing AI operations:
- Whisper transcription
- CLIP image/text embedding  
- Weaviate vector search
- End-to-end search requests

Usage:
    from observability import trace_whisper, trace_clip_image, trace_clip_text, trace_search, init_opik

    # Initialize at app startup
    init_opik()

    # Decorate functions
    @trace_whisper
    def transcribe_segment(audio_path):
        ...

    @trace_clip_text
    def embed_text(text):
        ...
"""

from __future__ import annotations

import os
import time
import functools
from typing import Any, Callable, Dict, Optional
from contextlib import contextmanager

# ── Opik Configuration ───────────────────────────────────────────────────────

# Default configuration - can be overridden via environment variables
OPIK_ENABLED = os.getenv("OPIK_ENABLED", "true").lower() in ("true", "1", "yes")
OPIK_URL = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:8000/api")
OPIK_WORKSPACE = os.getenv("OPIK_WORKSPACE", "default")
OPIK_PROJECT = os.getenv("OPIK_PROJECT_NAME", "chartseek")

# Track whether opik is available
_opik_available = False
_opik_initialized = False


def init_opik() -> bool:
    """
    Initialize Opik tracing. Call this at application startup.
    Returns True if Opik is available and configured.
    """
    global _opik_available, _opik_initialized
    
    if _opik_initialized:
        return _opik_available
    
    if not OPIK_ENABLED:
        print("[observability] Opik disabled via OPIK_ENABLED=false")
        _opik_initialized = True
        return False
    
    try:
        # Set environment variables before importing opik
        os.environ["OPIK_URL_OVERRIDE"] = OPIK_URL
        os.environ["OPIK_WORKSPACE"] = OPIK_WORKSPACE
        os.environ["OPIK_PROJECT_NAME"] = OPIK_PROJECT
        
        import opik
        _opik_available = True
        _opik_initialized = True
        print(f"[observability] ✓ Opik initialized - project={OPIK_PROJECT}, url={OPIK_URL}")
        return True
    except ImportError:
        print("[observability] ⚠ Opik not installed. Install with: pip install opik")
        _opik_initialized = True
        return False
    except Exception as e:
        print(f"[observability] ⚠ Opik initialization failed: {e}")
        _opik_initialized = True
        return False


def _get_track_decorator(name: str, capture_input: bool = True, capture_output: bool = True):
    """Get the @track decorator if opik is available, otherwise return identity."""
    if not _opik_available:
        return lambda f: f
    
    try:
        from opik import track
        return track(name=name, capture_input=capture_input, capture_output=capture_output)
    except Exception:
        return lambda f: f


# ── Tracing Decorators ───────────────────────────────────────────────────────

def trace_whisper(func: Callable) -> Callable:
    """
    Decorator for Whisper transcription functions.
    Traces: input audio path, output text, latency.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not _opik_available:
            return func(*args, **kwargs)
        
        try:
            from opik import track
            
            @track(name="whisper_transcribe", capture_input=True, capture_output=True)
            def traced_func(*a, **kw):
                return func(*a, **kw)
            
            return traced_func(*args, **kwargs)
        except Exception:
            return func(*args, **kwargs)
    
    return wrapper


def trace_clip_image(func: Callable) -> Callable:
    """
    Decorator for CLIP image embedding functions.
    Traces: frame count, embedding dimensions, latency.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not _opik_available:
            return func(*args, **kwargs)
        
        try:
            from opik import track
            
            @track(name="clip_embed_image", capture_input=False, capture_output=False)
            def traced_func(*a, **kw):
                return func(*a, **kw)
            
            return traced_func(*args, **kwargs)
        except Exception:
            return func(*args, **kwargs)
    
    return wrapper


def trace_clip_text(func: Callable) -> Callable:
    """
    Decorator for CLIP text embedding functions.
    Traces: query text, embedding dimensions, latency.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not _opik_available:
            return func(*args, **kwargs)
        
        try:
            from opik import track
            
            @track(name="clip_embed_text", capture_input=True, capture_output=False)
            def traced_func(*a, **kw):
                return func(*a, **kw)
            
            return traced_func(*args, **kwargs)
        except Exception:
            return func(*args, **kwargs)
    
    return wrapper


def trace_search(func: Callable) -> Callable:
    """
    Decorator for Weaviate search functions.
    Traces: query, collection, top_k, hit count, latency.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not _opik_available:
            return func(*args, **kwargs)
        
        try:
            from opik import track
            
            @track(name="weaviate_search", capture_input=True, capture_output=True)
            def traced_func(*a, **kw):
                return func(*a, **kw)
            
            return traced_func(*args, **kwargs)
        except Exception:
            return func(*args, **kwargs)
    
    return wrapper


def trace_video_process(func: Callable) -> Callable:
    """
    Decorator for video processing pipeline.
    Traces: video_id, processing stages, total latency.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not _opik_available:
            return func(*args, **kwargs)
        
        try:
            from opik import track
            
            @track(name="video_process_pipeline", capture_input=True, capture_output=True)
            def traced_func(*a, **kw):
                return func(*a, **kw)
            
            return traced_func(*args, **kwargs)
        except Exception:
            return func(*args, **kwargs)
    
    return wrapper


# ── Context Manager for Manual Spans ─────────────────────────────────────────

@contextmanager
def trace_span(name: str, metadata: Optional[Dict[str, Any]] = None):
    """
    Context manager for creating manual trace spans.
    
    Usage:
        with trace_span("my_operation", {"key": "value"}) as span:
            # do work
            span.set_metadata({"result": "success"})
    """
    start_time = time.time()
    span_data = {"name": name, "metadata": metadata or {}}
    
    class SpanContext:
        def set_metadata(self, data: Dict[str, Any]):
            span_data["metadata"].update(data)
    
    ctx = SpanContext()
    
    try:
        if _opik_available:
            try:
                import opik
                # Use opik's span context if available
                with opik.start_span(name=name) as span:
                    if metadata:
                        span.set_metadata(metadata)
                    yield ctx
                    span.set_metadata({"latency_ms": (time.time() - start_time) * 1000})
                return
            except Exception:
                pass
        
        # Fallback: just yield the context
        yield ctx
        
    finally:
        latency_ms = (time.time() - start_time) * 1000
        if not _opik_available:
            # Log to console if opik not available
            print(f"[trace] {name}: {latency_ms:.2f}ms - {span_data['metadata']}")


# ── Metrics Collection ───────────────────────────────────────────────────────

class MetricsCollector:
    """
    Simple metrics collector for tracking operation statistics.
    Useful for evaluation and monitoring.
    """
    
    def __init__(self):
        self._metrics: Dict[str, list] = {}
    
    def record(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Record a metric value."""
        key = name
        if key not in self._metrics:
            self._metrics[key] = []
        self._metrics[key].append({
            "value": value,
            "tags": tags or {},
            "timestamp": time.time()
        })
    
    def get_stats(self, name: str) -> Dict[str, float]:
        """Get statistics for a metric."""
        if name not in self._metrics or not self._metrics[name]:
            return {}
        
        values = [m["value"] for m in self._metrics[name]]
        return {
            "count": len(values),
            "sum": sum(values),
            "mean": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
        }
    
    def clear(self, name: Optional[str] = None):
        """Clear metrics."""
        if name:
            self._metrics.pop(name, None)
        else:
            self._metrics.clear()


# Global metrics collector instance
metrics = MetricsCollector()


# ── Evaluation Helpers ───────────────────────────────────────────────────────

def evaluate_search_quality(
    query: str,
    results: list,
    expected_visual_hits: bool = True,
    min_confidence: float = 0.3
) -> Dict[str, Any]:
    """
    Evaluate search result quality.
    
    Returns:
        {
            "passed": bool,
            "visual_hits": int,
            "transcript_hits": int,
            "best_score": float,
            "issues": list[str]
        }
    """
    visual_hits = [r for r in results if getattr(r, 'source', '') == 'visual']
    transcript_hits = [r for r in results if getattr(r, 'source', '') == 'transcript']
    
    issues = []
    
    # Check visual hits
    has_visual = len(visual_hits) > 0
    if expected_visual_hits and not has_visual:
        issues.append("Expected visual hits but found none")
    
    # Check confidence
    best_score = 0.0
    if results:
        distances = [getattr(r, 'distance', 1.0) for r in results]
        best_score = 1.0 - min(distances) if distances else 0.0
        
        if best_score < min_confidence:
            issues.append(f"Best score {best_score:.3f} below threshold {min_confidence}")
    
    return {
        "passed": len(issues) == 0,
        "query": query,
        "visual_hits": len(visual_hits),
        "transcript_hits": len(transcript_hits),
        "best_score": best_score,
        "issues": issues,
    }


# ── Auto-initialization ──────────────────────────────────────────────────────

# Try to initialize on module import if enabled
if OPIK_ENABLED:
    init_opik()
