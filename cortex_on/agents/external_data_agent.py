"""
CortexOS ExternalDataAgent — MongoDB Market Data for Claim Verification
========================================================================

Queries ETH/USD price data from MongoDB (loaded by load_eth_mongo.py)
and provides it to the IntelligenceLayer for data-vs-claim contradiction detection.

This is the agent that makes CortexOS different from ChatGPT:
  - Analyst says "ETH bottomed at $2,200" → agent pulls real data → Opus verifies
  - Analyst says "ETH is up 40% this quarter" → agent computes actual return → Opus flags

Auto-discovered by main.py via register_routes(app).

Collections used:
  - eth_daily_data:  44 daily OHLCV bars (Jan 1 – Feb 13, 2026)
  - eth_minute_data: 63K minute bars for granular verification

Location: cortex_on/agents/external_data_agent.py
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("cortexos.external_data")

# ── Config ──────────────────────────────────────────────────────────────

MONGODB_URL = os.getenv("MONGODB_URL", os.getenv("MONGODB_URI", "mongodb://admin:changeme@mongodb:27017/"))
MONGODB_DB = os.getenv("MONGODB_DB", "cortexos")

# ── Async MongoDB Connection ────────────────────────────────────────────

_mongo_client = None
_mongo_db = None


async def _get_db():
    global _mongo_client, _mongo_db
    if _mongo_db is not None:
        return _mongo_db
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        _mongo_client = AsyncIOMotorClient(MONGODB_URL)
        _mongo_db = _mongo_client[MONGODB_DB]
        # Verify connection
        count = await _mongo_db.eth_daily_data.count_documents({})
        logger.info(f"[ExternalDataAgent] MongoDB connected: {count} daily bars available")
        return _mongo_db
    except Exception as e:
        logger.error(f"[ExternalDataAgent] MongoDB connection failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# ExternalDataAgent
# ═══════════════════════════════════════════════════════════════════════════

class ExternalDataAgent:
    """
    Pulls real market data from MongoDB to cross-reference analyst claims.

    Three core capabilities:
      1. get_market_summary()  — full date-range summary for Opus context
      2. get_price_at_date()   — exact price on a specific date
      3. verify_claim()        — check a specific claim against real data
    """

    def __init__(self):
        self._db = None

    async def initialize(self):
        self._db = await _get_db()
        if self._db is None:
            logger.warning("[ExternalDataAgent] No MongoDB — market data verification disabled")

    @property
    def available(self) -> bool:
        return self._db is not None

    # ─── 1. Market Summary for Opus Context ─────────────────────────────

    async def get_market_summary(self) -> Dict[str, Any]:
        """
        Build a comprehensive ETH market summary that Opus can use
        to verify claims against real data.

        Returns daily OHLCV, key statistics, notable events.
        """
        if self._db is None:
            return {"error": "MongoDB not connected"}

        daily = await self._db.eth_daily_data.find(
            {}, {"_id": 0}
        ).sort("date", 1).to_list(length=100)

        if not daily:
            return {"error": "No ETH daily data in MongoDB"}

        # Key statistics
        closes = [d["close"] for d in daily]
        highs = [d["high"] for d in daily]
        lows = [d["low"] for d in daily]
        volumes = [d["volume"] for d in daily]

        period_high = max(highs)
        period_low = min(lows)
        period_high_date = daily[highs.index(period_high)]["date"]
        period_low_date = daily[lows.index(period_low)]["date"]

        first = daily[0]
        last = daily[-1]
        period_return = round((last["close"] - first["open"]) / first["open"] * 100, 2)

        # Weekly returns
        weekly_returns = []
        for i in range(0, len(daily), 7):
            week = daily[i:i + 7]
            if len(week) >= 2:
                wr = round((week[-1]["close"] - week[0]["open"]) / week[0]["open"] * 100, 2)
                weekly_returns.append({
                    "week_start": week[0]["date"],
                    "week_end": week[-1]["date"],
                    "return_pct": wr,
                    "high": max(d["high"] for d in week),
                    "low": min(d["low"] for d in week),
                })

        # Biggest single-day moves
        daily_moves = []
        for d in daily:
            if d.get("daily_return_pct") is not None:
                daily_moves.append({
                    "date": d["date"],
                    "return_pct": d["daily_return_pct"],
                    "close": d["close"],
                })
        daily_moves.sort(key=lambda x: abs(x["return_pct"]), reverse=True)

        # Drawdown from ATH in period
        running_max = 0
        max_drawdown = 0
        max_dd_date = ""
        for d in daily:
            running_max = max(running_max, d["high"])
            dd = (d["low"] - running_max) / running_max * 100
            if dd < max_drawdown:
                max_drawdown = dd
                max_dd_date = d["date"]

        return {
            "asset": "ETH/USD",
            "source": "Polygon.io via CortexOS MongoDB",
            "data_range": f"{first['date']} to {last['date']}",
            "total_days": len(daily),
            "current_price": last["close"],
            "period_open": first["open"],
            "period_close": last["close"],
            "period_return_pct": period_return,
            "period_high": period_high,
            "period_high_date": period_high_date,
            "period_low": period_low,
            "period_low_date": period_low_date,
            "max_drawdown_pct": round(max_drawdown, 2),
            "max_drawdown_date": max_dd_date,
            "avg_daily_volume": round(sum(volumes) / len(volumes), 1),
            "total_volume": round(sum(volumes), 1),
            "weekly_returns": weekly_returns,
            "biggest_daily_moves": daily_moves[:5],
            "daily_prices": [
                {
                    "date": d["date"],
                    "open": d["open"],
                    "high": d["high"],
                    "low": d["low"],
                    "close": d["close"],
                    "volume": round(d["volume"], 1),
                    "return_pct": d.get("daily_return_pct", 0),
                }
                for d in daily
            ],
        }

    # ─── 2. Price at Specific Date ──────────────────────────────────────

    async def get_price_at_date(self, date_str: str) -> Optional[Dict]:
        """Get exact OHLCV for a specific date."""
        if self._db is None:
            return None
        doc = await self._db.eth_daily_data.find_one(
            {"date": date_str}, {"_id": 0}
        )
        return doc

    # ─── 3. Price Range Query ───────────────────────────────────────────

    async def get_price_range(self, start_date: str, end_date: str) -> List[Dict]:
        """Get daily bars for a date range."""
        if self._db is None:
            return []
        cursor = self._db.eth_daily_data.find(
            {"date": {"$gte": start_date, "$lte": end_date}},
            {"_id": 0}
        ).sort("date", 1)
        return await cursor.to_list(length=100)

    # ─── 4. Compute Actual Return ───────────────────────────────────────

    async def compute_return(self, start_date: str, end_date: str) -> Optional[Dict]:
        """Compute actual ETH return between two dates."""
        start = await self.get_price_at_date(start_date)
        end = await self.get_price_at_date(end_date)
        if not start or not end:
            return None
        ret = (end["close"] - start["close"]) / start["close"] * 100
        return {
            "start_date": start_date,
            "end_date": end_date,
            "start_price": start["close"],
            "end_price": end["close"],
            "return_pct": round(ret, 2),
            "direction": "up" if ret > 0 else "down",
        }

    # ─── 5. Minute-Level Lookup ─────────────────────────────────────────

    async def get_minute_data(self, date_str: str, hour: int = None) -> List[Dict]:
        """Get minute-level bars for a specific date (and optional hour)."""
        if self._db is None:
            return []

        start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if hour is not None:
            start = start.replace(hour=hour)
            end = start + timedelta(hours=1)
        else:
            end = start + timedelta(days=1)

        cursor = self._db.eth_minute_data.find(
            {"datetime_parsed": {"$gte": start, "$lt": end}},
            {"_id": 0, "ticker": 0, "window_start": 0, "window_start_ms": 0}
        ).sort("datetime_parsed", 1)
        return await cursor.to_list(length=1500)


# ═══════════════════════════════════════════════════════════════════════════
# Auto-Discovery: register_routes(app)
# ═══════════════════════════════════════════════════════════════════════════

_agent = None


def _get_agent() -> ExternalDataAgent:
    global _agent
    if _agent is None:
        _agent = ExternalDataAgent()
    return _agent


def register_routes(app):
    """Register ExternalDataAgent API endpoints. Auto-discovered by main.py."""

    from fastapi import Query
    from pydantic import BaseModel
    from typing import Optional as Opt

    @app.on_event("startup")
    async def init_external_data():
        agent = _get_agent()
        await agent.initialize()

    # ── Market Summary ──────────────────────────────────────────────

    @app.get("/api/v1/market/summary", tags=["market-data"])
    async def market_summary():
        """
        Full ETH/USD market summary from MongoDB.
        
        Returns daily OHLCV, weekly returns, biggest moves, drawdown stats.
        This is the data Opus uses to verify analyst claims.
        """
        agent = _get_agent()
        if not agent.available:
            await agent.initialize()
        return await agent.get_market_summary()

    # ── Price at Date ───────────────────────────────────────────────

    @app.get("/api/v1/market/price/{date}", tags=["market-data"])
    async def price_at_date(date: str):
        """Get ETH OHLCV for a specific date (format: 2026-01-15)."""
        agent = _get_agent()
        if not agent.available:
            await agent.initialize()
        result = await agent.get_price_at_date(date)
        if not result:
            return {"error": f"No data for {date}"}
        return result

    # ── Price Range ─────────────────────────────────────────────────

    @app.get("/api/v1/market/range", tags=["market-data"])
    async def price_range(
        start: str = Query(..., description="Start date YYYY-MM-DD"),
        end: str = Query(..., description="End date YYYY-MM-DD"),
    ):
        """Get ETH daily bars for a date range."""
        agent = _get_agent()
        if not agent.available:
            await agent.initialize()
        return await agent.get_price_range(start, end)

    # ── Compute Return ──────────────────────────────────────────────

    @app.get("/api/v1/market/return", tags=["market-data"])
    async def compute_return(
        start: str = Query(..., description="Start date"),
        end: str = Query(..., description="End date"),
    ):
        """Compute actual ETH return between two dates."""
        agent = _get_agent()
        if not agent.available:
            await agent.initialize()
        result = await agent.compute_return(start, end)
        if not result:
            return {"error": "Date(s) not found"}
        return result

    # ── Minute Data ─────────────────────────────────────────────────

    @app.get("/api/v1/market/minute/{date}", tags=["market-data"])
    async def minute_data(date: str, hour: int = None):
        """Get minute-level ETH bars for a specific date."""
        agent = _get_agent()
        if not agent.available:
            await agent.initialize()
        return await agent.get_minute_data(date, hour)

    # ── Health ──────────────────────────────────────────────────────

    @app.get("/api/v1/market/health", tags=["market-data"])
    async def market_health():
        agent = _get_agent()
        if not agent.available:
            await agent.initialize()
        if not agent.available:
            return {"status": "unavailable", "message": "MongoDB not connected"}
        daily_count = await agent._db.eth_daily_data.count_documents({})
        minute_count = await agent._db.eth_minute_data.count_documents({})
        return {
            "status": "active",
            "agent": "external-data-agent",
            "daily_bars": daily_count,
            "minute_bars": minute_count,
            "mongodb_connected": True,
        }

    logger.info(
        "[ExternalDataAgent] Registered routes: "
        "/api/v1/market/summary, /market/price/{date}, "
        "/market/range, /market/return, /market/minute/{date}, /market/health"
    )
