"""
VidEx Observability — Opik Integration
========================================

Traces every Opus 4.6 decision and AI operation for evaluation:

  1. Opus Planning Traces:
     - Ingest plans: which segments got vision/enrichment and why
     - Synthesis plans: strategy chosen, confidence, additional searches
     - Adaptive analysis: discrepancies found, risk scores

  2. Model Traces:
     - TextGenerator: prompt, response, latency, backend (local vs paid)
     - VisionEngine: frames analyzed, entities extracted, latency
     - Whisper: segment duration, transcription latency

  3. Pipeline Traces:
     - End-to-end ingest: total time, segments, cost
     - End-to-end synthesis: search + plan + generate, cost
     - x402 payments: amount, status, tx_hash

  4. Evaluation Metrics:
     - Planning efficiency: % of segments where Opus saved work
     - Synthesis quality: citations count, source coverage
     - Discrepancy detection rate
     - Cost per query

Usage:
    from .observability import init_opik, trace_opus_plan, trace_synthesis, trace_ingest

    # At startup
    init_opik()

    # Decorating functions
    @trace_opus_plan
    async def plan_ingest(segments): ...

    @trace_synthesis
    async def synthesize(question, results): ...
"""
from __future__ import annotations

import functools
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────────────────

OPIK_ENABLED = os.getenv("OPIK_ENABLED", "true").lower() in ("true", "1", "yes")
OPIK_URL = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
OPIK_WORKSPACE = os.getenv("OPIK_WORKSPACE", "default")
OPIK_PROJECT = os.getenv("OPIK_PROJECT_NAME", "videx")

_opik_available = False
_opik_initialized = False
_metrics_store: List[Dict] = []  # In-memory metrics for /api/v1/metrics


def init_opik() -> bool:
    """Initialize Opik tracing at app startup."""
    global _opik_available, _opik_initialized

    if _opik_initialized:
        return _opik_available

    if not OPIK_ENABLED:
        logger.info("Opik disabled via OPIK_ENABLED=false")
        _opik_initialized = True
        return False

    try:
        os.environ["OPIK_URL_OVERRIDE"] = OPIK_URL
        os.environ["OPIK_WORKSPACE"] = OPIK_WORKSPACE
        os.environ["OPIK_PROJECT_NAME"] = OPIK_PROJECT

        import opik
        opik.configure(use_local=True)
        _opik_available = True
        _opik_initialized = True
        logger.info(f"Opik initialized — project={OPIK_PROJECT}, url={OPIK_URL}")
        return True
    except ImportError:
        logger.warning("Opik not installed. pip install opik")
        _opik_initialized = True
        return False
    except Exception as e:
        logger.warning(f"Opik init failed: {e}")
        _opik_initialized = True
        return False


def _get_tracker(name: str):
    """Get @track decorator or identity if Opik unavailable."""
    if _opik_available:
        try:
            from opik import track
            return track(name=name, project_name=OPIK_PROJECT)
        except Exception:
            pass
    return lambda fn: fn


def _record_metric(name: str, value: Any, tags: Optional[Dict] = None):
    """Record a metric to in-memory store + Opik if available."""
    entry = {
        "name": name,
        "value": value,
        "timestamp": time.time(),
        "tags": tags or {},
    }
    _metrics_store.append(entry)

    # Keep last 10000 entries
    if len(_metrics_store) > 10000:
        _metrics_store.pop(0)


def get_metrics(limit: int = 100) -> List[Dict]:
    """Return recent metrics for /api/v1/metrics endpoint."""
    return _metrics_store[-limit:]


# ─── Opus Planning Traces ────────────────────────────────────────────────

