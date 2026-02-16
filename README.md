# CortexOS

**A Claude-Powered Agent Operating System for Verifiable Intelligence**

CortexOS uses Claude Opus 4.6 as a planning and verification engine that extracts financial claims from video transcripts and tests them against quantitative market data. When a YouTube interview is ingested, Opus generates a structured execution plan: classifying the video, identifying testable claims, and distinguishing measurable predictions from subjective commentary. For claims like "violent upside repricing," an ExternalDataAgent retrieves 63,000 minute-level ETH/USD bars from MongoDB (Polygon.io source), computes realized returns over the referenced period, and returns a cited verdict with timestamps, percentages, and severity labels.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CortexOS                              │
│                   Claude Opus 4.6 Brain                      │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│ Agent    │ Opus     │ External │ Intel    │ Video           │
│ Coord    │ Planner  │ Data     │ Layer    │ Ingest          │
├──────────┼──────────┼──────────┼──────────┼─────────────────┤
│ Fact     │Synthesis │ Video QA │ x402     │ x402 Payment    │
│ Verifier │ Agent    │          │Middleware│ Agent           │
├──────────┴──────────┴──────────┴──────────┴─────────────────┤
│  Weaviate (vectors)  │  MongoDB (market data + audit)       │
└──────────────────────┴──────────────────────────────────────┘
```

### Core Agents (17 auto-discovered)

| Agent | File | Purpose |
|-------|------|---------|
| **Agent Coordinator** | `agent_coordinator.py` | Opus 4.6 team lead — plans, assigns parallel tasks, monitors, re-plans on failure |
| **Opus Planner** | `opus_planner.py` | Per-video strategy — classifies video type, decides claim extraction approach |
| **External Data Agent** | `external_data_agent.py` | Queries 63K minute-level ETH/USD bars from MongoDB for claim verification |
| **Intelligence Layer** | `intelligence_layer.py` | Contradiction detection, speaker scorecards, cross-reference against market data |
| **Video Ingest** | `video_ingest_agent.py` | YouTube download → Whisper transcription → Weaviate vector indexing |
| **Fact Verifier** | `fact_verifier.py` | Opus claim extraction → cross-video verification → CONFIRMED/CONTRADICTED/STALE verdicts |
| **Synthesis Agent** | `synthesis_agent.py` | Cited answers with timestamped evidence via Weaviate hybrid search |
| **Video QA** | `video_qa_agent.py` | Q&A over the entire video corpus |
| **x402 Middleware** | `x402_middleware.py` | Payment gate — HTTP 402 → pay USDC → access token |
| **x402 Payment Agent** | `x402_payment_agent.py` | Circle wallet management, spending guardrails, MongoDB audit trail |
| **Observability** | `observability.py` | Opik tracing — token counts, cost logging, quality scoring |
| **Context Enrichment** | `context_enrichment_agent.py` | Enriches synthesis with additional context on-demand |
| **Code Agent** | `code_agent.py` | Code generation and analysis |
| **Web Surfer** | `web_surfer.py` | Web browsing for external data retrieval |
| **Orchestrator** | `orchestrator.py` | Low-level task orchestration |
| **Parallel Client** | `parallel_client.py` | Concurrent API calls (5 parallel) |
| **Planner Agent** | `planner_agent.py` | Mission planning primitives |

### Auto-Discovery

Any Python file placed in `cortex_on/agents/` with a `register_routes(app)` function is automatically discovered and registered at startup. Zero configuration required.

```
[CortexOS] ✓ Auto-registered agent: agent_coordinator
[CortexOS] ✓ Auto-registered agent: external_data_agent
[CortexOS] ✓ Auto-registered agent: intelligence_layer
...
```

---

## Features

### Contradiction Detection with Market Data Verification

The Intelligence Layer sends all video transcripts plus real ETH/USD market data to Opus 4.6 in a single call. Opus extracts verifiable claims and cross-references them against actual prices.

**Output structure per contradiction:**
- Two cited claims with speaker attribution and video timestamps
- Contradiction type: `self_contradiction`, `cross_speaker`, `data_contradiction`
- Severity: `high` (direct factual conflict), `medium` (directional conflict), `low` (subtle inconsistency)
- Explanation with reasoning
- Market data evidence (when available) with exact price windows

**Example:**
> Tom Lee claimed Ethereum is entering a "structural demand phase" with "violent upside repricing."
> Actual ETH/USD data: −30.98% decline from $2,966 to $2,047, max drawdown −48.86%.
> Verdict: DATA_CONTRADICTION, severity HIGH.

### Speaker Scorecards

Every speaker is graded A through F based on:
- Total claims vs. verified claims
- Self-contradictions
- Stale predictions
- Data contradictions (claims vs. real market data)
- Accuracy rate: `verified / (verified + contradicted + stale + data_contradictions)`

### x402 Micropayments

Premium endpoints gated by HTTP 402 protocol:
- Contradiction scan: $0.03
- Synthesis: $0.05
- Video search: $0.01
- Fact verification: $0.03/claim

Payments in USDC on Circle's Arc network. AI agents pay autonomously with their own Circle wallets.

### MCP Server

CortexOS exposes 17 tools via Model Context Protocol (MCP). Any MCP-compatible client (Claude Desktop, Claude Code) can call CortexOS agents directly.

```bash
# Add to Claude Code
claude mcp add cortexos -- python3 -m mcp_server.server

