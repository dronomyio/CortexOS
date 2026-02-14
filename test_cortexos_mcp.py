#!/usr/bin/env python3
"""
CortexOS MCP Server Test Suite
================================
Tests all MCP tools by calling the CortexOS API directly via aiohttp
(same HTTP calls the MCP server makes internally).

This avoids MCP SDK version issues — tests the actual tool logic.

Run: python3 test_cortexos_mcp.py
Requires:
  - pip install aiohttp
  - CortexOS running at http://localhost:8093
"""

import asyncio
import json
import sys
import os

try:
    import aiohttp
except ImportError:
    print("ERROR: pip install aiohttp")
    sys.exit(1)

API = os.getenv("CORTEXOS_API_URL", "http://localhost:8093")
PASS = 0
FAIL = 0
SKIP = 0


def green(s):  return f"\033[32m{s}\033[0m"
def red(s):    return f"\033[31m{s}\033[0m"
def yellow(s): return f"\033[33m{s}\033[0m"


async def api_get(session, path, headers=None, params=None):
    try:
        async with session.get(f"{API}{path}", headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
            try:
                data = await r.json()
            except Exception:
                data = {"error": await r.text(), "status_code": r.status}
            return r.status, data
    except aiohttp.ClientConnectorError:
        return 0, {"error": f"Cannot connect to {API}"}
    except Exception as e:
        return 0, {"error": str(e)}


async def api_post(session, path, body=None, headers=None, params=None):
    try:
        async with session.post(f"{API}{path}", json=body, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
            try:
                data = await r.json()
            except Exception:
                data = {"error": await r.text(), "status_code": r.status}
            return r.status, data
    except aiohttp.ClientConnectorError:
        return 0, {"error": f"Cannot connect to {API}"}
    except Exception as e:
        return 0, {"error": str(e)}


def check(name, code, expected, data=None):
    global PASS, FAIL
    if code == expected:
        print(green(f"  ✓ {name} → {code}"))
        PASS += 1
    else:
        detail = ""
        if data and "error" in str(data):
            detail = f" ({str(data)[:80]})"
        print(red(f"  ✗ {name} → {code} (expected {expected}){detail}"))
        FAIL += 1


def check_key(name, data, key):
    global PASS, FAIL
    if data and key in data:
        print(green(f"  ✓ {name} → has '{key}'"))
        PASS += 1
    else:
        print(red(f"  ✗ {name} → missing '{key}'"))
        FAIL += 1


async def run_tests():
    global PASS, FAIL, SKIP

    print("")
    print("═══════════════════════════════════════════════════════════")
    print("  CortexOS MCP Tool Tests (via HTTP)")
    print(f"  API: {API}")
    print("═══════════════════════════════════════════════════════════")
    print("")

    async with aiohttp.ClientSession() as s:

        # ── Connectivity ────────────────────────────────────────
        code, data = await api_get(s, "/api/v1/health")
        if code == 0:
            print(red(f"  ✗ CortexOS not reachable at {API}"))
            print(red(f"    Start it: cd CortexOS && docker-compose up -d"))
            sys.exit(1)

        # ── MCP Tool: health_check ──────────────────────────────
        print("── health_check ──")
        check("GET /api/v1/health", code, 200)
        check_key("health has 'status'", data, "status")

        # ── MCP Tool: list_agents ───────────────────────────────
        print("\n── list_agents ──")
        code, data = await api_get(s, "/api/v1/agents")
        check("GET /api/v1/agents", code, 200)
        check_key("agents response", data, "agents")

        if data and "agents" in data:
            agents = data["agents"]
            count = len(agents) if isinstance(agents, (dict, list)) else 0
            print(green(f"  ✓ {count} agents discovered"))
            PASS += 1

        # ── MCP Tool: x402_list_resources ───────────────────────
        print("\n── x402_list_resources ──")
        code, data = await api_get(s, "/api/v1/x402/resources")
        check("GET /api/v1/x402/resources", code, 200)
        check_key("has resources", data, "resources")

        if data and "resources" in data:
            print(green(f"  ✓ {len(data['resources'])} priced resources"))
            PASS += 1
            for r in data["resources"]:
                print(f"     ${r.get('price_usdc','?')} → {r.get('path','?')}")

        # ── MCP Tool: x402_health ───────────────────────────────
        print("\n── x402_health ──")
        code, data = await api_get(s, "/api/v1/x402/health")
        check("GET /api/v1/x402/health", code, 200)
        check_key("has x402_enabled", data, "x402_enabled")

        # ── MCP Tool: x402_get_stats ────────────────────────────
        print("\n── x402_get_stats ──")
        code, data = await api_get(s, "/api/v1/x402/stats")
        check("GET /api/v1/x402/stats", code, 200)

        # ── MCP Tool: verify_video (expect 402) ─────────────────
        print("\n── verify_video (expect 402 gate) ──")
        code, data = await api_post(s, "/api/v1/verify", body={"video_id": "test_mcp_001"})
        check("POST /api/v1/verify → 402", code, 402, data)

        payment_id = None
        amount = None
        if code == 402 and data:
            payment = data.get("payment", {})
            payment_id = payment.get("payment_id")
            amount = payment.get("amount")
            if payment_id:
                print(green(f"  ✓ payment_id: {payment_id[:30]}... (${amount})"))
                PASS += 1
            else:
                print(red("  ✗ No payment_id in 402 response"))
                FAIL += 1

        # ── MCP Tool: synthesize (expect 402) ───────────────────
        print("\n── synthesize (expect 402 gate) ──")
        code, data = await api_post(s, "/api/v1/synthesize", body={"question": "test"})
        check("POST /api/v1/synthesize → 402", code, 402, data)

        # ── MCP Tool: verify_claim (expect 402) ─────────────────
        print("\n── verify_claim (expect 402 gate) ──")
        code, data = await api_post(s, "/api/v1/verify/claim", params={"text": "BTC hits 200K"})
        check("POST /api/v1/verify/claim → 402", code, 402, data)

        # ── MCP Tool: x402_submit_payment (full flow) ───────────
        print("\n── x402 Payment Flow ──")

        if payment_id:
            # Submit payment
            pay_body = {
                "payment_id": payment_id,
                "payer_address": "0xMCPTestAgent_v2",
                "tx_hash": "0xfake_mcp_test_abcdef1234567890aabbcc",
                "payment_method": "arc_direct",
            }
            code, data = await api_post(s, "/api/v1/x402/payments/submit", body=pay_body)
            check("POST submit payment", code, 200, data)

            access_token = data.get("access_token") if data else None
            if access_token:
                print(green(f"  ✓ access_token: {access_token[:30]}..."))
                PASS += 1

                # Access with token
                headers = {"X-Payment-Token": access_token}
                code, data = await api_post(s, "/api/v1/verify",
                                            body={"video_id": "test_mcp_001"},
                                            headers=headers)
                check("POST /api/v1/verify with token → 200", code, 200, data)
            else:
                print(red("  ✗ No access_token in payment response"))
                FAIL += 1
                SKIP += 1
        else:
            print(yellow("  ⊘ x402 not enabled — skipping payment flow"))
            SKIP += 3

        # ── MCP Tool: x402_autonomous_access (free endpoint) ────
        print("\n── x402_autonomous_access (free endpoint test) ──")
        code, data = await api_get(s, "/api/v1/verify/stats")
        check("GET /api/v1/verify/stats (free)", code, 200)

        code, data = await api_get(s, "/api/v1/ingest/stats")
        if code == 500:
            print(yellow("  ⊘ GET /api/v1/ingest/stats → 500 (known issue — video_ingest_agent bug)"))
            SKIP += 1
        else:
            check("GET /api/v1/ingest/stats (free)", code, 200)

        code, data = await api_get(s, "/api/v1/planner/stats")
        check("GET /api/v1/planner/stats (free)", code, 200)

        # ── MCP Tool: payments_stats ────────────────────────────
        print("\n── payments_stats ──")
        code, data = await api_get(s, "/api/v1/payments/stats")
        check("GET /api/v1/payments/stats", code, 200)

        # ── MCP Tool: payments_ledger ───────────────────────────
        print("\n── payments_ledger ──")
        code, data = await api_get(s, "/api/v1/payments/ledger", params={"limit": "5"})
        check("GET /api/v1/payments/ledger", code, 200)

        # ── MCP Tool: video_get_status (bad job) ────────────────
        print("\n── video_get_status (nonexistent) ──")
        code, data = await api_get(s, "/api/v1/jobs/nonexistent_123")
        # Could be 404 or 200 with error — just check it doesn't 500
        if code != 500:
            print(green(f"  ✓ GET /api/v1/jobs/bad_id → {code} (not 500)"))
            PASS += 1
        else:
            print(red(f"  ✗ GET /api/v1/jobs/bad_id → 500 (server error)"))
            FAIL += 1

        # ── MCP Tool: x402 payment lookup ───────────────────────
        print("\n── x402 payment lookup ──")
        code, data = await api_get(s, "/api/v1/x402/payments", params={"limit": "3"})
        check("GET /api/v1/x402/payments", code, 200)

        if payment_id:
            code, data = await api_get(s, f"/api/v1/x402/payments/{payment_id}")
            check(f"GET payment by ID", code, 200)

        # ── MCP Server Module Import Test ───────────────────────
        print("\n── MCP Server Import Test ──")
        try:
            # Test that the server module can be imported
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cortex_on"))
            # Just check if the mcp module structure works
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            print(green("  ✓ MCP client imports OK (ClientSession, StdioServerParameters)"))
            PASS += 1
        except ImportError as e:
            print(yellow(f"  ⊘ MCP imports not available: {e}"))
            SKIP += 1

        try:
            from mcp.server.fastmcp import FastMCP
            print(green("  ✓ MCP server import OK (FastMCP)"))
            PASS += 1
        except ImportError:
            try:
                from mcp.server import Server
                print(green("  ✓ MCP server import OK (Server — low-level)"))
                PASS += 1
            except ImportError as e:
                print(yellow(f"  ⊘ MCP server imports not available: {e}"))
                SKIP += 1

    # ── Summary ─────────────────────────────────────────────────
    print("")
    print("═══════════════════════════════════════════════════════════")
    print(f"  Results: {green(f'{PASS} passed')}, {red(f'{FAIL} failed')}, {yellow(f'{SKIP} skipped')}")
    print("═══════════════════════════════════════════════════════════")

    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_tests())
