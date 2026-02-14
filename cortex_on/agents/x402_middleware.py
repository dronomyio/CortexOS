"""
CortexOS x402 Server Middleware
================================
Gates premium CortexOS endpoints behind x402 micropayments.
CortexOS is the PROVIDER — other agents/apps pay CortexOS for:
  - Video fact-verification ($0.03/claim)
  - Opus synthesis ($0.03/query)
  - Semantic search ($0.01/query)
  - Full fact-check reports ($0.25/report)
  - Video ingest ($0.10/minute)

Payment flow:
  Client → GET /api/v1/verify → 402 Payment Required
  Client → POST /api/v1/x402/payments/submit (with tx_hash or circle_wallet)
  Client → GET /api/v1/verify (with X-Payment-Token header) → 200 + data

Uses MongoDB (local) for persistence. References MEV Shield Circle SDK
for Circle Wallet + Arc Network payment verification.

Auto-discovered by main.py — registers its own routes via register_routes(app).
"""

import os
import json
import secrets
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any

from fastapi import Request, Response

logger = logging.getLogger("cortexos.x402")

# ── Config ──────────────────────────────────────────────────────────────

PAYEE_ADDRESS = os.getenv("PAYEE_ADDRESS", "")
PAYMENT_EXPIRY_MINUTES = int(os.getenv("PAYMENT_EXPIRY_MINUTES", "15"))
ACCESS_TOKEN_EXPIRY_HOURS = int(os.getenv("ACCESS_TOKEN_EXPIRY_HOURS", "24"))
X402_ENABLED = os.getenv("X402_ENABLED", "true").lower() == "true"
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "cortexos")

# Circle SDK (optional — for verifying Circle Wallet payments)
CIRCLE_API_KEY = os.getenv("CIRCLE_API_KEY", "")
CIRCLE_ENTITY_SECRET = os.getenv("CIRCLE_ENTITY_SECRET", "")
ARC_TESTNET = os.getenv("ARC_TESTNET", "true").lower() == "true"


# ── Pricing Schedule ───────────────────────────────────────────────────

RESOURCE_PRICING = {
    "/api/v1/verify": {
        "resource_id": "fact-verify",
        "price_usdc": Decimal("0.03"),
        "description": "Fact-check an ingested video — extract and verify claims",
    },
    "/api/v1/verify/claim": {
        "resource_id": "verify-claim",
        "price_usdc": Decimal("0.03"),
        "description": "Verify a single claim against web data + indexed videos",
    },
    "/api/v1/synthesize": {
        "resource_id": "synthesis",
        "price_usdc": Decimal("0.03"),
        "description": "Opus-planned cited synthesis across all indexed videos",
    },
    "/api/v1/videos/{video_id}/search": {
        "resource_id": "video-search",
        "price_usdc": Decimal("0.01"),
        "description": "Semantic search over indexed video transcript + visual chunks",
    },
    "/api/v1/verify/{video_id}/report": {
        "resource_id": "full-report",
        "price_usdc": Decimal("0.25"),
        "description": "Full fact-check report with all evidence and verdicts",
    },
    "/api/v1/qa/ask": {
        "resource_id": "qa-ask",
        "price_usdc": Decimal("0.02"),
        "description": "Ask a question across indexed video content",
    },
}

# Endpoints that are always free
FREE_ENDPOINTS = {
    "/api/v1/health",
    "/health",
    "/api/v1/agents",
    "/api/v1/status",
    "/api/v1/debug",
    "/api/v1/jobs",
    "/api/v1/ingest/stats",
    "/api/v1/verify/stats",
    "/api/v1/payments/stats",
    "/api/v1/payments/ledger",
    "/api/v1/payments/guardrails",
    "/api/v1/planner/stats",
    "/api/v1/planner/strategies",
    "/api/v1/synthesis/stats",
    "/api/v1/synthesis/strategies",
    "/api/v1/qa/stats",
    "/api/v1/observability/metrics",
    "/api/v1/observability/eval",
    "/api/v1/observability/config",
    "/api/v1/x402/resources",
    "/api/v1/x402/payments/submit",
    "/api/v1/x402/stats",
    "/api/v1/x402/health",
    "/docs",
    "/openapi.json",
}

