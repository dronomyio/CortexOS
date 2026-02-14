# Agent Autonomy

## Role
Agent loop, autonomous monitoring, alert system. Runs CortexOS as a persistent service that detects events and triggers actions without user intervention.

## Code
`cortex_on/agent_loop.py` → Agent loop and monitoring logic

Endpoints registered by `cortex_on/main.py`:
```
POST /api/v1/agent/start   → Start autonomous agent loop
POST /api/v1/agent/stop    → Stop agent loop
GET  /api/v1/agent/status  → Agent loop status
GET  /api/v1/agent/alerts  → Recent alerts and actions taken
```

## What It Does
- Monitors for newly ingested videos → auto-triggers fact-verification
- Watches for contradictions across videos → escalates to alerts
- Tracks x402 spending against daily limits → warns before budget exceeded
- Checks agent health → restarts failed agents

## When to Invoke
- Start after deploy: `curl -X POST http://localhost:8093/api/v1/agent/start`
- Check status: `curl http://localhost:8093/api/v1/agent/status`
- Review what it did: `curl http://localhost:8093/api/v1/agent/alerts`

## Integration
- **video-ingest** → Detects new completed jobs
- **fact-verifier** → Triggers verification on new videos
- **observability** → Logs all autonomous actions
- **x402** → Monitors spending against guardrails

## Environment Variables
- `AGENT_AUTO_VERIFY` — Auto fact-check new videos (default: `true`)
- `AGENT_POLL_INTERVAL` — Seconds between checks (default: `30`)
- `AGENT_DAILY_LIMIT_USDC` — Stop spending after this amount (default: `10.00`)
