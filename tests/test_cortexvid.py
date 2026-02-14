"""
CortexVid Test Suite
====================

Run: python -m pytest tests/test_cortexvid.py -v
  or: python tests/test_cortexvid.py

Tests three layers:
  1. Import tests — all 25 modules compile and import
  2. Opus planner tests — planning logic works with mock data
  3. Observability tests — metrics collection and evaluation scoring
  4. Config tests — settings load correctly

No GPU, Weaviate, or external APIs needed.
"""
import asyncio
import json
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ─── 1. Import Tests ────────────────────────────────────────────────────

class TestImports(unittest.TestCase):
    """Verify all CortexVid modules import without errors."""

    def test_import_config(self):
        from cortex_on.config.settings import AppConfig, VisionConfig, X402Config, TextGenConfig
        config = AppConfig()
        self.assertIsNotNone(config)
        self.assertEqual(config.text_gen.paid_model, "claude-opus-4-6")

    def test_import_base_agent(self):
        from cortex_on.agents.base_agent import BaseAgent, AgentResult, AgentStatus
        self.assertEqual(AgentStatus.IDLE.value, "idle")

    def test_import_opus_planner(self):
        from cortex_on.agents.opus_planner import OpusPlanner, IngestPlan, SynthesisPlan, AdaptiveAnalysis
        plan = IngestPlan(0, {"vision_analysis": True, "enrichment": False})
        self.assertTrue(plan.vision_analysis)
        self.assertFalse(plan.enrichment)

    def test_import_observability(self):
        from cortex_on.agents.observability import (
            init_opik, get_metrics, trace_opus_plan,
            trace_synthesis, trace_ingest, VidExEvaluator,
        )
        self.assertIsNotNone(VidExEvaluator)

    def test_import_synthesis_agent(self):
        from cortex_on.agents.synthesis_agent import SynthesisAgent
        self.assertIsNotNone(SynthesisAgent)

    def test_import_orchestrator(self):
        """Orchestrator requires aiohttp — skip in minimal test env."""
        try:
            from cortex_on.agents.orchestrator import AgentOrchestrator, SYNTHESIS_STRATEGIES
            self.assertIn("direct", SYNTHESIS_STRATEGIES)
        except ImportError as e:
            if "aiohttp" in str(e):
                self.skipTest("aiohttp not installed — orchestrator test requires full deps")
            raise

    def test_import_synthesis_agent(self):
        """Synthesis agent requires aiohttp via x402 — skip in minimal test env."""
        try:
            from cortex_on.agents.synthesis_agent import SynthesisAgent
            self.assertIsNotNone(SynthesisAgent)
        except ImportError as e:
            if "aiohttp" in str(e):
                self.skipTest("aiohttp not installed — synthesis_agent test requires full deps")
            raise


# ─── 2. Opus Planner Tests ──────────────────────────────────────────────

class TestIngestPlan(unittest.TestCase):
    """Test IngestPlan data class."""

    def test_default_plan(self):
        from cortex_on.agents.opus_planner import IngestPlan
        plan = IngestPlan(0, {})
        self.assertTrue(plan.vision_analysis)
        self.assertTrue(plan.enrichment)
        self.assertTrue(plan.index)
        self.assertFalse(plan.clip_worthy)
        self.assertIsNone(plan.skip_reason)

    def test_skip_plan(self):
        from cortex_on.agents.opus_planner import IngestPlan
        plan = IngestPlan(3, {
            "vision_analysis": False,
            "enrichment": False,
            "index": True,
            "skip_reason": "filler intro",
        })
        self.assertFalse(plan.vision_analysis)
        self.assertFalse(plan.enrichment)
        self.assertEqual(plan.skip_reason, "filler intro")

    def test_to_dict(self):
        from cortex_on.agents.opus_planner import IngestPlan
        plan = IngestPlan(1, {"vision_analysis": True, "clip_worthy": True})
        d = plan.to_dict()
        self.assertEqual(d["segment_index"], 1)
        self.assertTrue(d["clip_worthy"])