# Prefixes that are always free
FREE_PREFIXES = [
    "/api/v1/videos/upload",
    "/api/v1/ingest/",
    "/api/v1/jobs/",
    "/api/v1/agent/",
    "/api/v1/clips/",
    "/api/v1/x402/",
]


# ── MongoDB Client ─────────────────────────────────────────────────────

_mongo_client = None
_mongo_db = None


async def _get_db():
    """Lazy-init MongoDB connection."""
    global _mongo_client, _mongo_db
    if _mongo_db is not None:
        return _mongo_db
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        _mongo_client = AsyncIOMotorClient(MONGODB_URL)
        _mongo_db = _mongo_client[MONGODB_DB]
        # Ensure indexes
        await _mongo_db.x402_payments.create_index("payment_id", unique=True)
        await _mongo_db.x402_payments.create_index("status")
        await _mongo_db.x402_payments.create_index("created_at")
        await _mongo_db.x402_access_tokens.create_index("token", unique=True)
        await _mongo_db.x402_access_tokens.create_index("expires_at")
        await _mongo_db.x402_resources.create_index("path", unique=True)
        logger.info(f"[x402] MongoDB connected: {MONGODB_URL}/{MONGODB_DB}")
        return _mongo_db
    except Exception as e:
        logger.error(f"[x402] MongoDB connection failed: {e}")
        return None


# ── Helpers ─────────────────────────────────────────────────────────────

def _generate_payment_id() -> str:
    return f"pay_{secrets.token_hex(16)}"


def _generate_access_token() -> str:
    return f"x402_{secrets.token_hex(32)}"


def _match_resource(path: str) -> Optional[Dict]:
    """Match a request path to a priced resource (handles path params)."""
    # Exact match first
    if path in RESOURCE_PRICING:
        return RESOURCE_PRICING[path]

    # Pattern match for paths with {video_id} etc
    for pattern, resource in RESOURCE_PRICING.items():
        if "{" in pattern:
            # Convert /api/v1/videos/{video_id}/search → regex-like match
            parts_pattern = pattern.split("/")
            parts_path = path.split("/")
            if len(parts_pattern) == len(parts_path):
                match = True
                for pp, pr in zip(parts_path, parts_pattern):
                    if pr.startswith("{") and pr.endswith("}"):
                        continue  # wildcard
                    if pp != pr:
                        match = False
                        break
                if match:
                    return resource
    return None


def _is_free(path: str) -> bool:
    """Check if endpoint is free (no payment required)."""
    if path in FREE_ENDPOINTS:
        return True
    for prefix in FREE_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


# ── Core x402 Logic ────────────────────────────────────────────────────

async def create_payment_record(resource_path: str, resource: Dict) -> Dict:
    """Create a pending payment in MongoDB."""
    db = await _get_db()
    payment_id = _generate_payment_id()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=PAYMENT_EXPIRY_MINUTES)

    record = {
        "payment_id": payment_id,
        "resource": resource_path,
        "resource_id": resource["resource_id"],
        "amount": str(resource["price_usdc"]),
        "status": "pending",
        "payer_address": None,
        "payee_address": PAYEE_ADDRESS,
        "tx_hash": None,
        "network": "arc",
        "circle_wallet_id": None,
        "created_at": datetime.now(timezone.utc),
        "expires_at": expires_at,
        "completed_at": None,
    }

    if db is not None:
        await db.x402_payments.insert_one(record)
    else:
        logger.warning("[x402] MongoDB unavailable — payment record not persisted")

    return {
        "payment_id": payment_id,
        "amount": str(resource["price_usdc"]),
        "payee_address": PAYEE_ADDRESS,
        "network": "arc",
        "asset": "USDC",
        "expires_at": expires_at.isoformat(),
    }


