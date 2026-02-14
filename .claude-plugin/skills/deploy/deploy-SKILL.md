# /deploy

Build, restart, and verify CortexOS Docker deployment.

## Usage

```
/deploy
/deploy --build
/deploy --build --no-cache
/deploy --check-only
/deploy --logs
```

## Parameters
- `--build` — Force rebuild (needed after Dockerfile or requirements.txt changes)
- `--no-cache` — Full rebuild from scratch (needed after base image changes)
- `--check-only` — Just run health checks, don't restart anything
- `--logs` — Show last 50 lines of cortex_on logs after deploy

## Steps

### 1. Pre-deploy checks
```bash
cd /Users/macmachine/tools/drone_project_idea/Blogs/Video_understanding/CortexVid

# Check .env exists with required vars
python3 -c "
from pathlib import Path
env = Path('.env')
if not env.exists():
    print('WARNING: .env file not found')
else:
    text = env.read_text()
    required = ['ANTHROPIC_API_KEY']
    for var in required:
        if var not in text:
            print(f'WARNING: {var} not set in .env')
    print('.env OK')
"

# Check for relative imports in agents (common deploy breaker)
echo "Checking for relative imports..."
RELIMPORTS=$(grep -rn "from \.\." cortex_on/agents/*.py 2>/dev/null | grep -v __pycache__)
if [ -n "$RELIMPORTS" ]; then
    echo "⚠️  RELATIVE IMPORTS FOUND — these will break auto-discovery:"
    echo "$RELIMPORTS"
    echo "Fix with: sed -i '' 's/from \.\./from /g' <file>"
    exit 1
fi
echo "  ✓ No relative imports"
```

### 2. Build (if --build or --no-cache)
```bash
# Standard rebuild
docker-compose build cortex_on

# OR full rebuild (--no-cache)
docker-compose build --no-cache cortex_on
```

### 3. Restart
```bash
# If --build was used:
docker-compose up -d cortex_on

# Otherwise just restart (bind mount picks up file changes):
docker-compose restart cortex_on
```

### 4. Wait for startup
```bash
echo "Waiting for CortexOS to start..."
for i in $(seq 1 30); do
    HEALTH=$(curl -s --max-time 3 http://localhost:8093/api/v1/health 2>/dev/null)
    if echo "$HEALTH" | python3 -c "import sys,json; h=json.load(sys.stdin); exit(0 if h.get('status')=='ok' else 1)" 2>/dev/null; then
        echo "  ✓ CortexOS responding after ${i}s"
        break
    fi
    sleep 1
done
```

### 5. Verify health
```bash
HEALTH=$(curl -s http://localhost:8093/api/v1/health)
echo "$HEALTH" | python3 -c "
import sys, json
h = json.load(sys.stdin)

checks = {
    'API Status': h.get('status') == 'ok',
    'Opus Planner': h.get('opus_planner') == 'active',
    'Model': 'claude' in h.get('model', ''),
    'Observability': h.get('observability', {}).get('enabled', False),
}

print('═══ CortexOS Health Check ═══')
all_ok = True
for name, ok in checks.items():
    icon = '✓' if ok else '✗'
    print(f'  {icon} {name}: {\"OK\" if ok else \"FAILED\"}')
    if not ok: all_ok = False

# Agent discovery
agents = h.get('discovered_agents', {})
registered = sum(1 for v in agents.values() if v == 'registered')
failed = sum(1 for v in agents.values() if v == 'failed')
print(f'  ✓ Agents: {registered} registered, {len(agents) - registered - failed} loaded, {failed} failed')

if failed > 0:
    print(f'  ⚠️  Failed agents:')
    for name, status in agents.items():
        if status == 'failed':
            print(f'      - {name}')

if all_ok and failed == 0:
    print('\\n  🟢 DEPLOY SUCCESS')
elif all_ok:
    print('\\n  🟡 DEPLOY OK (some agents failed)')
else:
    print('\\n  🔴 DEPLOY FAILED')
    sys.exit(1)
"
```

### 6. Verify agents
```bash
curl -s http://localhost:8093/api/v1/agents | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f\"\\nAgents: {data['registered']}/{data['total']} registered\")
for name, info in data['agents'].items():
    status = info.get('status', 'unknown')
    icon = '✓' if status == 'registered' else ('○' if status == 'loaded' else '✗')
    routes = ' (routes)' if info.get('has_routes') else ''
    print(f'  {icon} {name}{routes}')
"
```

### 7. (Optional) Show logs
```bash
docker-compose logs cortex_on --tail 50
```

### 8. Quick smoke test
```bash
echo ""
echo "Smoke test..."

# Test key endpoints
for endpoint in health agents planner/strategies payments/guardrails verify/stats ingest/stats; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8093/api/v1/$endpoint)
    if [ "$STATUS" = "200" ]; then
        echo "  ✓ /api/v1/$endpoint → 200"
    else
        echo "  ✗ /api/v1/$endpoint → $STATUS"
    fi
done
```

## Error Handling
- If Docker not running → "Docker Desktop not running. Start it first."
- If port 8093 busy → "Port conflict. Check: docker ps"
- If Opus planner inactive → "Check ANTHROPIC_API_KEY in .env"
- If agents fail → "Relative import detected. Run pre-deploy check."

## Post-Deploy
After successful deploy, the following endpoints are live:
```
Core:         /health, /agents, /status, /debug
Ingest:       /videos/upload, /ingest/url, /ingest/urls, /ingest/stats
Search:       /videos/{id}/search, /synthesize
Verify:       /verify, /verify/{id}/report, /verify/claim, /verify/stats
Planner:      /planner/stats, /planner/strategies
Payments:     /payments/stats, /payments/ledger, /payments/guardrails
Observability:/observability/metrics, /observability/eval, /observability/config
QA:           /qa/ask, /qa/stats
Synthesis:    /synthesis/strategies, /synthesis/stats
Jobs:         /jobs, /jobs/{id}, /jobs/{id}/plan
Agent:        /agent/start, /agent/stop, /agent/status, /agent/alerts
```