# Add to Claude Desktop (~/.config/claude_desktop_config.json)
{
  "mcpServers": {
    "cortexos": {
      "command": "python3",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/cortex_on",
      "env": { "CORTEXOS_API_URL": "http://localhost:8093" }
    }
  }
}
```

**MCP Tools:** `video_ingest_url`, `video_ingest_file`, `video_get_status`, `video_search`, `verify_video`, `verify_claim`, `synthesize`, `x402_list_resources`, `x402_submit_payment`, `x402_get_stats`, `x402_health`, `x402_autonomous_access`, `payments_stats`, `payments_ledger`, `health_check`, `list_agents`

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Anthropic API key (Claude Opus 4.6)
- 8GB+ RAM recommended

### Setup

```bash
git clone https://github.com/your-org/CortexOS.git
cd CortexOS

# Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Start all services
docker compose up -d

# Verify
docker logs cortexos-cortex_on-1 2>&1 | head -30
curl -s http://localhost:8093/api/v1/health | python3 -m json.tool
```

### Verify MongoDB Market Data

```bash
docker exec cortexos-cortex_on-1 python3 -c "
from pymongo import MongoClient
db = MongoClient('mongodb://admin:changeme@mongodb:27017/')['cortexos']
print('Daily bars:', db.eth_daily_data.count_documents({}))
print('Minute bars:', db.eth_minute_data.count_documents({}))
"
# Expected: Daily bars: 44, Minute bars: 63343
```

### Access

| Service | URL | Description |
|---------|-----|-------------|
| Frontend | http://localhost:3000 | CortexOS Dashboard |
| Backend API | http://localhost:8093 | FastAPI endpoints |
| Weaviate | http://localhost:8084 | Vector database |
| MongoDB | localhost:27017 | Market data + audit logs |

---

## Project Structure

```
CortexOS/
├── cortex_on/                    # Backend — FastAPI + agents
│   ├── main.py                   # App entry, agent auto-discovery, video pipeline
│   ├── entrypoint.sh             # Container startup: ETL → uvicorn
│   ├── load_eth_mongo.py         # ETH/USD data ETL: CSV → MongoDB
│   ├── agents/                   # Auto-discovered agents
│   │   ├── agent_coordinator.py
│   │   ├── external_data_agent.py
│   │   ├── intelligence_layer.py
│   │   ├── opus_planner.py
│   │   ├── fact_verifier.py
│   │   ├── synthesis_agent.py
│   │   ├── video_ingest_agent.py
│   │   ├── video_qa_agent.py
│   │   ├── x402_middleware.py
│   │   ├── x402_payment_agent.py
│   │   ├── observability.py
│   │   └── ...
│   ├── mcp_server/
│   │   └── server.py             # MCP server — 17 tools over stdio
│   ├── core/
│   │   └── jobs.py               # Shared job state
│   └── scripts/
│       └── mongo-init.js         # MongoDB initialization
├── frontend/                     # React + Vite dashboard
│   ├── src/
│   │   ├── CortexOS.jsx          # Main UI — dashboard, architecture, chat, contradictions
│   │   └── api.js                # API client
│   ├── vite.config.js
│   └── Dockerfile
├── assets/
│   └── mongodb_data/
│       └── eth_usd_minute_combined.csv  # 63K ETH/USD minute bars (Polygon.io)
├── docker-compose.yaml
└── .env
```

---

## API Endpoints

### Video Pipeline
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/ingest/url` | Ingest YouTube URL |
| POST | `/api/v1/ingest/urls` | Batch ingest multiple URLs |
| POST | `/api/v1/videos/upload` | Upload local video file |
| GET | `/api/v1/jobs` | List all jobs |
| GET | `/api/v1/jobs/{job_id}` | Job status and progress |

