#!/bin/bash
# CortexOS — Append register_routes() to existing agent files
# Run from CortexVid root: bash apply_discovery_patches.sh
set -e

AGENTS_DIR="cortex_on/agents"

echo "═══ CortexOS Auto-Discovery Patches ═══"
echo ""

# ── 1. opus_planner.py ──────────────────────────────────────────────────
echo "[1/5] Patching opus_planner.py..."
cat >> "${AGENTS_DIR}/opus_planner.py" << 'PATCH_EOF'


# ── Auto-Discovery ───────────────────────────────────────────────────────
def register_routes(app):
    """Auto-discovered by main.py — registers Opus planner endpoints."""

    @app.get("/api/v1/planner/stats", tags=["opus-planner"])
    async def planner_stats():
        """Opus 4.6 planner statistics — plans generated, strategies chosen."""
        try:
            import os
            from models.text_generator import TextGenerator
            text_gen = TextGenerator(
                paid_model=os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
                use_paid_fallback=os.getenv("TEXT_PAID_FALLBACK", "false").lower() == "true",
            )
            planner = OpusPlanner(text_gen)
            return {
                "agent": "opus-planner",
                "status": "active",
                "model": os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
                **planner.get_stats(),
            }
        except Exception as e:
            return {"agent": "opus-planner", "status": "error", "error": str(e)}

    @app.get("/api/v1/planner/strategies", tags=["opus-planner"])
    async def planner_strategies():
        """List available synthesis strategies."""
        try:
            from agents.orchestrator import SYNTHESIS_STRATEGIES
            return {"strategies": list(SYNTHESIS_STRATEGIES.keys()), "count": len(SYNTHESIS_STRATEGIES)}
        except ImportError:
            return {"strategies": ["direct", "comparative", "investigative", "timeline"], "count": 4}

    print("[OpusPlanner] Registered routes: /api/v1/planner/stats, /planner/strategies")
PATCH_EOF
echo "  ✓ opus_planner.py patched"

# ── 2. observability.py ─────────────────────────────────────────────────
echo "[2/5] Patching observability.py..."
cat >> "${AGENTS_DIR}/observability.py" << 'PATCH_EOF'


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
PATCH_EOF
echo "  ✓ observability.py patched"

# ── 3. synthesis_agent.py ───────────────────────────────────────────────
echo "[3/5] Patching synthesis_agent.py..."
cat >> "${AGENTS_DIR}/synthesis_agent.py" << 'PATCH_EOF'


# ── Auto-Discovery ───────────────────────────────────────────────────────
def register_routes(app):
    """Auto-discovered by main.py — registers synthesis endpoints."""
    import os as _os

    @app.get("/api/v1/synthesis/strategies", tags=["synthesis"])
    async def synthesis_strategies():
        """Available synthesis strategies and descriptions."""
        try:
            from agents.orchestrator import SYNTHESIS_STRATEGIES
            return {
                "agent": "synthesis",
                "strategies": {
                    k: v[:100] + "..." if len(v) > 100 else v
                    for k, v in SYNTHESIS_STRATEGIES.items()
                },
            }
        except ImportError:
            return {"agent": "synthesis", "strategies": {}, "error": "orchestrator not available"}

    @app.get("/api/v1/synthesis/stats", tags=["synthesis"])
    async def synthesis_stats():
        """Synthesis agent status."""
        return {
            "agent": "synthesis",
            "status": "active",
            "model": _os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-6"),
        }

    print("[SynthesisAgent] Registered routes: /api/v1/synthesis/strategies, /synthesis/stats")
PATCH_EOF
echo "  ✓ synthesis_agent.py patched"

# ── 4. video_qa_agent.py ────────────────────────────────────────────────
echo "[4/5] Patching video_qa_agent.py..."
cat >> "${AGENTS_DIR}/video_qa_agent.py" << 'PATCH_EOF'


