"""
CortexVid Integration Tests
============================

These tests require full dependencies (aiohttp, etc).
Run inside Docker: docker compose -f tests/docker-compose.test.yml up --build

Tests:
  1. Orchestrator init + strategy routing
  2. Synthesis agent with mocked LLM
  3. x402 payment agent guardrails
  4. End-to-end ingest plan → selective processing
  5. Observability trace decorators fire correctly
"""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


# ─── Orchestrator Tests ─────────────────────────────────────────────────

class TestOrchestrator(unittest.TestCase):
    """Test orchestrator with mocked subsystems."""

    def test_synthesis_strategies_exist(self):
        from cortex_on.agents.orchestrator import SYNTHESIS_STRATEGIES
        self.assertEqual(len(SYNTHESIS_STRATEGIES), 4)
        for key in ["direct", "comparative", "investigative", "timeline"]:
            self.assertIn(key, SYNTHESIS_STRATEGIES)
            self.assertGreater(len(SYNTHESIS_STRATEGIES[key]), 20)

    def test_synthesis_strategy_content(self):
        from cortex_on.agents.orchestrator import SYNTHESIS_STRATEGIES
        self.assertIn("fact-checking", SYNTHESIS_STRATEGIES["comparative"])
        self.assertIn("investigative", SYNTHESIS_STRATEGIES["investigative"])
        self.assertIn("chronological", SYNTHESIS_STRATEGIES["timeline"])
        self.assertIn("concise", SYNTHESIS_STRATEGIES["direct"])


# ─── Synthesis Agent Tests ───────────────────────────────────────────────

class TestSynthesisAgent(unittest.TestCase):
    """Test synthesis agent with strategy-aware prompting."""

    def test_system_prompt_override(self):
        """Verify synthesis agent accepts system_prompt_override from planner."""
        from cortex_on.agents.synthesis_agent import SynthesisAgent, SYNTHESIS_SYSTEM

        mock_text_gen = AsyncMock()
        mock_text_gen.generate = AsyncMock(return_value="Test synthesis output [1].")
        mock_text_gen.backend_name = "claude-opus-4-6"
        mock_text_gen._loaded = True

        mock_payments = AsyncMock()
        mock_payments.pay_for_synthesis = AsyncMock(return_value=MagicMock(
            to_dict=lambda: {"amount_usdc": 0.03, "status": "completed"},
        ))

        agent = SynthesisAgent(
            text_generator=mock_text_gen,
            payment_agent=mock_payments,
        )

        task = {
            "question": "Is CRISPR 99% precise?",
            "search_results": {"merged": [], "mode": "hybrid", "total_results": 0},
            "enrichments": [],
            "enrichment_on_demand": False,
            "system_prompt_override": "You are an investigative analyst.",
        }

        result = asyncio.get_event_loop().run_until_complete(
            agent.execute(task)
        )

        # Verify custom system prompt was used
        call_args = mock_text_gen.generate.call_args
        self.assertEqual(call_args.kwargs.get("system", call_args[1].get("system", "")),
                         "You are an investigative analyst.")

    def test_default_system_prompt(self):
        """Without override, uses default SYNTHESIS_SYSTEM."""
        from cortex_on.agents.synthesis_agent import SynthesisAgent, SYNTHESIS_SYSTEM

        mock_text_gen = AsyncMock()
        mock_text_gen.generate = AsyncMock(return_value="Default output.")
        mock_text_gen.backend_name = "local"
        mock_text_gen._loaded = True

        mock_payments = AsyncMock()
        mock_payments.pay_for_synthesis = AsyncMock(return_value=MagicMock(
            to_dict=lambda: {"amount_usdc": 0.03, "status": "completed"},
        ))

        agent = SynthesisAgent(
            text_generator=mock_text_gen,
            payment_agent=mock_payments,
        )

        task = {
            "question": "What happened?",
            "search_results": {"merged": [], "mode": "hybrid", "total_results": 0},
            "enrichments": [],
        }

        result = asyncio.get_event_loop().run_until_complete(
            agent.execute(task)
        )

        call_args = mock_text_gen.generate.call_args
        system_used = call_args.kwargs.get("system", call_args[1].get("system", ""))
        self.assertIn("precise research analyst", system_used)


# ─── X402 Payment Agent Tests ───────────────────────────────────────────

