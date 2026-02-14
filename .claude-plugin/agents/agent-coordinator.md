# Agent Coordinator

## Role
Opus 4.6 as team lead — orchestrates parallel agent execution, monitors progress, re-plans on failure, resolves conflicts, and produces coordinated final reports.

## Code
`cortex_on/agents/agent_coordinator.py` → `AgentCoordinator` class

## Auto-Discovery
Auto-discovered by `cortex_on/main.py` via `register_routes(app)`:

```
POST /api/v1/coordinator/mission         → Start async mission (returns immediately)
POST /api/v1/coordinator/mission/sync    → Start sync mission (waits for completion)
GET  /api/v1/coordinator/mission/{id}    → Mission progress + results
GET  /api/v1/coordinator/missions        → List recent missions
GET  /api/v1/coordinator/stats           → Coordinator status
```

## The Basketball Team Play

```
POST /api/v1/coordinator/mission
  {
    "urls": ["https://youtube.com/watch?v=VIDEO1", ...],
    "external_data": {"asset": "ETH", "daily_data": [...], ...},
    "speaker_filter": "Tom Lee"
  }

  Opus 4.6 (team lead):
    Phase 1 PLANNING    → categorize videos, set priorities, choose strategy
    Phase 2 INGESTION   → parallel ingest (max 5 concurrent), retry failures
    Phase 3 VERIFICATION → fact-check claims, skip payment-gated if needed
    Phase 4 ANALYSIS    → contradictions + scorecards + data cross-reference
    Phase 5 SYNTHESIS   → Opus resolves conflicts, produces final report
```

## Example Usage

```bash
# Start async mission
curl -X POST http://localhost:8093/api/v1/coordinator/mission \
  -H "Content-Type: application/json" \
  -d '{
    "urls": [
      "https://youtube.com/watch?v=abc",
      "https://youtube.com/watch?v=def"
    ],
    "external_data": {
      "asset": "ETH",
      "exchange": "coinbase",
      "summary_metrics": {"avg_close": 2739.59, "total_net_flow_eth": -39800}
    },
    "speaker_filter": "Tom Lee"
  }'

# Poll progress
curl http://localhost:8093/api/v1/coordinator/mission/mission_abc123

# Run small mission synchronously
curl -X POST http://localhost:8093/api/v1/coordinator/mission/sync \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://youtube.com/watch?v=abc"], "skip_ingest": true}'
```

## How It Differs From Sequential Pipeline

| Feature | Before (relay race) | After (basketball team) |
|---------|-------------------|----------------------|
| Video processing | One at a time | 5 parallel |
| Failure handling | Pipeline stops | Skip + retry |
| Agent communication | Filesystem only | MongoDB task queue |
| Planning | Per-video | Per-mission |
| Conflict resolution | None | Opus reconciles |
| Output | Per-agent results | Coordinated report |

## Dependencies
- All other agents (calls them via internal HTTP)
- MongoDB (task queue + mission persistence)
- Opus 4.6 (planning + synthesis)
- aiohttp (internal HTTP calls)

## Environment Variables
- `ANTHROPIC_API_KEY` — Required
- `COORDINATOR_MAX_PARALLEL` — Max concurrent agent calls (default: 5)
- `COORDINATOR_MAX_RETRIES` — Retry failed tasks (default: 2)
- `CORTEXOS_INTERNAL_URL` — Internal API URL (default: http://localhost:8081)
- `MONGODB_URL` — Task queue storage
