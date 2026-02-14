# MCP Server — CortexOS Video Intelligence

## What it does

CortexOS is a **paid API provider** — other AI agents and apps pay to use its video
intelligence services via the x402 micropayment protocol on Arc Network.

The MCP server exposes CortexOS to Claude Code / Claude Desktop as both:
- **Client tools** — ingest videos, search, verify, synthesize
- **x402 Provider tools** — list pricing, submit payments, autonomous access

### Video Pipeline (free ingest, paid query)
| Tool | Price | Description |
|---|---|---|
| `video_ingest_url` | Free | Ingest YouTube URL → Whisper → Opus → Weaviate |
| `video_ingest_file` | Free | Upload local video file |
| `video_get_status` | Free | Poll job progress |
| `video_search` | $0.01 | Semantic search over indexed video |

### Fact Verification (paid)
| Tool | Price | Description |
|---|---|---|
| `verify_video` | $0.03/claim | Fact-check all claims in a video |
| `verify_claim` | $0.03 | Verify a single claim vs web + indexed videos |

### Synthesis (paid)
| Tool | Price | Description |
|---|---|---|
| `synthesize` | $0.03 | Opus-planned cited synthesis across videos |

### x402 Provider
| Tool | Description |
|---|---|
| `x402_list_resources` | List all priced endpoints and costs |
| `x402_submit_payment` | Submit payment proof → get access token |
| `x402_autonomous_access` | Auto: request → 402 → pay → access (one step) |
| `x402_get_stats` | Revenue, payments, active tokens |
| `x402_health` | MongoDB, Circle SDK, network status |

### System (free)
| Tool | Description |
|---|---|
| `health_check` | API status, Opus planner, agent discovery |
| `list_agents` | All auto-discovered agents |
| `payments_stats` | x402 payment agent stats |
| `payments_ledger` | Payment audit trail |

## x402 Payment Flow

```
Agent → GET /api/v1/synthesize
        ← 402 Payment Required {payment_id, amount: $0.03, payee_address}

Agent → POST /api/v1/x402/payments/submit
        {payment_id, payer_address, tx_hash}
        ← 200 {access_token: "x402_abc123..."}

Agent → GET /api/v1/synthesize
        Header: X-Payment-Token: x402_abc123...
        ← 200 {synthesis data}
```

Or use `x402_autonomous_access` to do all three steps in one MCP call.

## Architecture

```
Claude Code / Claude Desktop / External Agent
    ↕ stdio (MCP protocol)
MCP Server (mcp/server.py)
    ↕ HTTP (aiohttp)
CortexOS API (Docker :8093)
    ├── x402 Middleware (gates paid endpoints)
    │   └── MongoDB (payments, tokens, resources)
    ├── Opus 4.6 Planner
    ├── Weaviate (vector search)
    ├── Whisper (transcription)
    └── 7 auto-discovered agents
```

## Setup

### Claude Code (recommended)

```bash
pip install mcp aiohttp
cd /path/to/CortexOS/cortex_on
claude mcp add cortexos -- python3 -m mcp.server
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cortexos": {
      "command": "python3",
      "args": ["-m", "mcp.server"],
      "cwd": "/path/to/CortexOS/cortex_on",
      "env": {
        "CORTEXOS_API_URL": "http://localhost:8093"
      }
    }
  }
}
```

### Environment

```bash
# CortexOS API
CORTEXOS_API_URL=http://localhost:8093

# x402 server (in .env for docker-compose)
X402_ENABLED=true
MONGODB_URL=mongodb://localhost:27017
MONGODB_DB=cortexos
PAYEE_ADDRESS=0x...your-arc-address...

# Circle SDK (optional — for wallet payment verification)
CIRCLE_API_KEY=...
CIRCLE_ENTITY_SECRET=...
ARC_TESTNET=true
```

### MongoDB

CortexOS x402 uses MongoDB (local) for payment persistence. No schema migration needed —
collections are auto-created with indexes on first request:

```
cortexos.x402_payments      → payment records
cortexos.x402_access_tokens → post-payment access tokens
cortexos.x402_resources     → priced endpoint catalog
```

### Prerequisites

```bash
pip install mcp aiohttp motor
```

## x402 Middleware Integration

The x402 middleware is an auto-discovered agent (`agents/x402_middleware.py`).
Drop it in `agents/` and it registers:

- HTTP middleware that intercepts ALL requests
- `POST /api/v1/x402/payments/submit` — submit payment
- `GET /api/v1/x402/resources` — list pricing
- `GET /api/v1/x402/stats` — revenue stats
- `GET /api/v1/x402/health` — server health
- `GET /api/v1/x402/payments` — list payments
- `GET /api/v1/x402/payments/{id}` — payment lookup

No `main.py` changes needed — auto-discovery handles registration.
