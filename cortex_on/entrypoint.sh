#!/bin/bash
# CortexOS entrypoint: load market data (idempotent) then start server

echo "[entrypoint] Checking market data..."

# Run ETL if CSV exists (skips automatically if data already loaded)
if [ -f /data/eth_usd_minute_combined.csv ]; then
    python3 /app/load_eth_mongo.py /data/eth_usd_minute_combined.csv
else
    echo "[entrypoint] No CSV found at /data/eth_usd_minute_combined.csv — skipping ETL"
fi

echo "[entrypoint] Starting CortexOS server..."
exec python3 -m uvicorn main:app --host 0.0.0.0 --port 8081 --workers 1