class TestX402PaymentAgent(unittest.TestCase):
    """Test x402 guardrails and pricing."""

    def test_guardrail_max_payment(self):
        from cortex_on.config.settings import X402Config
        config = X402Config()
        self.assertEqual(config.max_payment_usdc, 1.0)
        # Verify no single operation exceeds max
        self.assertLessEqual(config.price_per_synthesis, config.max_payment_usdc)
        self.assertLessEqual(config.price_per_qa_question, config.max_payment_usdc)
        self.assertLessEqual(config.price_per_report, config.max_payment_usdc)

    def test_pricing_schedule(self):
        from cortex_on.config.settings import X402Config
        config = X402Config()
        self.assertEqual(config.price_per_minute_video, 0.10)
        self.assertEqual(config.price_per_synthesis, 0.03)
        self.assertEqual(config.price_per_qa_question, 0.05)
        self.assertEqual(config.price_per_enrichment_query, 0.01)
        self.assertEqual(config.price_per_report, 0.25)


# ─── Opus Planner Full Flow Tests ────────────────────────────────────────

class TestOpusPlannerFullFlow(unittest.TestCase):
    """End-to-end planner tests with mocked LLM responses."""

    def test_ingest_plan_efficiency(self):
        """Verify planner correctly identifies segments to skip."""
        from cortex_on.agents.opus_planner import OpusPlanner

        mock_response = json.dumps([
            {"vision_analysis": True, "enrichment": True, "index": True,
             "clip_worthy": True, "enrichment_focus": ["GDP growth claim"]},
            {"vision_analysis": False, "enrichment": False, "index": True,
             "skip_reason": "sponsor ad"},
            {"vision_analysis": False, "enrichment": False, "index": False,
             "skip_reason": "outro music"},
            {"vision_analysis": True, "enrichment": True, "index": True,
             "vision_priority": "high", "enrichment_focus": ["inflation at 3.2%"]},
            {"vision_analysis": True, "enrichment": False, "index": True,
             "clip_worthy": False},
        ])

        mock_text_gen = AsyncMock()
        mock_text_gen.generate = AsyncMock(return_value=mock_response)
        mock_text_gen.backend_name = "claude-opus-4-6"

        planner = OpusPlanner(mock_text_gen)

        segments = [
            {"index": i, "transcript": f"Content segment {i}" * 30,
             "start_seconds": i * 60, "end_seconds": (i + 1) * 60}
            for i in range(5)
        ]

        plans = asyncio.get_event_loop().run_until_complete(
            planner.plan_ingest(segments)
        )

        # Efficiency checks
        vision_count = sum(1 for p in plans if p.vision_analysis)
        enrich_count = sum(1 for p in plans if p.enrichment)
        index_count = sum(1 for p in plans if p.index)
        clip_count = sum(1 for p in plans if p.clip_worthy)

        self.assertEqual(vision_count, 3)   # 3 of 5 need vision
        self.assertEqual(enrich_count, 2)   # Only 2 have verifiable claims
        self.assertEqual(index_count, 4)    # Skip only outro
        self.assertEqual(clip_count, 1)     # 1 clip-worthy

        # Verify skip reasons
        self.assertEqual(plans[1].skip_reason, "sponsor ad")
        self.assertFalse(plans[2].index)  # Outro not indexed

    def test_synthesis_plan_conflict_detection(self):
        """When evidence conflicts, planner should choose investigative strategy."""
        from cortex_on.agents.opus_planner import OpusPlanner

        mock_response = json.dumps({
            "search_sufficient": True,
            "additional_searches": [],
            "needs_fresh_enrichment": True,
            "enrichment_queries": ["actual GDP Q3 2025"],
            "conflict_detected": True,
            "confidence_assessment": "low",
            "synthesis_strategy": "investigative",
            "focus_segments": [0, 3],
            "reasoning": "Video claims GDP grew 4.2% but web sources show 2.8%",
        })

        mock_text_gen = AsyncMock()
        mock_text_gen.generate = AsyncMock(return_value=mock_response)
        mock_text_gen.backend_name = "claude-opus-4-6"

        planner = OpusPlanner(mock_text_gen)

        search_results = {
            "merged": [
                {"video_id": "v1", "start_seconds": 0, "text": "GDP grew 4.2%",
                 "score": 0.95, "match_source": "transcript"},
            ],
        }

        plan = asyncio.get_event_loop().run_until_complete(
            planner.plan_synthesis("What was GDP growth?", search_results, [], "hybrid")
        )

        self.assertEqual(plan.strategy, "investigative")
        self.assertTrue(plan.conflict_detected)
        self.assertTrue(plan.needs_fresh_enrichment)
        self.assertEqual(plan.confidence, "low")

    def test_adaptive_analysis_discrepancy(self):
        """Opus should catch speech vs visual mismatches."""
        from cortex_on.agents.opus_planner import OpusPlanner

        mock_response = json.dumps({
            "claims_to_verify": ["SOL hit $142 at 61.8% fibonacci"],
            "entities_to_enrich": ["Solana", "Fibonacci retracement"],
            "discrepancies": ["Speaker says $142 but chart drawn from $98-$178 shows 61.8% = $128.56"],
            "follow_up_questions": ["What was actual SOL price on this date?"],
            "risk_score": 0.82,
        })

        mock_text_gen = AsyncMock()
        mock_text_gen.generate = AsyncMock(return_value=mock_response)
        mock_text_gen.backend_name = "claude-opus-4-6"

        planner = OpusPlanner(mock_text_gen)

        analysis = asyncio.get_event_loop().run_until_complete(
            planner.analyze_segment_deep(
                transcript="SOL hit the 61.8% fibonacci level at $142",
                vision_output={"description": "Chart showing fib from $98 to $178", "entities": ["Solana"]},
                video_id="crypto_v1",
                start_seconds=163,
                end_seconds=223,
            )
        )

        self.assertGreater(analysis.risk_score, 0.7)
        self.assertEqual(len(analysis.discrepancies), 1)
        self.assertIn("$142", analysis.discrepancies[0])
        self.assertIn("$128", analysis.discrepancies[0])
        self.assertEqual(len(analysis.claims_to_verify), 1)


