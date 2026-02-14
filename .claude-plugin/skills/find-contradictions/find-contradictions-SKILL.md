# /find-contradictions

Search across all indexed videos for contradictions, revised positions, and stale claims.

## Usage

```
/find-contradictions "BTC price targets"
/find-contradictions "Tom Lee predictions" --speaker "Tom Lee"
/find-contradictions "Fed rate cuts" --since 2025-01-01
/find-contradictions --video abc123 --against def456
/find-contradictions --full-scan
```

## Parameters
- `query` — Topic to search for contradictions (natural language)
- `--speaker` — Filter to specific person's claims
- `--since` — Only consider claims after this date
- `--video` — Source video ID to check
- `--against` — Compare against specific video ID
- `--full-scan` — Scan ALL indexed videos (expensive, uses more x402 credits)
- `--max-claims` — Max claims to verify (default: 20)

## Steps

### 1. Health check
```bash
HEALTH=$(curl -s http://localhost:8093/api/v1/health)
echo $HEALTH | python3 -c "
import sys, json
h = json.load(sys.stdin)
if h.get('opus_planner') != 'active':
    print('ERROR: Opus planner not active')
    sys.exit(1)
print('CortexOS OK — Opus 4.6 active')
"
```

### 2. Search Weaviate for relevant segments
```bash
# Search across all videos (or specific video)
curl -s "http://localhost:8093/api/v1/videos/${VIDEO_ID:-all}/search?q=${QUERY}&top_k=20&include_visual=false" \
  | python3 -m json.tool
```

### 3. Use Opus synthesis with investigative strategy
```bash
curl -s -X POST http://localhost:8093/api/v1/synthesize \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Find all contradictions, revised positions, and inconsistencies about: ${QUERY}. Compare what different speakers said at different times. Flag any claims that conflict with each other or with current data.",
    "search_mode": "hybrid",
    "top_k": 20
  }' | python3 -m json.tool
```

### 4. Run fact-verifier on extracted claims
```bash
# Verify individual claims found in synthesis
curl -s -X POST "http://localhost:8093/api/v1/verify/claim?text=${CLAIM_TEXT}&speaker=${SPEAKER}&category=prediction" \
  | python3 -m json.tool
```

### 5. For --full-scan: iterate all videos
```bash
# Get all completed jobs
JOBS=$(curl -s http://localhost:8093/api/v1/jobs)
echo $JOBS | python3 -c "
import sys, json
jobs = json.load(sys.stdin)
ready = [j for j in jobs if j.get('status') == 'ready']
print(f'Found {len(ready)} indexed videos')
for j in ready:
    print(f\"  {j['video_id']}: {j.get('message', '')[:60]}\")
"

# Verify each video
for VIDEO_ID in $(echo $JOBS | python3 -c "
import sys, json
for j in json.load(sys.stdin):
    if j.get('status') == 'ready':
        print(j['video_id'])
"); do
  echo "=== Verifying $VIDEO_ID ==="
  curl -s -X POST http://localhost:8093/api/v1/verify \
    -H "Content-Type: application/json" \
    -d "{\"video_id\": \"$VIDEO_ID\", \"max_claims\": ${MAX_CLAIMS:-10}}"
  echo ""
done
```

### 6. Report
Format output as:

```
╔══════════════════════════════════════════════════════╗
║  CortexOS Contradiction Report                       ║
║  Query: "${QUERY}"                                   ║
║  Videos scanned: N                                   ║
╠══════════════════════════════════════════════════════╣
║                                                      ║
║  ⚠️  CONTRADICTIONS (N)                              ║
║  ────────────────────                                ║
║  1. [Video A @ 2:30] Tom Lee: "BTC $200K by June"   ║
║     [Video B @ 5:15] Tom Lee: "BTC $150K by Q3"     ║
║     Verdict: REVISED — same speaker changed target   ║
║                                                      ║
║  ⏰ STALE CLAIMS (N)                                 ║
║  ────────────────────                                ║
║  1. [Video C @ 1:45] "Fed will cut 3 times in 2025" ║
║     Current data: Fed has cut only once so far       ║
║     Verdict: STALE                                   ║
║                                                      ║
║  💰 Cost: $0.60 USDC (20 claims × $0.03)            ║
╚══════════════════════════════════════════════════════╝
```

## Error Handling
- If no indexed videos → "No videos ingested yet. Run /ingest-video first."
- If Weaviate down → "Weaviate not reachable. Check docker-compose."
- If x402 daily limit hit → warn and show remaining budget
- If query too broad → suggest narrowing with --speaker or --since

## Dependencies
- CortexOS running with indexed videos
- Opus planner active
- For web verification: PARALLEL_API_KEY set
- For billing: x402 server + Circle wallet configured
