"""
MCP Server for CortexOS — Opus 4.6 Video Intelligence
=======================================================
Two roles:
  1. CortexOS CLIENT tools — ingest, search, verify, synthesize
  2. CortexOS x402 PROVIDER tools — list pricing, submit payments, autonomous access

Run: python -m mcp.server
Claude Code: claude mcp add cortexos -- python3 -m mcp.server
"""

import asyncio, json, os, sys
from pathlib import Path
from typing import Any, Dict

import aiohttp

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("ERROR: pip install mcp", file=sys.stderr)
    sys.exit(1)

API_BASE = os.getenv("CORTEXOS_API_URL", "http://localhost:8093")
server = Server("cortexos-mcp")


async def _api(method, path, json_body=None, params=None, headers=None, timeout=30):
    url = f"{API_BASE}{path}"
    try:
        async with aiohttp.ClientSession() as s:
            kw = {"timeout": aiohttp.ClientTimeout(total=timeout)}
            if json_body: kw["json"] = json_body
            if params: kw["params"] = params
            if headers: kw["headers"] = headers
            fn = s.get if method == "GET" else s.post
            async with fn(url, **kw) as r:
                if r.status == 402:
                    return {"x402_payment_required": True, **(await r.json())}
                return await r.json()
    except aiohttp.ClientConnectorError:
        return {"error": f"Cannot connect to CortexOS at {API_BASE}"}
    except Exception as e:
        return {"error": str(e)}