def trace_opus_plan(fn: Callable) -> Callable:
    """
    Trace Opus 4.6 planning decisions.

    Captures:
    - Input: segments or question + search results
    - Output: plan decisions (vision/enrichment/strategy)
    - Metrics: planning latency, segments skipped, strategy chosen
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        fn_name = fn.__name__

        try:
            result = await fn(*args, **kwargs)
            latency_ms = (time.perf_counter() - start) * 1000

            # Extract planning metrics based on function
            if fn_name == "plan_ingest" and isinstance(result, list):
                vision_count = sum(1 for p in result if getattr(p, 'vision_analysis', True))
                enrich_count = sum(1 for p in result if getattr(p, 'enrichment', True))
                total = len(result)
                skip_rate = 1 - (vision_count / max(total, 1))

                _record_metric("opus.ingest_plan.latency_ms", latency_ms)
                _record_metric("opus.ingest_plan.total_segments", total)
                _record_metric("opus.ingest_plan.vision_segments", vision_count)
                _record_metric("opus.ingest_plan.enrich_segments", enrich_count)
                _record_metric("opus.ingest_plan.skip_rate", round(skip_rate, 3))

                logger.info(
                    f"[opik] plan_ingest: {total} segments, "
                    f"{vision_count} vision, {enrich_count} enrich, "
                    f"skip_rate={skip_rate:.1%}, {latency_ms:.0f}ms"
                )

            elif fn_name == "plan_synthesis":
                plan = result
                _record_metric("opus.synthesis_plan.latency_ms", latency_ms)
                _record_metric("opus.synthesis_plan.strategy", getattr(plan, 'strategy', 'unknown'))
                _record_metric("opus.synthesis_plan.confidence", getattr(plan, 'confidence', 'unknown'))
                _record_metric("opus.synthesis_plan.additional_searches", len(getattr(plan, 'additional_searches', [])))
                _record_metric("opus.synthesis_plan.needs_enrichment", getattr(plan, 'needs_fresh_enrichment', False))
                _record_metric("opus.synthesis_plan.conflict_detected", getattr(plan, 'conflict_detected', False))

                logger.info(
                    f"[opik] plan_synthesis: strategy={getattr(plan, 'strategy', '?')}, "
                    f"confidence={getattr(plan, 'confidence', '?')}, {latency_ms:.0f}ms"
                )

            elif fn_name == "analyze_segment_deep":
                analysis = result
                risk = getattr(analysis, 'risk_score', 0)
                discrepancies = len(getattr(analysis, 'discrepancies', []))
                claims = len(getattr(analysis, 'claims_to_verify', []))

                _record_metric("opus.adaptive.latency_ms", latency_ms)
                _record_metric("opus.adaptive.risk_score", risk)
                _record_metric("opus.adaptive.discrepancies", discrepancies)
                _record_metric("opus.adaptive.claims_found", claims)

                if risk > 0.5:
                    logger.warning(f"[opik] HIGH RISK segment: score={risk}, discrepancies={discrepancies}")

            # Opik trace if available
            if _opik_available:
                try:
                    import opik
                    opik.track(
                        name=f"opus.{fn_name}",
                        project_name=OPIK_PROJECT,
                    )(lambda: result)()
                except Exception:
                    pass

            return result

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            _record_metric(f"opus.{fn_name}.error", str(e))
            _record_metric(f"opus.{fn_name}.latency_ms", latency_ms)
            raise

    return wrapper


# ─── Text Generation Traces ─────────────────────────────────────────────

def trace_generation(fn: Callable) -> Callable:
    """
    Trace LLM text generation (local Qwen or paid Opus/GPT).

    Captures:
    - Input: system prompt length, user prompt length
    - Output: response length, backend used
    - Metrics: latency, tokens estimated, cost
    """
    @functools.wraps(fn)
    async def wrapper(self, *args, **kwargs):
        start = time.perf_counter()

        # Capture input
        system = kwargs.get("system", args[0] if args else "")
        user = kwargs.get("user", args[1] if len(args) > 1 else "")

        try:
            result = await fn(self, *args, **kwargs)
            latency_ms = (time.perf_counter() - start) * 1000

            backend = getattr(self, '_backend', 'unknown')
            input_chars = len(system) + len(user)
            output_chars = len(result) if isinstance(result, str) else 0

            _record_metric("textgen.latency_ms", latency_ms, {"backend": backend})
            _record_metric("textgen.input_chars", input_chars, {"backend": backend})
            _record_metric("textgen.output_chars", output_chars, {"backend": backend})
            _record_metric("textgen.backend", backend)

            # Estimate tokens (~4 chars per token)
            est_input_tokens = input_chars // 4
            est_output_tokens = output_chars // 4
            _record_metric("textgen.est_input_tokens", est_input_tokens)
            _record_metric("textgen.est_output_tokens", est_output_tokens)

            logger.info(
                f"[opik] generate: backend={backend}, "
                f"in={est_input_tokens}tok, out={est_output_tokens}tok, "
                f"{latency_ms:.0f}ms"
            )

            # Opik span
            if _opik_available:
                try:
                    import opik
                    opik.track(
                        name="textgen.generate",
                        project_name=OPIK_PROJECT,
                    )(lambda: {"response_length": output_chars, "backend": backend, "latency_ms": latency_ms})()
                except Exception:
                    pass

            return result

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            _record_metric("textgen.error", str(e))
            _record_metric("textgen.latency_ms", latency_ms)
            raise

    return wrapper


# ─── Synthesis Traces ────────────────────────────────────────────────────

def trace_synthesis(fn: Callable) -> Callable:
    """
    Trace end-to-end synthesis pipeline.

    Captures:
    - Input: question, search result count, enrichment count
    - Output: synthesis length, citation count, strategy used
    - Metrics: total latency, cost, quality indicators
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()

        try:
            result = await fn(*args, **kwargs)
            latency_ms = (time.perf_counter() - start) * 1000

            # Extract from AgentResult
            data = getattr(result, 'data', {}) or {}
            synthesis = data.get("synthesis", "")
            sources = data.get("sources", [])
            strategy = data.get("synthesis_strategy", "unknown")
            backend = data.get("generator_backend", "unknown")
            plan = data.get("opus_plan", {})
            payment = data.get("payment", {})

            # Quality metrics
            citation_count = synthesis.count("[") // 2  # Rough [1], [2] count
            synthesis_length = len(synthesis)
            has_timestamps = any(c in synthesis for c in ["s,", "s.", "s:", ":00", ":30"])

            _record_metric("synthesis.latency_ms", latency_ms)
            _record_metric("synthesis.length_chars", synthesis_length)
            _record_metric("synthesis.citation_count", citation_count)
            _record_metric("synthesis.source_count", len(sources))
            _record_metric("synthesis.has_timestamps", has_timestamps)
            _record_metric("synthesis.strategy", strategy)
            _record_metric("synthesis.backend", backend)
            _record_metric("synthesis.confidence", plan.get("confidence_assessment", "unknown"))
            _record_metric("synthesis.cost_usdc", payment.get("amount_usdc", 0))

            logger.info(
                f"[opik] synthesis: strategy={strategy}, "
                f"{citation_count} citations, {len(sources)} sources, "
                f"confidence={plan.get('confidence_assessment', '?')}, "
                f"{latency_ms:.0f}ms, ${payment.get('amount_usdc', 0)}"
            )

            return result

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            _record_metric("synthesis.error", str(e))
            _record_metric("synthesis.latency_ms", latency_ms)
            raise

    return wrapper


