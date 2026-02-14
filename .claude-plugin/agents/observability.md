# Observability Agent

## Role
Opik tracing for every Opus decision, pipeline metrics, evaluation framework.

## Code
`cortex_on/agents/observability.py` → `ObservabilityManager` class

## Auto-Discovery
Auto-discovered by `cortex_on/main.py` via `register_routes(app)`:

```
GET  /api/v1/observability/metrics  → Opus planning traces, pipeline metrics
GET  /api/v1/observability/eval     → Evaluation summary
GET  /api/v1/observability/config   → Current observability config
```

## What It Traces
- Every Opus 4.6 planning decision (strategy selected, scores assigned)
- Pipeline step durations (download, transcribe, plan, clip, index)
- Fact-verification latency and cost per claim
- Synthesis quality metrics
- Agent discovery status

## When to Invoke
- Always running — wraps every agent call with tracing
- Check metrics after a deploy to verify performance
- Review eval results to assess Opus planning quality

## API Usage

```bash
curl http://localhost:8093/api/v1/observability/metrics
curl http://localhost:8093/api/v1/observability/eval
curl http://localhost:8093/api/v1/observability/config
```

## Integration
- **All agents** → Observability wraps every agent's execution
- **opus-planner** → Traces strategy selection and segment scoring
- **fact-verifier** → Traces verification latency and verdict distribution
- Opik dashboard at `http://localhost:5173`

## Environment Variables
- `OPIK_ENABLED` — Default: `true`
- `OPIK_PROJECT` — Default: `cortexos`
- `OPIK_BASE_URL` — Default: `http://localhost:5173`