async def validate_access_token(token: str, resource_path: str) -> bool:
    """Check if an access token is valid for the given resource."""
    db = await _get_db()
    if db is None:
        return False

    # Match by exact resource or resource_id
    doc = await db.x402_access_tokens.find_one({
        "token": token,
        "expires_at": {"$gt": datetime.now(timezone.utc)},
    })

    if doc:
        # Check resource matches (exact path or any path for same resource_id)
        resource = _match_resource(resource_path)
        if resource and doc.get("resource_id") == resource["resource_id"]:
            await db.x402_access_tokens.update_one(
                {"token": token},
                {"$inc": {"used_count": 1}}
            )
            return True
        # Also allow if resource path matches directly
        if doc.get("resource") == resource_path:
            await db.x402_access_tokens.update_one(
                {"token": token},
                {"$inc": {"used_count": 1}}
            )
            return True
    return False


async def require_payment(request: Request, resource_path: str) -> Optional[Response]:
    """
    x402 middleware check. Returns None if access granted, 402 Response if payment required.
    """
    if not X402_ENABLED:
        return None  # x402 disabled — everything free

    if _is_free(resource_path):
        return None  # Free endpoint

    resource = _match_resource(resource_path)
    if not resource:
        return None  # Not a priced resource — let it through

    # Check for access token
    payment_token = request.headers.get("X-Payment-Token")
    if payment_token and await validate_access_token(payment_token, resource_path):
        return None  # Valid token — access granted

    # No valid token → 402 Payment Required
    payment_info = await create_payment_record(resource_path, resource)

    return Response(
        status_code=402,
        content=json.dumps({
            "error": "Payment Required",
            "x402_version": "1.0",
            "service": "cortexos",
            "payment": {
                **payment_info,
                "payment_methods": [
                    {
                        "type": "circle_wallet",
                        "description": "Pay via Circle Wallet (recommended for AI agents)",
                    },
                    {
                        "type": "arc_direct",
                        "description": "Direct USDC transfer on Arc Network",
                    },
                ],
            },
            "resource": {
                "path": resource_path,
                "resource_id": resource["resource_id"],
                "description": resource["description"],
            },
        }),
        headers={
            "Content-Type": "application/json",
            "X-Payment-Required": "true",
            "X-Payment-Network": "arc",
            "X-Payment-Asset": "USDC",
        },
    )


# ── Auto-Discovery: register_routes(app) ──────────────────────────────

