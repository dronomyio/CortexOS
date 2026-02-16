#!/usr/bin/env python3
"""
Load ETH/USD minute data into MongoDB for CortexOS ExternalDataAgent.

Creates two collections:
  - eth_minute_data: raw minute bars (63K rows)
  - eth_daily_data:  daily OHLCV aggregated from minute bars (~44 days)

Usage:
  python3 load_eth_mongo.py /path/to/eth_usd_minute_combined.csv

Connects to MongoDB at MONGODB_URL env var or default localhost:27017.
"""

import csv
import os
import sys
from datetime import datetime, timezone
from collections import defaultdict

from pymongo import MongoClient, ASCENDING

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://admin:changeme@localhost:27017/")
MONGODB_DB = os.getenv("MONGODB_DB", "cortexos")


def load_csv(path):
    """Read CSV and return list of dicts with typed values."""
    rows = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "ticker": r["ticker"],
                "datetime": r["datetime"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
                "transactions": int(r["transactions"]),
                "window_start_ms": int(r["window_start_ms"]),
            })
    return rows


def aggregate_daily(minute_rows):
    """Aggregate minute bars into daily OHLCV."""
    by_date = defaultdict(list)
    for r in minute_rows:
        # Extract date from datetime string: "2026-01-01 00:00:00+00:00"
        date_str = r["datetime"][:10]
        by_date[date_str].append(r)

    daily = []
    for date_str in sorted(by_date.keys()):
        bars = by_date[date_str]
        bars_sorted = sorted(bars, key=lambda x: x["window_start_ms"])
        daily.append({
            "ticker": "X:ETH-USD",
            "date": date_str,
            "datetime": datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc),
            "open": bars_sorted[0]["open"],
            "high": max(b["high"] for b in bars),
            "low": min(b["low"] for b in bars),
            "close": bars_sorted[-1]["close"],
            "volume": round(sum(b["volume"] for b in bars), 4),
            "transactions": sum(b["transactions"] for b in bars),
            "bars_count": len(bars),
            "vwap": round(
                sum(b["close"] * b["volume"] for b in bars) /
                max(sum(b["volume"] for b in bars), 0.001), 2
            ),
            "daily_range_pct": round(
                (max(b["high"] for b in bars) - min(b["low"] for b in bars)) /
                bars_sorted[0]["open"] * 100, 3
            ),
        })

    # Add daily returns
    for i in range(1, len(daily)):
        prev_close = daily[i - 1]["close"]
        daily[i]["prev_close"] = prev_close
        daily[i]["daily_return_pct"] = round(
            (daily[i]["close"] - prev_close) / prev_close * 100, 3
        )
    if daily:
        daily[0]["prev_close"] = None
        daily[0]["daily_return_pct"] = 0.0

    return daily


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "/data/eth_usd_minute_combined.csv"

    print(f"[ETL] Reading {csv_path}...")
    minute_rows = load_csv(csv_path)
    print(f"[ETL] Loaded {len(minute_rows):,} minute bars")

    print("[ETL] Aggregating to daily OHLCV...")
    daily_rows = aggregate_daily(minute_rows)
    print(f"[ETL] Aggregated to {len(daily_rows)} daily bars")

    # Summary
    if daily_rows:
        print(f"[ETL] Date range: {daily_rows[0]['date']} → {daily_rows[-1]['date']}")
        print(f"[ETL] Price range: ${min(d['low'] for d in daily_rows):,.2f} → ${max(d['high'] for d in daily_rows):,.2f}")
        print(f"[ETL] Latest close: ${daily_rows[-1]['close']:,.2f}")

    print(f"\n[ETL] Connecting to MongoDB: {MONGODB_URL}")
    client = MongoClient(MONGODB_URL)
    db = client[MONGODB_DB]

    # -- Daily data --
    coll_daily = db["eth_daily_data"]
    coll_daily.drop()
    if daily_rows:
        coll_daily.insert_many(daily_rows)
        coll_daily.create_index([("date", ASCENDING)], unique=True)
        coll_daily.create_index([("datetime", ASCENDING)])
    print(f"[ETL] ✓ Inserted {len(daily_rows)} daily bars into eth_daily_data")

    # -- Minute data --
    coll_minute = db["eth_minute_data"]
    coll_minute.drop()
    # Add parsed datetime for indexing
    for r in minute_rows:
        r["datetime_parsed"] = datetime.strptime(
            r["datetime"][:19], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

    # Insert in batches of 5000
    batch_size = 5000
    for i in range(0, len(minute_rows), batch_size):
        batch = minute_rows[i:i + batch_size]
        coll_minute.insert_many(batch)
        print(f"[ETL]   ... inserted {min(i + batch_size, len(minute_rows)):,}/{len(minute_rows):,} minute bars")

    coll_minute.create_index([("window_start_ms", ASCENDING)])
    coll_minute.create_index([("datetime_parsed", ASCENDING)])
    print(f"[ETL] ✓ Inserted {len(minute_rows):,} minute bars into eth_minute_data")

    # -- Verify --
    print(f"\n[ETL] Collections in {MONGODB_DB}:")
    for name in db.list_collection_names():
        count = db[name].estimated_document_count()
        print(f"  {name}: {count:,} docs")

    # -- Print sample daily for verification --
    print("\n[ETL] Sample daily data (last 5 days):")
    for d in daily_rows[-5:]:
        ret = f"{d['daily_return_pct']:+.2f}%" if d.get('daily_return_pct') else ""
        print(f"  {d['date']}  O:{d['open']:>8.2f}  H:{d['high']:>8.2f}  "
              f"L:{d['low']:>8.2f}  C:{d['close']:>8.2f}  "
              f"Vol:{d['volume']:>10.2f}  {ret}")

    client.close()
    print("\n[ETL] Done! ✓")


if __name__ == "__main__":
    main()
