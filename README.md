# CortexOS Deploy Package

## What's inside

```
cortex_on/
├── main.py                          # Updated FastAPI (928 lines, uses VideoIngestAgent)
├── agent_loop.py                    # Autonomous loop (root level → /app/agent_loop.py)
├── agents/
│   ├── __init__.py                  # Safe init (only imports base_agent)
│   ├── opus_planner.py              # Fixed absolute imports
│   ├── video_ingest_agent.py        # NEW — wraps pipeline into agent class
│   └── observability.py             # Opik tracing (30/30 tests)
└── .claude-plugin/
    └── agents/
        ├── video-ingest.md          # Subagent definition
        ├── agent-autonomy.md        # Subagent definition
        ├── opus-planner.md          # Subagent definition
        └── observability.md         # Subagent definition
```

```
```

final `.claude-plugin/` structure:
```
.claude-plugin/
├── plugin.json
├── agents/
│   ├── opus-planner.md
│   ├── video-ingest.md
│   ├── agent-autonomy.md
│   ├── fact-verifier.md
│   └── observability.md
├── skills/
│   ├── ingest-video/SKILL.md      →  /ingest-video "url"
│   ├── find-contradictions/SKILL.md → /find-contradictions "BTC targets"
│   └── deploy/SKILL.md            →  /deploy --build
└── hooks/
    ├── post-edit.sh               →  auto: catches relative imports
    └── post-deploy.sh             →  auto: verifies health after deploy
```

## Deploy to running Docker container

### Option A: Copy into container (quick test, doesn't survive rebuild)

```bash
# From the directory containing this README:

# Core files
docker cp cortex_on/main.py cortexvid-cortex_on-1:/app/main.py
docker cp cortex_on/agent_loop.py cortexvid-cortex_on-1:/app/agent_loop.py

# Agents
docker cp cortex_on/agents/__init__.py cortexvid-cortex_on-1:/app/agents/__init__.py
docker cp cortex_on/agents/opus_planner.py cortexvid-cortex_on-1:/app/agents/opus_planner.py
docker cp cortex_on/agents/video_ingest_agent.py cortexvid-cortex_on-1:/app/agents/video_ingest_agent.py
docker cp cortex_on/agents/observability.py cortexvid-cortex_on-1:/app/agents/observability.py

# Restart
docker-compose restart cortex_on
curl http://localhost:8093/api/v1/health
```

### Option B: Copy to local project (survives rebuild)

```bash
# From your CortexVid project root:

# Core files
cp cortex_on/main.py cortex_on/main.py
cp cortex_on/agent_loop.py cortex_on/agent_loop.py

# Agents
cp cortex_on/agents/__init__.py cortex_on/agents/__init__.py
cp cortex_on/agents/opus_planner.py cortex_on/agents/opus_planner.py
cp cortex_on/agents/video_ingest_agent.py cortex_on/agents/video_ingest_agent.py
cp cortex_on/agents/observability.py cortex_on/agents/observability.py

# Subagent definitions (for Claude Code)
mkdir -p cortex_on/.claude-plugin/agents
cp cortex_on/.claude-plugin/agents/*.md cortex_on/.claude-plugin/agents/

# Rebuild
docker-compose up -d --build cortex_on
curl http://localhost:8093/api/v1/health
```

## What changed in main.py

| Before (1172 lines) | After (928 lines) | Change |
|---|---|---|
| Inline `_process_video()` ~250 lines | 40-line wrapper calling `VideoIngestAgent.ingest_file()` | -210 lines |
| Inline `_download_url()` helper | Removed (now inside VideoIngestAgent) | -30 lines |
| Inline `ingest_folder` with manual copy | Calls `VideoIngestAgent.ingest_folder()` | -25 lines |
| No ingest stats endpoint | `GET /api/v1/ingest/stats` | +5 lines |

All existing API endpoints are unchanged. All existing behavior is preserved.

## Verify after deploy

```bash
# Health check — should show opus_planner: "active"
curl http://localhost:8093/api/v1/health

# Ingest agent stats
curl http://localhost:8093/api/v1/ingest/stats

# Test upload still works
curl -X POST http://localhost:8093/api/v1/videos/upload \
  -F "file=@test_video.mp4"

# Test URL ingest
curl -X POST http://localhost:8093/api/v1/ingest/url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://youtube.com/watch?v=xxx", "title": "test"}'

# Agent status
curl http://localhost:8093/api/v1/agent/status
```