# ─── Ingest Pipeline Traces ──────────────────────────────────────────────

def trace_ingest(fn: Callable) -> Callable:
    """
    Trace end-to-end video ingestion.

    Captures:
    - Input: video duration, segment count
    - Output: plan summary, enrichments, discrepancies, cost
    - Metrics: processing time per segment, skip rate, total cost
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()

        try:
            result = await fn(*args, **kwargs)
            latency_ms = (time.perf_counter() - start) * 1000

            if isinstance(result, dict):
                segments = result.get("segments_count", 0)
                duration = result.get("total_duration_seconds", 0)
                plan = result.get("opus_plan", {})
                discrepancies = result.get("discrepancies_found", 0)
                high_risk = len(result.get("high_risk_segments", []))
                enrichments = result.get("enrichments", 0)
                payment = result.get("payment", {})

                _record_metric("ingest.latency_ms", latency_ms)
                _record_metric("ingest.video_duration_s", duration)
                _record_metric("ingest.segments", segments)
                _record_metric("ingest.vision_planned", plan.get("vision_planned", segments))
                _record_metric("ingest.enrichment_planned", plan.get("enrichment_planned", segments))
                _record_metric("ingest.clip_worthy", plan.get("clip_worthy", 0))
                _record_metric("ingest.discrepancies", discrepancies)
                _record_metric("ingest.high_risk_segments", high_risk)
                _record_metric("ingest.enrichments_completed", enrichments)
                _record_metric("ingest.cost_usdc", payment.get("amount_usdc", 0))

                # Efficiency: how much work did Opus save?
                if segments > 0:
                    vision_rate = plan.get("vision_planned", segments) / segments
                    enrich_rate = plan.get("enrichment_planned", segments) / segments
                    _record_metric("ingest.vision_rate", round(vision_rate, 3))
                    _record_metric("ingest.enrich_rate", round(enrich_rate, 3))
                    _record_metric("ingest.opus_savings_pct",
                                   round((1 - (vision_rate + enrich_rate) / 2) * 100, 1))

                logger.info(
                    f"[opik] ingest: {segments} segments, {duration:.0f}s video, "
                    f"{discrepancies} discrepancies, {high_risk} high-risk, "
                    f"${payment.get('amount_usdc', 0)}, {latency_ms:.0f}ms"
                )

            return result

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            _record_metric("ingest.error", str(e))
            raise

    return wrapper


# ─── x402 Payment Traces ────────────────────────────────────────────────

def trace_payment(fn: Callable) -> Callable:
    """Trace x402 micropayments."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()

        try:
            result = await fn(*args, **kwargs)
            latency_ms = (time.perf_counter() - start) * 1000

            action = getattr(result, 'action', 'unknown')
            amount = getattr(result, 'amount_usdc', 0)
            status = getattr(result, 'status', 'unknown')

            _record_metric("x402.latency_ms", latency_ms, {"action": action})
            _record_metric("x402.amount_usdc", amount, {"action": action})
            _record_metric("x402.status", status, {"action": action})

            return result

        except Exception as e:
            _record_metric("x402.error", str(e))
            raise

    return wrapper


