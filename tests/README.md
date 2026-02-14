# CortexVid Tests

## Quick Start

```bash
# Docker (recommended — includes all deps)
./tests/run_tests.sh

# Local (no Docker, skips tests needing aiohttp)
./tests/run_tests.sh local
```

## Test Modes

| Command | What it runs | Deps needed |
|---------|-------------|-------------|
| `./run_tests.sh` | All 45+ tests in Docker | Docker only |
| `./run_tests.sh opus` | Opus planner tests only | Docker only |
| `./run_tests.sh observability` | Metrics + eval scoring | Docker only |
| `./run_tests.sh opik` | All tests + Opik dashboard | Docker only |
| `./run_tests.sh local` | Unit tests locally | Python 3.11+ |
| `./run_tests.sh ci` | CI pipeline + JUnit XML | Docker only |

## Test Files

| File | Tests | Requires |
|------|-------|----------|
| `test_cortexvid.py` | 30 tests — planner logic, data classes, JSON parsing, observability scoring | No external deps |
| `test_integration.py` | 15+ tests — orchestrator, synthesis agent, x402 guardrails, full planner flow | aiohttp |

## What's Tested

### Opus 4.6 Planner
- IngestPlan creation (skip filler, prioritize charts)
- SynthesisPlan strategy selection (direct/comparative/investigative/timeline)
- AdaptiveAnalysis discrepancy detection (speech vs visual mismatch)
- JSON response parsing (clean, markdown-fenced, garbage)
- Small segment optimization (≤2 segments skip planning)
- Mock LLM integration (full planning flow with mock responses)

### Observability
- Metric collection and retrieval
- Ingest scoring (planning efficiency, detection rate, cost)
- Synthesis scoring (citations, timestamps, verdicts, strategy fit)
- Evaluation summary aggregation

### Config
- Opus 4.6 model string (`claude-opus-4-6`)
- x402 guardrails (max $1/tx, $50/day)
- Pricing schedule validation
- Synthesis strategy prompts exist and are substantive

## Opik Dashboard

After running `./run_tests.sh opik`:
- Dashboard: http://localhost:5173
- Project: `cortexvid-tests`
- See all Opus planning traces, latencies, and quality scores