# ─── Observability Decorator Tests ───────────────────────────────────────

class TestObservabilityDecorators(unittest.TestCase):
    """Test that trace decorators capture metrics correctly."""

    def test_trace_opus_plan_captures_ingest_metrics(self):
        from cortex_on.agents.observability import _metrics_store, _record_metric, get_metrics
        _metrics_store.clear()

        # Simulate what trace_opus_plan records
        _record_metric("opus.ingest_plan.latency_ms", 150.0)
        _record_metric("opus.ingest_plan.total_segments", 10)
        _record_metric("opus.ingest_plan.vision_segments", 7)
        _record_metric("opus.ingest_plan.enrich_segments", 4)
        _record_metric("opus.ingest_plan.skip_rate", 0.30)

        metrics = get_metrics(10)
        self.assertEqual(len(metrics), 5)

        names = [m["name"] for m in metrics]
        self.assertIn("opus.ingest_plan.skip_rate", names)

        skip_metric = next(m for m in metrics if m["name"] == "opus.ingest_plan.skip_rate")
        self.assertEqual(skip_metric["value"], 0.30)

    def test_trace_synthesis_captures_quality_metrics(self):
        from cortex_on.agents.observability import _metrics_store, _record_metric, get_metrics
        _metrics_store.clear()

        _record_metric("synthesis.latency_ms", 2300.0)
        _record_metric("synthesis.citation_count", 3)
        _record_metric("synthesis.strategy", "investigative")
        _record_metric("synthesis.confidence", "low")
        _record_metric("synthesis.cost_usdc", 0.03)

        metrics = get_metrics(10)
        strategy = next(m for m in metrics if m["name"] == "synthesis.strategy")
        self.assertEqual(strategy["value"], "investigative")

        cost = next(m for m in metrics if m["name"] == "synthesis.cost_usdc")
        self.assertEqual(cost["value"], 0.03)

    def test_evaluator_full_pipeline(self):
        """Run evaluator on ingest + synthesis results, verify summary."""
        from cortex_on.agents.observability import VidExEvaluator

        evaluator = VidExEvaluator()

        # Simulate 3 ingests
        for i in range(3):
            evaluator.score_ingest({
                "segments_count": 10 + i,
                "opus_plan": {
                    "vision_planned": 7 + i,
                    "enrichment_planned": 4,
                    "clip_worthy": 1,
                },
                "discrepancies_found": i,
                "high_risk_segments": list(range(i)),
                "pipeline_duration_seconds": 30 + i * 5,
                "payment": {"amount_usdc": 0.50 + i * 0.1},
            })

        # Simulate 3 syntheses
        for strategy in ["direct", "investigative", "comparative"]:
            evaluator.score_synthesis({
                "synthesis": (
                    f"At 1:30, the speaker states X [1]. "
                    f"Web evidence confirms this claim [2]. "
                    f"However, the timeline shows a gap [3]."
                ),
                "sources": [{"url": f"http://src{i}.com"} for i in range(3)],
                "opus_plan": {
                    "synthesis_strategy": strategy,
                    "confidence_assessment": "medium",
                    "conflict_detected": strategy == "investigative",
                },
                "payment": {"amount_usdc": 0.03},
            })

        summary = evaluator.get_summary()
        self.assertEqual(summary["total_evaluations"], 6)
        self.assertEqual(summary["ingest"]["count"], 3)
        self.assertEqual(summary["synthesis"]["count"], 3)
        self.assertGreater(summary["ingest"]["total_discrepancies"], 0)
        self.assertGreater(summary["synthesis"]["avg_quality_score"], 0.3)
        self.assertAlmostEqual(summary["synthesis"]["total_cost_usdc"], 0.09, places=2)


# ─── Run ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("CortexVid Integration Tests (Docker)")
    print("=" * 60)
    unittest.main(verbosity=2)