# ─── Vision Traces ───────────────────────────────────────────────────────

def trace_vision(fn: Callable) -> Callable:
    """Trace vision model analysis."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()

        try:
            result = await fn(*args, **kwargs)
            latency_ms = (time.perf_counter() - start) * 1000

            if hasattr(result, 'to_dict'):
                data = result.to_dict()
                entities = len(data.get("entities", []))
                claims = len(data.get("claims", []))
                _record_metric("vision.latency_ms", latency_ms)
                _record_metric("vision.entities_found", entities)
                _record_metric("vision.claims_found", claims)

            return result

        except Exception as e:
            _record_metric("vision.error", str(e))
            raise

    return wrapper


# ─── Evaluation Scoring ──────────────────────────────────────────────────

class VidExEvaluator:
    """
    Evaluates Opus 4.6 output quality for the hackathon.

    Run this against a set of test videos + questions to measure:
    1. Planning efficiency — does Opus skip work that doesn't add value?
    2. Discrepancy detection — does it catch speech vs visual mismatches?
    3. Synthesis quality — are answers cited, timestamped, accurate?
    4. Cost efficiency — USDC spent per useful insight
    """

    def __init__(self):
        self.eval_results: List[Dict] = []

    def score_ingest(self, ingest_result: Dict) -> Dict:
        """Score an ingest pipeline run."""
        segments = ingest_result.get("segments_count", 0)
        plan = ingest_result.get("opus_plan", {})
        discrepancies = ingest_result.get("discrepancies_found", 0)
        high_risk = len(ingest_result.get("high_risk_segments", []))
        duration = ingest_result.get("pipeline_duration_seconds", 0)
        cost = ingest_result.get("payment", {}).get("amount_usdc", 0)

        # Efficiency: did Opus skip unnecessary work?
        vision_rate = plan.get("vision_planned", segments) / max(segments, 1)
        enrich_rate = plan.get("enrichment_planned", segments) / max(segments, 1)
        savings = 1 - (vision_rate + enrich_rate) / 2

        # Quality: did it find problems?
        detection_score = min(discrepancies + high_risk, 10) / 10  # Normalize to 0-1

        # Speed: seconds per segment
        speed = duration / max(segments, 1)

        score = {
            "planning_efficiency": round(savings, 3),
            "detection_score": round(detection_score, 3),
            "speed_per_segment_s": round(speed, 2),
            "cost_usdc": cost,
            "segments": segments,
            "discrepancies": discrepancies,
            "high_risk_segments": high_risk,
            "clip_worthy": plan.get("clip_worthy", 0),
        }

        self.eval_results.append({"type": "ingest", **score})
        return score

    def score_synthesis(self, synthesis_result: Dict) -> Dict:
        """Score a synthesis output."""
        data = synthesis_result if isinstance(synthesis_result, dict) else {}
        synthesis = data.get("synthesis", "")
        sources = data.get("sources", [])
        plan = data.get("opus_plan", {})
        payment = data.get("payment", {})

        # Citation quality
        citation_count = synthesis.count("[1]") + synthesis.count("[2]") + synthesis.count("[3]")
        has_timestamps = bool(any(
            marker in synthesis
            for marker in [" at ", "s,", "s.", ":00", ":30", "second"]
        ))

        # Completeness
        has_sources = len(sources) > 0
        has_verdict = any(
            word in synthesis.lower()
            for word in ["however", "confirms", "contradicts", "misleading",
                         "verified", "unverifiable", "incorrect", "accurate"]
        )

        # Strategy appropriateness (higher = better)
        strategy = plan.get("synthesis_strategy", "direct")
        confidence = plan.get("confidence_assessment", "medium")
        conflict = plan.get("conflict_detected", False)

        # Investigative strategy when conflicts detected = good
        strategy_appropriate = (
            (conflict and strategy == "investigative") or
            (not conflict and strategy in ("direct", "comparative")) or
            (confidence == "low" and strategy != "direct")
        )

        quality_score = (
            (0.3 if citation_count >= 2 else 0.1 * citation_count) +
            (0.2 if has_timestamps else 0) +
            (0.2 if has_sources else 0) +
            (0.2 if has_verdict else 0) +
            (0.1 if strategy_appropriate else 0)
        )

        score = {
            "quality_score": round(quality_score, 3),
            "citation_count": citation_count,
            "has_timestamps": has_timestamps,
            "has_sources": has_sources,
            "has_verdict": has_verdict,
            "strategy": strategy,
            "strategy_appropriate": strategy_appropriate,
            "confidence": confidence,
            "conflict_detected": conflict,
            "synthesis_length": len(synthesis),
            "source_count": len(sources),
            "cost_usdc": payment.get("amount_usdc", 0),
        }

        self.eval_results.append({"type": "synthesis", **score})
        return score

    def get_summary(self) -> Dict:
        """Aggregate evaluation summary."""
        ingest_scores = [r for r in self.eval_results if r["type"] == "ingest"]
        synthesis_scores = [r for r in self.eval_results if r["type"] == "synthesis"]

        def avg(lst, key):
            vals = [r[key] for r in lst if key in r]
            return round(sum(vals) / max(len(vals), 1), 3)

        return {
            "total_evaluations": len(self.eval_results),
            "ingest": {
                "count": len(ingest_scores),
                "avg_planning_efficiency": avg(ingest_scores, "planning_efficiency"),
                "avg_detection_score": avg(ingest_scores, "detection_score"),
                "avg_speed_per_segment": avg(ingest_scores, "speed_per_segment_s"),
                "total_discrepancies": sum(r.get("discrepancies", 0) for r in ingest_scores),
                "total_cost_usdc": round(sum(r.get("cost_usdc", 0) for r in ingest_scores), 4),
            },
            "synthesis": {
                "count": len(synthesis_scores),
                "avg_quality_score": avg(synthesis_scores, "quality_score"),
                "avg_citations": avg(synthesis_scores, "citation_count"),
                "timestamp_rate": avg(synthesis_scores, "has_timestamps"),
                "verdict_rate": avg(synthesis_scores, "has_verdict"),
                "strategy_appropriate_rate": avg(synthesis_scores, "strategy_appropriate"),
                "total_cost_usdc": round(sum(r.get("cost_usdc", 0) for r in synthesis_scores), 4),
            },
        }


# ── Auto-Discovery ───────────────────────────────────────────────────────
def register_routes(app):
    """Auto-discovered by main.py — registers observability endpoints."""
    import os as _os

    @app.get("/api/v1/observability/metrics", tags=["observability"])
    async def observability_metrics():
        """Opus planning traces and pipeline metrics."""
        try:
            data = get_metrics(100)
            return {
                "agent": "observability",
                "status": "active",
                "opik_url": _os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api"),
                "project": _os.getenv("OPIK_PROJECT_NAME", "CortexVid"),
                "traces": data,
            }
        except Exception as e:
            return {"agent": "observability", "status": "error", "error": str(e)}

    @app.get("/api/v1/observability/eval", tags=["observability"])
    async def observability_eval():
        """Evaluation summary — planning efficiency and synthesis quality."""
        try:
            evaluator = VidExEvaluator()
            return {"agent": "observability", **evaluator.get_summary()}
        except Exception as e:
            return {"agent": "observability", "status": "error", "error": str(e)}

    @app.get("/api/v1/observability/config", tags=["observability"])
    async def observability_config():
        """Current observability configuration."""
        return {
            "opik_enabled": _os.getenv("OPIK_ENABLED", "false"),
            "opik_url": _os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api"),
            "opik_workspace": _os.getenv("OPIK_WORKSPACE", "default"),
            "opik_project": _os.getenv("OPIK_PROJECT_NAME", "CortexVid"),
        }

    print("[Observability] Registered routes: /api/v1/observability/metrics, /eval, /config")


# ── Auto-Discovery ───────────────────────────────────────────────────────
def register_routes(app):
    """Auto-discovered by main.py — registers observability endpoints."""
    import os as _os

    @app.get("/api/v1/observability/metrics", tags=["observability"])
    async def observability_metrics():
        """Opus planning traces and pipeline metrics."""
        try:
            data = get_metrics(100)
            return {
                "agent": "observability",
                "status": "active",
                "opik_url": _os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api"),
                "project": _os.getenv("OPIK_PROJECT_NAME", "CortexVid"),
                "traces": data,
            }
        except Exception as e:
            return {"agent": "observability", "status": "error", "error": str(e)}

    @app.get("/api/v1/observability/eval", tags=["observability"])
    async def observability_eval():
        """Evaluation summary — planning efficiency and synthesis quality."""
        try:
            evaluator = VidExEvaluator()
            return {"agent": "observability", **evaluator.get_summary()}
        except Exception as e:
            return {"agent": "observability", "status": "error", "error": str(e)}

    @app.get("/api/v1/observability/config", tags=["observability"])
    async def observability_config():
        """Current observability configuration."""
        return {
            "opik_enabled": _os.getenv("OPIK_ENABLED", "false"),
            "opik_url": _os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api"),
            "opik_workspace": _os.getenv("OPIK_WORKSPACE", "default"),
            "opik_project": _os.getenv("OPIK_PROJECT_NAME", "CortexVid"),
        }

    print("[Observability] Registered routes: /api/v1/observability/metrics, /eval, /config")