### Intelligence
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/contradictions/find` | Scan all videos for contradictions |
| GET | `/api/v1/contradictions/stats` | Intelligence layer status |
| GET | `/api/v1/speakers/scorecard` | Reliability scores per speaker |
| POST | `/api/v1/intelligence/cross-reference` | Cross-reference claims against data |

### Synthesis
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/synthesize` | Opus-powered Q&A with citations |
| POST | `/api/v1/qa/ask` | Video QA |

### Market Data
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/market/summary` | Full ETH market data summary |
| GET | `/api/v1/market/price/{date}` | OHLCV for specific date |
| GET | `/api/v1/market/range?start=X&end=Y` | Date range query |
| GET | `/api/v1/market/return?start=X&end=Y` | Compute actual return |
| GET | `/api/v1/market/minute/{date}` | Minute-level bars |
| GET | `/api/v1/market/health` | MongoDB connection status |

### x402 Payments
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/x402/resources` | List priced endpoints |
| POST | `/api/v1/x402/payments/submit` | Submit payment proof |
| GET | `/api/v1/x402/stats` | Revenue and payment stats |
| GET | `/api/v1/x402/health` | x402 server health |

### System
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/health` | Backend health + discovered agents |
| GET | `/api/v1/agents` | All registered agents |

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required. Claude API key |
| `ANTHROPIC_MODEL_NAME` | `claude-opus-4-6` | Model for planning and synthesis |
| `MONGODB_URI` | `mongodb://admin:changeme@mongodb:27017/` | MongoDB connection |
| `MONGODB_URL` | Same as URI | Used by ETL script |
| `WEAVIATE_URL` | `http://weaviate:8080` | Weaviate connection |
| `WHISPER_MODEL` | `tiny` | Whisper model size (tiny/base/small/medium/large) |
| `SKIP_CLIP` | `true` | Skip CLIP visual analysis |
| `X402_ENABLED` | `false` | Enable x402 payment gates |
| `OPIK_ENABLED` | `false` | Enable Opik observability |
| `OPIK_URL_OVERRIDE` | `http://host.docker.internal:5173/api` | Opik server URL |
| `TEXT_PAID_FALLBACK` | `true` | Allow paid API fallback |

### GPU Support

For GPU-accelerated Whisper transcription, add to `docker-compose.yaml` under `cortex_on`:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

---

## Data Pipeline

### ETH Market Data ETL

On container startup, `entrypoint.sh` runs `load_eth_mongo.py` which:

1. Reads `eth_usd_minute_combined.csv` (63,343 minute bars, Jan 1 – Feb 13, 2026)
2. Aggregates to daily OHLCV with VWAP, returns, range %
3. Creates `eth_daily_data` collection (44 bars) — used by Opus for claim verification
4. Creates `eth_minute_data` collection (63,343 bars) — for granular verification
5. Idempotent — skips if data already loaded

### Video Ingestion Pipeline

```
YouTube URL → yt-dlp download → Whisper transcription (GPU/CPU)
  → Opus 4.6 planning → Weaviate vector indexing → Ready
```

5 phases tracked per job: Download (10%) → Transcribe (35%) → Opus Plan (50%) → Index (85%) → Done (100%)

---

## Frontend

The CortexOS Dashboard (`frontend/src/CortexOS.jsx`) provides:

- **Dashboard** — Video library with thumbnails, search, job status, stats bar
- **Architecture** — Interactive agent grid showing all 17 agents with descriptions
- **Chat Panel** — Interactive Q&A with Opus 4.6, scoped to selected video or all
- **Contradiction Timeline** — Visual timeline with color-coded severity dots, claim pairs, market data evidence
- **Speaker Scorecards** — A-F reliability grades per speaker
- **Diagram** — System architecture visualization
- **Plugin** — MCP integration details

---

## Cloud Deployment

### Morph Cloud (CPU)

Add to `frontend/vite.config.js`:
```js
server: {
  host: '0.0.0.0',
  port: 3000,
  allowedHosts: 'all',
}
```

### Lambda Cloud (GPU)

Use the GPU deploy block in docker-compose.yaml. Whisper runs 5-10x faster with GPU.

---

## Adding New Agents

CortexOS is vertical-agnostic. To add a new agent:

1. Create `cortex_on/agents/my_new_agent.py`
2. Implement `register_routes(app)`:

```python
from fastapi import FastAPI

def register_routes(app: FastAPI):
    @app.get("/api/v1/my-agent/health")
    async def health():
        return {"status": "active", "agent": "my_new_agent"}

    @app.post("/api/v1/my-agent/analyze")
    async def analyze(request: dict):
        # Your agent logic here
        return {"result": "..."}
```

3. Restart: `docker compose restart cortex_on`
4. Verify: `curl http://localhost:8093/api/v1/health` — your agent appears in `discovered_agents`

---

## License

Proprietary — AdaBoost AI
