# Fact-Verifier Agent

## Role
Claim extraction, web verification, cross-video contradiction detection, and x402 micropayment billing.

## Model
claude-opus-4-6 (for claim extraction and verdict reasoning)

## Code
`cortex_on/agents/fact_verifier.py` → `FactVerifier` class

## Auto-Discovery
Auto-discovered by `cortex_on/main.py` via `register_routes(app)`:

```
POST /api/v1/verify              → Fact-check an ingested video
GET  /api/v1/verify/{id}/report  → Full report with evidence + verdicts
POST /api/v1/verify/claim        → Verify a single claim against web + indexed videos
GET  /api/v1/verify/stats        → Agent statistics
```

## When to Invoke
- After video ingestion completes (Step 5 done)
- When user asks to fact-check a specific video
- When the autonomous agent loop detects new claims to verify
- When `/find-contradictions` skill is triggered
- After synthesis queries that involve verifiable statements

## API Usage

```bash
# Fact-check an ingested video
curl -X POST http://localhost:8093/api/v1/verify \
  -H "Content-Type: application/json" \
  -d '{"video_id": "5e97aff414d2", "max_claims": 15}'

# Verify a single claim
curl -X POST "http://localhost:8093/api/v1/verify/claim?text=BTC+will+hit+200K+by+June&speaker=Tom+Lee&category=prediction"

# Full report with evidence
curl http://localhost:8093/api/v1/verify/5e97aff414d2/report

# Agent stats
curl http://localhost:8093/api/v1/verify/stats
```

## Verification Flow
1. **Extract** — Opus 4.6 reads transcript, identifies verifiable claims
2. **Pay** — x402 charges $0.03 per claim via Circle wallet
3. **Search** — Parallel.ai searches for supporting/contradicting evidence
4. **Cross-reference** — Weaviate semantic search finds similar claims in other indexed videos
5. **Verdict** — Opus 4.6 reasons about the evidence and returns verdict

## Verdicts
- **CONFIRMED** — Evidence supports the claim
- **CONTRADICTED** — Evidence directly conflicts with the claim
- **STALE** — Claim was true when made but is now outdated
- **REVISED** — Same speaker changed their position in a later video
- **UNVERIFIABLE** — Insufficient evidence either way

## Dependencies
- `cortex_on/agents/parallel_client.py` — Parallel.ai API client
- `cortex_on/agents/x402_payment_agent.py` — x402 micropayment infrastructure
- `cortex_on/agents/opus_planner.py` — Opus 4.6 reasoning
- Weaviate — Cross-video semantic search

## Integration
- **opus-planner** → Flags segments with `enrichment_priority > 0.5` or `risk_score > 0.3`
- **video-ingest** → Provides transcript segments and Opus plan after ingestion
- **agent-autonomy** → Triggers fact-check on new videos, escalates contradictions
- **observability** → Traces verification latency, cost per claim, verdict distribution

## Environment Variables
- `PARALLEL_API_KEY` — Required for web verification
- `X402_ENABLED` — Enable x402 billing (default: true)
- `ANTHROPIC_MODEL_NAME` — Default: `claude-opus-4-6`
- `WEAVIATE_URL` — Weaviate instance for cross-referencing

## Pricing
- $0.03 per claim verified via x402
- Opus API cost per claim: ~$0.01-0.03 (extraction + verdict)
- Typical video (12 claims): ~$0.36-0.72 total