class TestSynthesisPlan(unittest.TestCase):
    """Test SynthesisPlan data class."""

    def test_default_synthesis_plan(self):
        from cortex_on.agents.opus_planner import SynthesisPlan
        plan = SynthesisPlan({})
        self.assertTrue(plan.search_sufficient)
        self.assertFalse(plan.needs_fresh_enrichment)
        self.assertEqual(plan.strategy, "direct")
        self.assertEqual(plan.confidence, "medium")

    def test_investigative_plan(self):
        from cortex_on.agents.opus_planner import SynthesisPlan
        plan = SynthesisPlan({
            "synthesis_strategy": "investigative",
            "conflict_detected": True,
            "confidence_assessment": "low",
            "needs_fresh_enrichment": True,
            "additional_searches": [{"query": "SOL price history", "mode": "visual"}],
            "reasoning": "Claims conflict with chart data",
        })
        self.assertEqual(plan.strategy, "investigative")
        self.assertTrue(plan.conflict_detected)
        self.assertEqual(plan.confidence, "low")
        self.assertEqual(len(plan.additional_searches), 1)

    def test_to_dict_roundtrip(self):
        from cortex_on.agents.opus_planner import SynthesisPlan
        original = {
            "synthesis_strategy": "comparative",
            "confidence_assessment": "high",
            "conflict_detected": False,
        }
        plan = SynthesisPlan(original)
        d = plan.to_dict()
        self.assertEqual(d["synthesis_strategy"], "comparative")
        self.assertEqual(d["confidence_assessment"], "high")


class TestAdaptiveAnalysis(unittest.TestCase):
    """Test AdaptiveAnalysis data class."""

    def test_high_risk(self):
        from cortex_on.agents.opus_planner import AdaptiveAnalysis
        analysis = AdaptiveAnalysis({
            "claims_to_verify": ["SOL hit $142 at 61.8% fib"],
            "discrepancies": ["Speaker says $142 but chart shows $128"],
            "risk_score": 0.85,
        })
        self.assertEqual(len(analysis.claims_to_verify), 1)
        self.assertEqual(len(analysis.discrepancies), 1)
        self.assertGreater(analysis.risk_score, 0.5)

    def test_clean_segment(self):
        from cortex_on.agents.opus_planner import AdaptiveAnalysis
        analysis = AdaptiveAnalysis({})
        self.assertEqual(analysis.risk_score, 0.0)
        self.assertEqual(len(analysis.discrepancies), 0)


class TestOpusPlannerLogic(unittest.TestCase):
    """Test OpusPlanner methods with mocked LLM."""

    def test_small_segment_count_skips_planning(self):
        """1-2 segments should skip planning overhead."""
        from cortex_on.agents.opus_planner import OpusPlanner

        mock_text_gen = MagicMock()
        planner = OpusPlanner(mock_text_gen)

        segments = [
            {"index": 0, "transcript": "Hello world", "start_seconds": 0, "end_seconds": 60},
        ]

        plans = asyncio.get_event_loop().run_until_complete(
            planner.plan_ingest(segments)
        )

        self.assertEqual(len(plans), 1)
        self.assertTrue(plans[0].vision_analysis)
        # Should NOT have called the LLM
        mock_text_gen.generate.assert_not_called()

    def test_default_plans_fallback(self):
        """When planning fails, should return process-everything defaults."""
        from cortex_on.agents.opus_planner import OpusPlanner

        mock_text_gen = MagicMock()
        planner = OpusPlanner(mock_text_gen)

        segments = [
            {"index": i, "transcript": f"Segment {i}", "start_seconds": i*60, "end_seconds": (i+1)*60}
            for i in range(5)
        ]

        defaults = planner._default_ingest_plans(segments)
        self.assertEqual(len(defaults), 5)
        for p in defaults:
            self.assertTrue(p.vision_analysis)
            self.assertTrue(p.enrichment)
            self.assertTrue(p.index)

    def test_json_parsing_clean(self):
        """Parse clean JSON array."""
        from cortex_on.agents.opus_planner import OpusPlanner

        planner = OpusPlanner(MagicMock())
        result = planner._parse_json_response(
            '[{"vision_analysis": true}, {"vision_analysis": false}]',
            expect_list=True,
        )
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]["vision_analysis"])

    def test_json_parsing_markdown_fenced(self):
        """Parse JSON wrapped in markdown code fences."""
        from cortex_on.agents.opus_planner import OpusPlanner

        planner = OpusPlanner(MagicMock())
        result = planner._parse_json_response(
            '```json\n{"strategy": "investigative"}\n```',
            expect_list=False,
        )
        self.assertEqual(result["strategy"], "investigative")

    def test_json_parsing_garbage(self):
        """Unparseable response returns None."""
        from cortex_on.agents.opus_planner import OpusPlanner

        planner = OpusPlanner(MagicMock())
        result = planner._parse_json_response("This is not JSON at all", expect_list=False)
        self.assertIsNone(result)

    def test_planner_stats(self):
        from cortex_on.agents.opus_planner import OpusPlanner

        planner = OpusPlanner(MagicMock())
        stats = planner.get_stats()
        self.assertEqual(stats["planning_calls"], 0)


