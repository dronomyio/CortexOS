# Opus Planner Agent

## Role
Dynamic planning — Opus 4.6 reads each transcript and selects the optimal processing strategy before any work begins.

## Model
claude-opus-4-6

## Code
`cortex_on/agents/opus_planner.py` → `OpusPlanner` class

## Auto-Discovery
Auto-discovered by `cortex_on/main.py` at startup via `register_routes(app)`:

```
GET  /api/v1/planner/stats       → Plans generated, strategies chosen
GET  /api/v1/planner/strategies  → List available synthesis strategies
```

## When to Invoke
- Immediately after Whisper transcription completes (Step 2 → Step 3)
- Before any CLIP processing, fact-verification, or synthesis
- The planner decides what happens next — it's never skipped

## Strategies
Opus selects one of these based on content analysis:
- **investigative** — Prediction-heavy content, contradictions likely
- **comparative** — Multiple speakers or competing viewpoints
- **factual** — Data-driven claims, verifiable against external sources
- **narrative** — Story-based content, timeline reconstruction
- **exploratory** — Broad topic, needs wide search

## API Usage

```bash
# Check planner stats
curl http://localhost:8093/api/v1/planner/stats

# List strategies
curl http://localhost:8093/api/v1/planner/strategies

# Planner runs automatically during ingest — see job plan:
curl http://localhost:8093/api/v1/jobs/{job_id}/plan
```

## How It Plans
1. Reads transcript segments from Whisper output
2. Evaluates content type, speaker patterns, claim density
3. Assigns `enrichment_priority` (0.0–1.0) and `risk_score` (0.0–1.0) per segment
4. Selects strategy and outputs `/data/out/{video_id}/opus_plan.json`

## Integration
- **video-ingest** → Calls planner after transcription
- **fact-verifier** → Uses plan to prioritize which segments to verify
- **observability** → Traces every planning decision via Opik

## Environment Variables
- `ANTHROPIC_API_KEY` — Required
- `ANTHROPIC_MODEL_NAME` — Default: `claude-opus-4-6`
