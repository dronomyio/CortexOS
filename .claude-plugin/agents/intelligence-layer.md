# Intelligence Layer Agent

## Role
Cross-video contradiction detection, speaker reliability scorecards, and external data cross-referencing.

## Code
`cortex_on/agents/intelligence_layer.py` → `IntelligenceLayer` class

## Auto-Discovery
Auto-discovered by `cortex_on/main.py` via `register_routes(app)`:

```
POST /api/v1/contradictions/find           → Find contradictions across all indexed videos
GET  /api/v1/contradictions/stats          → Intelligence layer stats
POST /api/v1/speakers/scorecard            → Reliability scorecards for all/specific speakers
GET  /api/v1/speakers/{speaker}/score      → Quick score for one speaker
POST /api/v1/intelligence/cross-reference  → Cross-reference claims against YOUR external data
```

## Key Endpoints

### Find Contradictions
```bash
curl -X POST http://localhost:8093/api/v1/contradictions/find \
  -H "Content-Type: application/json" \
  -d '{
    "speaker_filter": "Tom Lee",
    "topic_filter": "ETH",
    "external_data": {
      "asset": "ETH",
      "summary_metrics": {"avg_close": 2739.59, "total_net_flow_eth": -39800}
    }
  }'
```

### Speaker Scorecard
```bash
curl -X POST http://localhost:8093/api/v1/speakers/scorecard \
  -H "Content-Type: application/json" \
  -d '{"speaker": "Tom Lee"}'
```

### Cross-Reference External Data
```bash
curl -X POST http://localhost:8093/api/v1/intelligence/cross-reference \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Which ETH price predictions were wrong?",
    "external_data": {"daily_data": [...], "summary_metrics": {...}},
    "speaker_filter": "Tom Lee"
  }'
```

## Dependencies
- `cortex_on/agents/fact_verifier.py` — Claim extraction and verification
- `cortex_on/agents/opus_planner.py` — Opus 4.6 reasoning
- Weaviate — Cross-video semantic search
- `ANTHROPIC_API_KEY` — Required for Opus reasoning

## Integration
- **fact-verifier** → Uses claim extraction and verification pipeline
- **opus-planner** → Opus 4.6 reasons about contradictions and data conflicts
- **video-ingest** → Reads indexed video transcripts from /data/out/
- **x402** → Contradiction find and scorecard are paid endpoints ($0.03 each)
