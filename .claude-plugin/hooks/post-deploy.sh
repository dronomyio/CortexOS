#!/bin/bash
# CortexOS post-deploy hook
# Fires after any docker-compose command
# Verifies health, Opus planner, agent discovery

set -e

API_URL="${CORTEXOS_API_URL:-http://localhost:8093}"
MAX_WAIT=30

echo ""
echo "═══ CortexOS Post-Deploy Verification ═══"
echo ""

# Wait for API
echo "Waiting for CortexOS API..."
STARTED=false
for i in $(seq 1 $MAX_WAIT); do
    if curl -s --max-time 2 "$API_URL/api/v1/health" >/dev/null 2>&1; then
        STARTED=true
        echo "  ✓ API responding (${i}s)"
        break
    fi
    sleep 1
done

if [ "$STARTED" = false ]; then
    echo "  ✗ API not responding after ${MAX_WAIT}s"
    echo "  docker ps | grep cortex_on"
    echo "  docker-compose logs cortex_on --tail 20"
    exit 1
fi

# Health check
HEALTH=$(curl -s "$API_URL/api/v1/health")

python3 -c "
import sys, json

h = json.loads('''$HEALTH''')

checks = []
checks.append(('API Status', h.get('status') == 'ok'))
checks.append(('Opus Planner', h.get('opus_planner') == 'active'))
checks.append(('Model', 'claude' in h.get('model', '')))

agents = h.get('discovered_agents', {})
registered = sum(1 for v in agents.values() if v == 'registered')
failed_agents = [k for k, v in agents.items() if v == 'failed']
checks.append(('Agent Discovery', len(failed_agents) == 0))

obs = h.get('observability', {})
checks.append(('Observability', obs.get('enabled', False)))

all_ok = True
for name, ok in checks:
    icon = '✓' if ok else '✗'
    print(f'  {icon} {name}')
    if not ok: all_ok = False

print(f'  ✓ Agents: {registered} registered / {len(agents)} total')

if failed_agents:
    print(f'  ⚠️  Failed: {chr(44).join(failed_agents)}')

# Smoke test
print('')
print('Endpoint check:')
import urllib.request
for ep in ['health', 'agents', 'ingest/stats', 'verify/stats', 'x402/health']:
    try:
        req = urllib.request.urlopen(f'$API_URL/api/v1/{ep}', timeout=3)
        print(f'  ✓ /api/v1/{ep} → {req.status}')
    except Exception as e:
        print(f'  ✗ /api/v1/{ep} → {e}')
        all_ok = False

print('')
if all_ok:
    print('🟢 DEPLOY VERIFIED — CortexOS healthy')
elif h.get('opus_planner') == 'active':
    print('🟡 DEPLOY OK — some non-critical issues')
else:
    print('🔴 DEPLOY FAILED — Opus planner inactive')
    sys.exit(1)
" 2>&1

echo ""
echo "═══ Post-deploy complete ═══"