async def _api_upload(path, file_path, timeout=60):
    url = f"{API_BASE}{path}"
    try:
        async with aiohttp.ClientSession() as s:
            data = aiohttp.FormData()
            data.add_field("file", open(file_path, "rb"), filename=Path(file_path).name)
            async with s.post(url, data=data, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                return await r.json()
    except Exception as e:
        return {"error": str(e)}


async def _api_with_token(method, path, token=None, params=None, json_body=None, timeout=30):
    headers = {"X-Payment-Token": token} if token else {}
    return await _api(method, path, json_body=json_body, params=params, headers=headers, timeout=timeout)


def _t(result): return [TextContent(type="text", text=json.dumps(result, indent=2))]


@server.list_tools()
async def list_tools():
    return [
        # Video Pipeline
        Tool(name="video_ingest_url", description="Ingest YouTube URL into CortexOS", inputSchema={
            "type": "object", "required": ["url"],
            "properties": {"url": {"type": "string"}, "title": {"type": "string"}, "start_time": {"type": "string"}, "end_time": {"type": "string"}}}),
        Tool(name="video_ingest_file", description="Upload local video file", inputSchema={
            "type": "object", "required": ["video_path"],
            "properties": {"video_path": {"type": "string"}}}),
        Tool(name="video_get_status", description="Get job progress", inputSchema={
            "type": "object", "required": ["job_id"],
            "properties": {"job_id": {"type": "string"}}}),
        Tool(name="video_search", description="Semantic search ($0.01 x402)", inputSchema={
            "type": "object", "required": ["video_id", "query"],
            "properties": {"video_id": {"type": "string"}, "query": {"type": "string"}, "top_k": {"type": "integer", "default": 8}, "payment_token": {"type": "string"}}}),

        # Fact Verification
        Tool(name="verify_video", description="Fact-check video ($0.03/claim x402)", inputSchema={
            "type": "object", "required": ["video_id"],
            "properties": {"video_id": {"type": "string"}, "max_claims": {"type": "integer", "default": 20}, "payment_token": {"type": "string"}}}),
        Tool(name="verify_claim", description="Verify single claim ($0.03 x402)", inputSchema={
            "type": "object", "required": ["text"],
            "properties": {"text": {"type": "string"}, "speaker": {"type": "string"}, "category": {"type": "string"}, "payment_token": {"type": "string"}}}),

        # Synthesis
        Tool(name="synthesize", description="Opus synthesis ($0.03 x402)", inputSchema={
            "type": "object", "required": ["question"],
            "properties": {"question": {"type": "string"}, "video_id": {"type": "string"}, "search_mode": {"type": "string", "default": "hybrid"}, "top_k": {"type": "integer", "default": 8}, "payment_token": {"type": "string"}}}),

        # x402 Provider
        Tool(name="x402_list_resources", description="List priced CortexOS endpoints and costs", inputSchema={"type": "object", "properties": {}}),
        Tool(name="x402_submit_payment", description="Submit payment proof → get access token", inputSchema={
            "type": "object", "required": ["payment_id", "payer_address"],
            "properties": {"payment_id": {"type": "string"}, "payer_address": {"type": "string"}, "tx_hash": {"type": "string"}, "circle_tx_id": {"type": "string"}, "circle_wallet_id": {"type": "string"}, "payment_method": {"type": "string", "default": "arc_direct"}}}),
        Tool(name="x402_get_stats", description="CortexOS x402 revenue and payment stats", inputSchema={"type": "object", "properties": {}}),
        Tool(name="x402_health", description="x402 server health — MongoDB, Circle, network", inputSchema={"type": "object", "properties": {}}),
        Tool(name="x402_autonomous_access", description="Auto pay+access a CortexOS resource in one step", inputSchema={
            "type": "object", "required": ["resource_path", "payer_address"],
            "properties": {"resource_path": {"type": "string"}, "payer_address": {"type": "string"}, "circle_wallet_id": {"type": "string"}}}),

        # Payment tracking
        Tool(name="payments_stats", description="x402 payment agent daily spend", inputSchema={"type": "object", "properties": {}}),
        Tool(name="payments_ledger", description="Payment audit trail", inputSchema={
            "type": "object", "properties": {"limit": {"type": "integer", "default": 50}}}),

        # System
        Tool(name="health_check", description="CortexOS health + agent discovery", inputSchema={"type": "object", "properties": {}}),
        Tool(name="list_agents", description="All auto-discovered agents", inputSchema={"type": "object", "properties": {}}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]):

    # ── Video Pipeline ──────────────────────────────────────────────
    if name == "video_ingest_url":
        body = {"url": arguments["url"]}
        for k in ("title", "start_time", "end_time"):
            if arguments.get(k): body[k] = arguments[k]
        return _t(await _api("POST", "/api/v1/ingest/url", json_body=body, timeout=60))

    if name == "video_ingest_file":
        src = arguments["video_path"]
        if not Path(src).exists():
            return _t({"error": f"File not found: {src}"})
        return _t(await _api_upload("/api/v1/videos/upload", src))

    if name == "video_get_status":
        return _t(await _api("GET", f"/api/v1/jobs/{arguments['job_id']}"))

    if name == "video_search":
        params = {"q": arguments["query"], "top_k": arguments.get("top_k", 8)}
        return _t(await _api_with_token("GET", f"/api/v1/videos/{arguments['video_id']}/search",
                                        token=arguments.get("payment_token"), params=params))

    # ── Fact Verification ───────────────────────────────────────────
    if name == "verify_video":
        body = {"video_id": arguments["video_id"], "max_claims": arguments.get("max_claims", 20)}
        return _t(await _api_with_token("POST", "/api/v1/verify", token=arguments.get("payment_token"),
                                        json_body=body, timeout=120))

    if name == "verify_claim":
        params = {k: arguments[k] for k in ("text", "speaker", "category", "video_id") if arguments.get(k)}
        return _t(await _api_with_token("POST", "/api/v1/verify/claim",
                                        token=arguments.get("payment_token"), params=params))

    # ── Synthesis ───────────────────────────────────────────────────
    if name == "synthesize":
        body = {k: arguments[k] for k in ("question", "search_mode", "top_k", "video_id") if arguments.get(k)}
        return _t(await _api_with_token("POST", "/api/v1/synthesize",
                                        token=arguments.get("payment_token"), json_body=body, timeout=60))

    # ── x402 Provider ───────────────────────────────────────────────
    if name == "x402_list_resources":
        return _t(await _api("GET", "/api/v1/x402/resources"))

    if name == "x402_submit_payment":
        body = {k: arguments[k] for k in ("payment_id", "payer_address", "tx_hash", "circle_tx_id",
                                           "circle_wallet_id", "payment_method") if arguments.get(k)}
        return _t(await _api("POST", "/api/v1/x402/payments/submit", json_body=body))

    if name == "x402_get_stats":
        return _t(await _api("GET", "/api/v1/x402/stats"))

    if name == "x402_health":
        return _t(await _api("GET", "/api/v1/x402/health"))

    if name == "x402_autonomous_access":
        """402 → pay → access in one step."""
        path = arguments["resource_path"]
        payer = arguments["payer_address"]
        wallet_id = arguments.get("circle_wallet_id")

        # Step 1: Request → expect 402
        r1 = await _api("GET", path)
        if not r1.get("x402_payment_required"):
            return _t({"status": "free_access", "data": r1})

        payment = r1.get("payment", {})
        pid = payment.get("payment_id")
        amount = payment.get("amount")

        # Step 2: Submit payment
        pay_body = {"payment_id": pid, "payer_address": payer,
                    "payment_method": "circle_wallet" if wallet_id else "arc_direct",
                    "tx_hash": f"0x_auto_{pid[:16]}"}
        if wallet_id:
            pay_body["circle_wallet_id"] = wallet_id
            pay_body["circle_tx_id"] = f"circle_auto_{pid[:16]}"

        r2 = await _api("POST", "/api/v1/x402/payments/submit", json_body=pay_body)
        if r2.get("status") != "success":
            return _t({"status": "payment_failed", "error": r2})

        token = r2.get("access_token")

        # Step 3: Access with token
        r3 = await _api_with_token("GET", path, token=token)
        return _t({"status": "autonomous_access_complete", "resource": path,
                    "amount_paid": amount, "access_token": token, "data": r3})

    # ── Payment tracking ────────────────────────────────────────────
    if name == "payments_stats":
        return _t(await _api("GET", "/api/v1/payments/stats"))

    if name == "payments_ledger":
        return _t(await _api("GET", "/api/v1/payments/ledger", params={"limit": arguments.get("limit", 50)}))

    # ── System ──────────────────────────────────────────────────────
    if name == "health_check":
        return _t(await _api("GET", "/api/v1/health"))

    if name == "list_agents":
        return _t(await _api("GET", "/api/v1/agents"))

    return _t({"error": f"Unknown tool: {name}"})


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