# ── Auto-Discovery ───────────────────────────────────────────────────────
def register_routes(app):
    """Auto-discovered by main.py — registers video QA endpoints."""
    from fastapi import Query as FQuery

    @app.get("/api/v1/qa/ask", tags=["video-qa"])
    async def qa_ask(
        question: str = FQuery(..., description="Question about indexed videos"),
        video_id: str = FQuery(None, description="Limit to specific video"),
        top_k: int = FQuery(5, ge=1, le=20),
    ):
        """Ask a question across indexed video content."""
        try:
            from config import ParallelAIConfig, X402Config
            from agents.parallel_client import ParallelClient
            from agents.x402_payment_agent import X402PaymentAgent

            payment_agent = X402PaymentAgent(X402Config())
            agent = VideoQAAgent(
                parallel_config=ParallelAIConfig(),
                payment_agent=payment_agent,
                weaviate_url=None,
            )
            result = await agent.execute({
                "question": question,
                "video_id": video_id,
                "top_k": top_k,
            })
            return result.data
        except ImportError as e:
            from fastapi import HTTPException
            raise HTTPException(503, f"VideoQAAgent deps not available: {e}")
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(500, f"QA failed: {e}")

    @app.get("/api/v1/qa/stats", tags=["video-qa"])
    async def qa_stats():
        """Video QA agent status."""
        return {"agent": "video-qa", "status": "active"}

    print("[VideoQAAgent] Registered routes: /api/v1/qa/ask, /qa/stats")
PATCH_EOF
echo "  ✓ video_qa_agent.py patched"

# ── 5. x402_payment_agent.py ────────────────────────────────────────────
echo "[5/5] Patching x402_payment_agent.py..."
cat >> "${AGENTS_DIR}/x402_payment_agent.py" << 'PATCH_EOF'


# ── Auto-Discovery ───────────────────────────────────────────────────────
_payment_instance = None

def _get_payment_agent():
    """Lazy singleton for x402 payment agent."""
    global _payment_instance
    if _payment_instance is None:
        try:
            from config import X402Config
            _payment_instance = X402PaymentAgent(X402Config())
        except ImportError:
            return None
    return _payment_instance


def register_routes(app):
    """Auto-discovered by main.py — registers x402 payment endpoints."""
    from fastapi import Query as FQuery

    @app.get("/api/v1/payments/stats", tags=["x402-payments"])
    async def payment_stats():
        """Daily spend, limits, and payment counters."""
        agent = _get_payment_agent()
        if not agent:
            return {"agent": "x402-payments", "status": "not configured"}
        return {
            "agent": "x402-payments",
            "status": "active",
            "daily_spend": agent.get_daily_spend(),
        }

    @app.get("/api/v1/payments/ledger", tags=["x402-payments"])
    async def payment_ledger(limit: int = FQuery(50, ge=1, le=500)):
        """Payment audit trail — all x402 transactions."""
        agent = _get_payment_agent()
        if not agent:
            return {"agent": "x402-payments", "status": "not configured", "records": []}
        return {
            "agent": "x402-payments",
            "records": agent.get_ledger(limit),
            "daily_spend": agent.get_daily_spend(),
        }

    @app.get("/api/v1/payments/guardrails", tags=["x402-payments"])
    async def payment_guardrails():
        """Guardrail config — max payment, daily limit, pricing schedule."""
        agent = _get_payment_agent()
        if not agent:
            return {"agent": "x402-payments", "status": "not configured"}
        return {
            "agent": "x402-payments",
            "max_payment_usdc": agent.config.max_payment_usdc,
            "daily_limit_usdc": agent.config.daily_limit_usdc,
            "pricing": {
                "per_minute_video": agent.config.price_per_minute_video,
                "per_enrichment_query": agent.config.price_per_enrichment_query,
                "per_qa_question": agent.config.price_per_qa_question,
                "per_report": agent.config.price_per_report,
                "per_synthesis": agent.config.price_per_synthesis,
            },
        }

    print("[X402PaymentAgent] Registered routes: /api/v1/payments/stats, /ledger, /guardrails")
PATCH_EOF
echo "  ✓ x402_payment_agent.py patched"

echo ""
echo "═══ All 5 agents patched ═══"
echo ""
echo "Restart to activate:"
echo "  docker-compose restart cortex_on"
echo ""
echo "Verify:"
echo "  curl -s http://localhost:8093/api/v1/agents | python3 -m json.tool"
echo "  curl -s http://localhost:8093/api/v1/health | python3 -m json.tool"
echo ""
echo "New endpoints:"
echo "  GET /api/v1/planner/stats"
echo "  GET /api/v1/planner/strategies"
echo "  GET /api/v1/observability/metrics"
echo "  GET /api/v1/observability/eval"
echo "  GET /api/v1/observability/config"
echo "  GET /api/v1/synthesis/strategies"
echo "  GET /api/v1/synthesis/stats"
echo "  GET /api/v1/qa/ask?question=..."
echo "  GET /api/v1/qa/stats"
echo "  GET /api/v1/payments/stats"
echo "  GET /api/v1/payments/ledger"
echo "  GET /api/v1/payments/guardrails"
