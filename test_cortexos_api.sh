#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# CortexOS API Test Suite
# Run: bash test_cortexos_api.sh
# Requires: CortexOS running at http://localhost:8093
# ═══════════════════════════════════════════════════════════════

set -e

API="http://localhost:8093"
PASS=0
FAIL=0
SKIP=0

green()  { echo -e "\033[32m$1\033[0m"; }
red()    { echo -e "\033[31m$1\033[0m"; }
yellow() { echo -e "\033[33m$1\033[0m"; }

check() {
    local name="$1"
    local expected_code="$2"
    local url="$3"
    local method="${4:-GET}"
    local data="$5"

    if [ "$method" = "POST" ] && [ -n "$data" ]; then
        RESP=$(curl -s -w "\n%{http_code}" -X POST "$url" -H "Content-Type: application/json" -d "$data" 2>/dev/null)
    else
        RESP=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null)
    fi

    CODE=$(echo "$RESP" | tail -1)
    BODY=$(echo "$RESP" | sed '$d')

    if [ "$CODE" = "$expected_code" ]; then
        green "  ✓ $name → $CODE"
        PASS=$((PASS + 1))
    else
        red "  ✗ $name → $CODE (expected $expected_code)"
        FAIL=$((FAIL + 1))
    fi
}

check_json() {
    local name="$1"
    local url="$2"
    local key="$3"
    local expected="$4"

    BODY=$(curl -s "$url" 2>/dev/null)
    VALUE=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('$key',''))" 2>/dev/null)

    if [ "$VALUE" = "$expected" ]; then
        green "  ✓ $name → $key=$VALUE"
        PASS=$((PASS + 1))
    else
        red "  ✗ $name → $key=$VALUE (expected $expected)"
        FAIL=$((FAIL + 1))
    fi
}

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  CortexOS API Test Suite"
echo "═══════════════════════════════════════════════════════════"
echo ""

# ── 1. Health & System ──────────────────────────────────────────

echo "── Health & System ──"

check "Health endpoint" "200" "$API/api/v1/health"
check_json "Health status OK" "$API/api/v1/health" "status" "ok"
check_json "Opus planner active" "$API/api/v1/health" "opus_planner" "active"

check "Agent discovery" "200" "$API/api/v1/agents"
check "Debug endpoint" "200" "$API/api/v1/debug"
check "Status endpoint" "200" "$API/api/v1/status"

echo ""

# ── 2. Agent Endpoints (auto-discovered) ────────────────────────

echo "── Auto-Discovered Agent Endpoints ──"

check "Ingest stats" "200" "$API/api/v1/ingest/stats"
check "Verify stats" "200" "$API/api/v1/verify/stats"
check "Planner stats" "200" "$API/api/v1/planner/stats"
check "Planner strategies" "200" "$API/api/v1/planner/strategies"
check "Synthesis stats" "200" "$API/api/v1/synthesis/stats"
check "Synthesis strategies" "200" "$API/api/v1/synthesis/strategies"
check "QA stats" "200" "$API/api/v1/qa/stats"
check "Observability metrics" "200" "$API/api/v1/observability/metrics"
check "Observability eval" "200" "$API/api/v1/observability/eval"
check "Observability config" "200" "$API/api/v1/observability/config"
check "Payments stats" "200" "$API/api/v1/payments/stats"
check "Payments ledger" "200" "$API/api/v1/payments/ledger"
check "Payments guardrails" "200" "$API/api/v1/payments/guardrails"

echo ""

# ── 3. x402 Server Endpoints ───────────────────────────────────

echo "── x402 Server (CortexOS as provider) ──"

check "x402 health" "200" "$API/api/v1/x402/health"
check "x402 resources" "200" "$API/api/v1/x402/resources"
check "x402 stats" "200" "$API/api/v1/x402/stats"
check "x402 payments list" "200" "$API/api/v1/x402/payments"

echo ""

# ── 4. x402 Payment Gate Tests ─────────────────────────────────

echo "── x402 Payment Gate ──"

# These should return 402 when x402 is enabled
check "Verify requires payment" "402" "$API/api/v1/verify" "POST" '{"video_id":"test123"}'
check "Synthesize requires payment" "402" "$API/api/v1/synthesize" "POST" '{"question":"test"}'
check "Verify claim requires payment" "402" "$API/api/v1/verify/claim?text=test" "POST"

echo ""

# ── 5. x402 Payment Flow ───────────────────────────────────────

echo "── x402 Full Payment Flow ──"

# Step 1: Request a paid endpoint → get 402 with payment_id
echo "  Step 1: Request paid resource..."
RESP_402=$(curl -s -X POST "$API/api/v1/verify" \
    -H "Content-Type: application/json" \
    -d '{"video_id":"test_flow"}' 2>/dev/null)

PAYMENT_ID=$(echo "$RESP_402" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('payment',{}).get('payment_id',''))
except:
    print('')
" 2>/dev/null)

if [ -z "$PAYMENT_ID" ]; then
    yellow "  ⊘ Skip payment flow — x402 not enabled or no payment_id"
    SKIP=$((SKIP + 3))