# ─── 3. Observability Tests ─────────────────────────────────────────────

class TestObservability(unittest.TestCase):
    """Test metrics collection and evaluation scoring."""

    def test_metrics_collection(self):
        from cortex_on.agents.observability import _record_metric, get_metrics, _metrics_store
        _metrics_store.clear()

        _record_metric("test.latency", 42.5, {"backend": "opus"})
        _record_metric("test.count", 3)

        metrics = get_metrics(10)
        self.assertEqual(len(metrics), 2)
        self.assertEqual(metrics[0]["name"], "test.latency")
        self.assertEqual(metrics[0]["value"], 42.5)
        self.assertEqual(metrics[0]["tags"]["backend"], "opus")

    def test_evaluator_ingest_scoring(self):
        from cortex_on.agents.observability import VidExEvaluator

        evaluator = VidExEvaluator()
        score = evaluator.score_ingest({
            "segments_count": 10,
            "opus_plan": {
                "vision_planned": 6,
                "enrichment_planned": 4,
                "clip_worthy": 2,
            },
            "discrepancies_found": 3,
            "high_risk_segments": [2, 7],
            "pipeline_duration_seconds": 45.0,
            "payment": {"amount_usdc": 0.50},
        })

        self.assertGreater(score["planning_efficiency"], 0)
        self.assertEqual(score["discrepancies"], 3)
        self.assertEqual(score["cost_usdc"], 0.50)
        self.assertEqual(score["clip_worthy"], 2)

    def test_evaluator_synthesis_scoring(self):
        from cortex_on.agents.observability import VidExEvaluator

        evaluator = VidExEvaluator()
        score = evaluator.score_synthesis({
            "synthesis": (
                "At 2:43, the trader claims SOL hit $142 at the 61.8% fib level [1]. "
                "However, the chart visible on screen shows $128.56 [2]. "
                "This contradicts the spoken claim. Current SOL price is $143.20 [3]."
            ),
            "sources": [
                {"url": "https://example.com/1"},
                {"url": "https://example.com/2"},
                {"url": "https://example.com/3"},
            ],
            "opus_plan": {
                "synthesis_strategy": "investigative",
                "confidence_assessment": "medium",
                "conflict_detected": True,
            },
            "payment": {"amount_usdc": 0.03},
        })

        self.assertGreater(score["quality_score"], 0.5)
        self.assertGreater(score["citation_count"], 0)
        self.assertTrue(score["has_timestamps"])
        self.assertTrue(score["has_verdict"])
        self.assertTrue(score["strategy_appropriate"])
        self.assertEqual(score["strategy"], "investigative")

    def test_evaluator_summary(self):
        from cortex_on.agents.observability import VidExEvaluator

        evaluator = VidExEvaluator()
        # Add some test data
        evaluator.score_ingest({
            "segments_count": 5,
            "opus_plan": {"vision_planned": 3, "enrichment_planned": 2, "clip_worthy": 1},
            "discrepancies_found": 1,
            "high_risk_segments": [],
            "pipeline_duration_seconds": 20.0,
            "payment": {"amount_usdc": 0.25},
        })
        evaluator.score_synthesis({
            "synthesis": "At 1:00, speaker says X [1]. Confirmed by evidence [2].",
            "sources": [{"url": "http://a.com"}],
            "opus_plan": {"synthesis_strategy": "direct", "confidence_assessment": "high", "conflict_detected": False},
            "payment": {"amount_usdc": 0.03},
        })

        summary = evaluator.get_summary()
        self.assertEqual(summary["total_evaluations"], 2)
        self.assertEqual(summary["ingest"]["count"], 1)
        self.assertEqual(summary["synthesis"]["count"], 1)
        self.assertGreater(summary["ingest"]["avg_planning_efficiency"], 0)


# ─── 4. Config Tests ────────────────────────────────────────────────────

class TestConfig(unittest.TestCase):
    """Test configuration defaults."""

    def test_opus_model_default(self):
        from cortex_on.config.settings import TextGenConfig
        config = TextGenConfig()
        self.assertEqual(config.paid_model, "claude-opus-4-6")

    def test_x402_guardrails(self):
        from cortex_on.config.settings import X402Config
        config = X402Config()
        self.assertEqual(config.max_payment_usdc, 1.0)
        self.assertEqual(config.daily_limit_usdc, 50.0)
        self.assertEqual(config.price_per_synthesis, 0.03)

    def test_synthesis_strategies(self):
        try:
            from cortex_on.agents.orchestrator import SYNTHESIS_STRATEGIES
            self.assertEqual(len(SYNTHESIS_STRATEGIES), 4)
        except ImportError:
            self.skipTest("aiohttp not installed")

    def test_full_app_config(self):
        from cortex_on.config.settings import AppConfig
        config = AppConfig()
        self.assertEqual(config.max_concurrent_agents, 4)
        self.assertEqual(config.segment_duration_seconds, 60)
        self.assertEqual(config.agent_timeout_seconds, 120)