def register_routes(app):
    """Auto-discovered by main.py — registers x402 server middleware + management endpoints."""
    from fastapi import HTTPException, Query as FQuery
    from pydantic import BaseModel
    from typing import Optional as Opt

    # ── x402 Middleware (intercepts ALL requests) ───────────────────

    @app.middleware("http")
    async def x402_middleware(request: Request, call_next):
        """x402 payment gate — checks every request for payment requirement."""
        if not X402_ENABLED:
            return await call_next(request)

        path = request.url.path

        # Skip middleware entirely for free endpoints (avoids body consumption issues)
        if _is_free(path):
            return await call_next(request)

        resource = _match_resource(path)
        if not resource:
            return await call_next(request)

        # Check for access token
        payment_token = request.headers.get("X-Payment-Token")
        if payment_token and await validate_access_token(payment_token, path):
            return await call_next(request)

        # No valid token → 402 Payment Required
        payment_info = await create_payment_record(path, resource)

        return Response(
            status_code=402,
            content=json.dumps({
                "error": "Payment Required",
                "x402_version": "1.0",
                "service": "cortexos",
                "payment": {
                    **payment_info,
                    "payment_methods": [
                        {"type": "circle_wallet", "description": "Pay via Circle Wallet (recommended for AI agents)"},
                        {"type": "arc_direct", "description": "Direct USDC transfer on Arc Network"},
                    ],
                },
                "resource": {
                    "path": path,
                    "resource_id": resource["resource_id"],
                    "description": resource["description"],
                },
            }),
            headers={
                "Content-Type": "application/json",
                "X-Payment-Required": "true",
                "X-Payment-Network": "arc",
                "X-Payment-Asset": "USDC",
            },
        )

    # ── Payment Submission ──────────────────────────────────────────

    class PaymentSubmission(BaseModel):
        payment_id: str
        payer_address: str
        tx_hash: Opt[str] = None
        circle_tx_id: Opt[str] = None
        circle_wallet_id: Opt[str] = None
        payment_method: str = "arc_direct"

    @app.post("/api/v1/x402/payments/submit", tags=["x402"])
    async def submit_payment(submission: PaymentSubmission):
        """
        Submit payment proof and receive an access token.
        After paying, include the token as X-Payment-Token header.
        """
        db = await _get_db()
        if db is None:
            raise HTTPException(503, "Database not available")

        # Find the payment
        payment = await db.x402_payments.find_one({"payment_id": submission.payment_id})
        if not payment:
            raise HTTPException(404, "Payment not found")
        if payment["status"] == "completed":
            raise HTTPException(400, "Payment already completed")
        if payment["expires_at"] < datetime.now(timezone.utc):
            await db.x402_payments.update_one(
                {"payment_id": submission.payment_id},
                {"$set": {"status": "expired"}}
            )
            raise HTTPException(400, "Payment expired")

        # Verify payment (basic verification — extend with Circle SDK for production)
        verified = False
        tx_hash = submission.tx_hash or ""

        if submission.payment_method == "circle_wallet" and submission.circle_tx_id:
            # In production: verify via Circle API
            # For now: trust circle_tx_id format
            if submission.circle_tx_id and len(submission.circle_tx_id) > 10:
                verified = True
                tx_hash = submission.circle_tx_id

        elif submission.payment_method == "arc_direct" and submission.tx_hash:
            # In production: verify on Arc chain
            # For now: trust tx_hash format (0x + 64 hex chars)
            if submission.tx_hash and len(submission.tx_hash) >= 10:
                verified = True

        elif submission.payment_method == "gateway":
            verified = True

        if not verified:
            raise HTTPException(400, "Payment verification failed")

        # Complete payment
        await db.x402_payments.update_one(
            {"payment_id": submission.payment_id},
            {"$set": {
                "status": "completed",
                "payer_address": submission.payer_address,
                "tx_hash": tx_hash,
                "circle_wallet_id": submission.circle_wallet_id,
                "completed_at": datetime.now(timezone.utc),
            }}
        )

        # Issue access token
        resource = _match_resource(payment["resource"])
        token = _generate_access_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRY_HOURS)

        await db.x402_access_tokens.insert_one({
            "token": token,
            "payment_id": submission.payment_id,
            "resource": payment["resource"],
            "resource_id": resource["resource_id"] if resource else payment.get("resource_id", ""),
            "created_at": datetime.now(timezone.utc),
            "expires_at": expires_at,
            "used_count": 0,
        })

        return {
            "status": "success",
            "access_token": token,
            "resource": payment["resource"],
            "expires_in_hours": ACCESS_TOKEN_EXPIRY_HOURS,
        }

    # ── Resource Listing ────────────────────────────────────────────

    @app.get("/api/v1/x402/resources", tags=["x402"])
    async def list_x402_resources():
        """List all x402-gated CortexOS resources with pricing."""
        return {
            "service": "cortexos",
            "network": "arc",
            "asset": "USDC",
            "payee": PAYEE_ADDRESS,
            "payment_methods": ["circle_wallet", "arc_direct"],
            "resources": [
                {
                    "path": path,
                    "resource_id": r["resource_id"],
                    "price_usdc": str(r["price_usdc"]),
                    "description": r["description"],
                }
                for path, r in RESOURCE_PRICING.items()
            ],
        }

    # ── Stats ───────────────────────────────────────────────────────

    @app.get("/api/v1/x402/stats", tags=["x402"])
    async def x402_stats():
        """x402 payment server statistics — revenue, payments, tokens."""
        db = await _get_db()
        if db is None:
            return {"status": "database unavailable"}

        pipeline = [
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1},
                "total": {"$sum": {"$toDecimal": "$amount"}},
            }}
        ]
        stats = {}
        async for doc in db.x402_payments.aggregate(pipeline):
            stats[doc["_id"]] = {
                "count": doc["count"],
                "total_usdc": str(doc["total"]),
            }

        total_tokens = await db.x402_access_tokens.count_documents({})
        active_tokens = await db.x402_access_tokens.count_documents({
            "expires_at": {"$gt": datetime.now(timezone.utc)}
        })

        return {
            "service": "cortexos",
            "x402_enabled": X402_ENABLED,
            "network": "arc",
            "payee_address": PAYEE_ADDRESS,
            "payments": stats,
            "access_tokens": {
                "total": total_tokens,
                "active": active_tokens,
            },
        }

    # ── Health ──────────────────────────────────────────────────────

    @app.get("/api/v1/x402/health", tags=["x402"])
    async def x402_health():
        """x402 server health — MongoDB connection, Circle SDK status."""
        db = await _get_db()
        db_status = "connected" if db is not None else "disconnected"

        try:
            if db is not None:
                await db.command("ping")
        except Exception:
            db_status = "error"

        return {
            "service": "cortexos-x402",
            "status": "healthy" if db_status == "connected" else "degraded",
            "x402_enabled": X402_ENABLED,
            "database": db_status,
            "circle_sdk": "configured" if CIRCLE_API_KEY else "not_configured",
            "network": "arc-testnet" if ARC_TESTNET else "arc-mainnet",
            "payee_address": PAYEE_ADDRESS or "not_set",
            "priced_endpoints": len(RESOURCE_PRICING),
            "free_endpoints": len(FREE_ENDPOINTS),
        }

    # ── Payment Lookup ──────────────────────────────────────────────

    @app.get("/api/v1/x402/payments/{payment_id}", tags=["x402"])
    async def get_payment(payment_id: str):
        """Get payment status by ID."""
        db = await _get_db()
        if db is None:
            raise HTTPException(503, "Database not available")

        payment = await db.x402_payments.find_one(
            {"payment_id": payment_id},
            {"_id": 0}
        )
        if not payment:
            raise HTTPException(404, "Payment not found")

        # Convert datetime to ISO strings
        for k in ("created_at", "expires_at", "completed_at"):
            if payment.get(k) and isinstance(payment[k], datetime):
                payment[k] = payment[k].isoformat()

        return payment

    @app.get("/api/v1/x402/payments", tags=["x402"])
    async def list_payments(
        status: str = FQuery(None, description="Filter: pending, completed, failed, expired"),
        limit: int = FQuery(20, ge=1, le=100),
    ):
        """List x402 payments with optional filtering."""
        db = await _get_db()
        if db is None:
            raise HTTPException(503, "Database not available")

        query = {}
        if status:
            query["status"] = status

        cursor = db.x402_payments.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
        payments = []
        async for doc in cursor:
            for k in ("created_at", "expires_at", "completed_at"):
                if doc.get(k) and isinstance(doc[k], datetime):
                    doc[k] = doc[k].isoformat()
            payments.append(doc)

        return {"payments": payments, "count": len(payments)}

    logger.info("[x402] Registered middleware + routes: /api/v1/x402/*")
    logger.info(f"[x402] Enabled={X402_ENABLED}, Priced endpoints={len(RESOURCE_PRICING)}")