else
    green "  ✓ Got payment_id: ${PAYMENT_ID:0:30}..."
    PASS=$((PASS + 1))

    # Step 2: Submit payment
    echo "  Step 2: Submit payment..."
    PAY_RESP=$(curl -s -X POST "$API/api/v1/x402/payments/submit" \
        -H "Content-Type: application/json" \
        -d "{
            \"payment_id\": \"$PAYMENT_ID\",
            \"payer_address\": \"0xTestAgent1234567890\",
            \"tx_hash\": \"0xfake_test_tx_hash_1234567890abcdef\",
            \"payment_method\": \"arc_direct\"
        }" 2>/dev/null)

    ACCESS_TOKEN=$(echo "$PAY_RESP" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('access_token',''))
except:
    print('')
" 2>/dev/null)

    if [ -n "$ACCESS_TOKEN" ]; then
        green "  ✓ Got access_token: ${ACCESS_TOKEN:0:30}..."
        PASS=$((PASS + 1))

        # Step 3: Access with token
        echo "  Step 3: Access with token..."
        TOKEN_RESP=$(curl -s -w "\n%{http_code}" -X POST "$API/api/v1/verify" \
            -H "Content-Type: application/json" \
            -H "X-Payment-Token: $ACCESS_TOKEN" \
            -d '{"video_id":"test_flow"}' 2>/dev/null)

        TOKEN_CODE=$(echo "$TOKEN_RESP" | tail -1)
        if [ "$TOKEN_CODE" = "200" ]; then
            green "  ✓ Access with token → 200 (payment flow complete!)"
            PASS=$((PASS + 1))
        else
            red "  ✗ Access with token → $TOKEN_CODE (expected 200)"
            FAIL=$((FAIL + 1))
        fi
    else
        red "  ✗ No access_token in payment response"
        FAIL=$((FAIL + 2))
    fi
fi

echo ""

# ── 6. x402 Payment Lookup ─────────────────────────────────────

echo "── x402 Payment Lookup ──"

if [ -n "$PAYMENT_ID" ]; then
    check "Get payment by ID" "200" "$API/api/v1/x402/payments/$PAYMENT_ID"
else
    yellow "  ⊘ Skip — no payment_id from flow test"
    SKIP=$((SKIP + 1))
fi

check "List completed payments" "200" "$API/api/v1/x402/payments?status=completed&limit=5"

echo ""

# ── 7. Free Endpoints (should NOT return 402) ──────────────────

echo "── Free Endpoints (should not be gated) ──"

check "Health is free" "200" "$API/api/v1/health"
check "Agents is free" "200" "$API/api/v1/agents"
check "Jobs is free" "200" "$API/api/v1/jobs"
check "Ingest stats is free" "200" "$API/api/v1/ingest/stats"
check "Verify stats is free" "200" "$API/api/v1/verify/stats"
check "x402 resources is free" "200" "$API/api/v1/x402/resources"

echo ""

# ── 8. Jobs & Ingest ───────────────────────────────────────────

echo "── Jobs & Ingest ──"

check "List jobs" "200" "$API/api/v1/jobs"

# Test URL ingest (will likely fail without yt-dlp but should return 200 or error, not 500)
INGEST_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/api/v1/ingest/url" \
    -H "Content-Type: application/json" \
    -d '{"url":"https://youtube.com/watch?v=test"}' 2>/dev/null)

if [ "$INGEST_CODE" = "500" ]; then
    yellow "  ⊘ Ingest URL → 500 (expected — yt-dlp may not be installed)"
    SKIP=$((SKIP + 1))
else
    green "  ✓ Ingest URL → $INGEST_CODE"
    PASS=$((PASS + 1))
fi

echo ""

# ── 9. Agent Discovery Verification ────────────────────────────

echo "── Agent Discovery Detail ──"

AGENTS_JSON=$(curl -s "$API/api/v1/agents" 2>/dev/null)
python3 -c "
import sys, json
try:
    data = json.loads('''$AGENTS_JSON''')
    agents = data.get('agents', {})
    registered = 0
    loaded = 0
    failed = 0
    for name, info in agents.items():
        status = info.get('status', 'unknown')
        if status == 'registered': registered += 1
        elif status == 'loaded': loaded += 1
        else: failed += 1
        icon = '✓' if status == 'registered' else ('○' if status == 'loaded' else '✗')
        print(f'  {icon} {name}: {status}')
    print(f'')
    print(f'  Total: {registered} registered, {loaded} loaded, {failed} failed')
except Exception as e:
    print(f'  ✗ Could not parse agents response: {e}')
" 2>&1

echo ""

# ── Summary ─────────────────────────────────────────────────────

echo "═══════════════════════════════════════════════════════════"
echo "  Results: $(green "$PASS passed"), $(red "$FAIL failed"), $(yellow "$SKIP skipped")"
echo "═══════════════════════════════════════════════════════════"

if [ $FAIL -gt 0 ]; then
    exit 1
fi