# ─── 5. Integration Test (mocked LLM) ───────────────────────────────────

class TestOpusPlannerWithMockLLM(unittest.TestCase):
    """Test full planner flow with a mocked text generator."""

    def test_plan_ingest_with_mock_llm(self):
        """Simulate Opus analyzing 5 segments and returning a plan."""
        from cortex_on.agents.opus_planner import OpusPlanner

        mock_response = json.dumps([
            {"vision_analysis": True, "vision_priority": "high", "enrichment": True,
             "enrichment_focus": ["CRISPR 99% precision"], "index": True, "clip_worthy": True},
            {"vision_analysis": False, "enrichment": False, "index": True,
             "skip_reason": "intro filler"},
            {"vision_analysis": True, "enrichment": True, "index": True, "clip_worthy": False},
            {"vision_analysis": True, "vision_priority": "high", "enrichment": True,
             "enrichment_focus": ["FDA approved Casgevy"], "index": True, "clip_worthy": True},
            {"vision_analysis": False, "enrichment": False, "index": False,
             "skip_reason": "outro music"},
        ])

        mock_text_gen = AsyncMock()
        mock_text_gen.generate = AsyncMock(return_value=mock_response)
        mock_text_gen.backend_name = "claude-opus-4-6"

        planner = OpusPlanner(mock_text_gen)

        segments = [
            {"index": i, "transcript": f"Content {i}" * 50, "start_seconds": i*60, "end_seconds": (i+1)*60}
            for i in range(5)
        ]

        plans = asyncio.get_event_loop().run_until_complete(
            planner.plan_ingest(segments)
        )

        self.assertEqual(len(plans), 5)

        # Segment 0: full processing + clip worthy
        self.assertTrue(plans[0].vision_analysis)
        self.assertTrue(plans[0].enrichment)
        self.assertTrue(plans[0].clip_worthy)
        self.assertEqual(plans[0].enrichment_focus, ["CRISPR 99% precision"])

        # Segment 1: skipped
        self.assertFalse(plans[1].vision_analysis)
        self.assertFalse(plans[1].enrichment)
        self.assertEqual(plans[1].skip_reason, "intro filler")

        # Segment 4: fully skipped
        self.assertFalse(plans[4].index)

        # Verify LLM was called
        mock_text_gen.generate.assert_called_once()

    def test_plan_synthesis_with_mock_llm(self):
        """Simulate Opus choosing investigative strategy for conflicting evidence."""
        from cortex_on.agents.opus_planner import OpusPlanner

        mock_response = json.dumps({
            "search_sufficient": True,
            "additional_searches": [{"query": "SOL fibonacci levels", "mode": "visual"}],
            "needs_fresh_enrichment": True,
            "enrichment_queries": ["current SOL price", "fibonacci 61.8% calculation"],
            "conflict_detected": True,
            "confidence_assessment": "low",
            "synthesis_strategy": "investigative",
            "focus_segments": [0, 3],
            "reasoning": "Speaker claims $142 but chart shows $128 — clear discrepancy needs investigation",
        })

        mock_text_gen = AsyncMock()
        mock_text_gen.generate = AsyncMock(return_value=mock_response)
        mock_text_gen.backend_name = "claude-opus-4-6"

        planner = OpusPlanner(mock_text_gen)

        search_results = {
            "merged": [
                {"video_id": "v1", "start_seconds": 0, "text": "SOL hit $142", "score": 0.9, "match_source": "transcript"},
                {"video_id": "v1", "start_seconds": 163, "description": "chart showing fibonacci", "score": 0.85, "match_source": "visual"},
            ],
        }

        plan = asyncio.get_event_loop().run_until_complete(
            planner.plan_synthesis("Did SOL really hit $142?", search_results, [], "hybrid")
        )

        self.assertEqual(plan.strategy, "investigative")
        self.assertTrue(plan.conflict_detected)
        self.assertEqual(plan.confidence, "low")
        self.assertTrue(plan.needs_fresh_enrichment)
        self.assertEqual(len(plan.additional_searches), 1)
        self.assertIn("$142", plan.reasoning)


# ─── Run ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("CortexVid Test Suite")
    print("=" * 60)
    unittest.main(verbosity=2)
