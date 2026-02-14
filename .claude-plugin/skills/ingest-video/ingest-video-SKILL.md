# /ingest-video

Ingest a video into CortexOS — download, transcribe, Opus-plan, index, and optionally fact-check.

## Usage

```
/ingest-video "https://youtube.com/watch?v=xxx"
/ingest-video "https://youtube.com/watch?v=xxx" --start 1:30 --end 4:45
/ingest-video "https://youtube.com/watch?v=xxx" --title "Tom Lee BTC Call Feb 2025"
/ingest-video ./local_video.mp4
/ingest-video batch urls.txt
```

## Parameters
- `url` or `file` — YouTube URL, local file path, or `batch` + file with one URL per line
- `--start` / `--end` — Optional time range to extract (format: MM:SS or HH:MM:SS)
- `--title` — Optional title override
- `--skip-clip` — Skip CLIP keyframe extraction (faster, transcript-only)
- `--verify` — Auto-run fact-verifier after ingest completes

## Steps

### 1. Determine input type
```bash
# URL → use /api/v1/ingest/url
# File → use /api/v1/videos/upload
# batch → use /api/v1/ingest/urls
```

### 2. Submit to CortexOS API

For URL:
```bash
curl -s -X POST http://localhost:8093/api/v1/ingest/url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "${URL}",
    "title": "${TITLE}",
    "start_time": "${START}",
    "end_time": "${END}"
  }' | python3 -m json.tool
```

For file:
```bash
curl -s -X POST http://localhost:8093/api/v1/videos/upload \
  -F "file=@${FILE_PATH}" | python3 -m json.tool
```

For batch:
```bash
# Read urls.txt, format as JSON array
URLS=$(cat ${BATCH_FILE} | python3 -c "
import sys, json
urls = []
for line in sys.stdin:
    line = line.strip()
    if not line or line.startswith('#'): continue
    parts = line.split('|')
    entry = {'url': parts[0].strip()}
    if len(parts) > 1: entry['title'] = parts[1].strip()
    urls.append(entry)
print(json.dumps({'urls': urls}))
")
curl -s -X POST http://localhost:8093/api/v1/ingest/urls \
  -H "Content-Type: application/json" \
  -d "$URLS" | python3 -m json.tool
```

### 3. Poll for completion
```bash
# Extract job_id from response, then poll
JOB_ID="<from step 2>"
while true; do
  STATUS=$(curl -s http://localhost:8093/api/v1/jobs/${JOB_ID})
  PROGRESS=$(echo $STATUS | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "$STATUS" | python3 -m json.tool
  if [ "$PROGRESS" = "ready" ] || [ "$PROGRESS" = "failed" ]; then
    break
  fi
  sleep 10
done
```

### 4. Show Opus plan
```bash
curl -s http://localhost:8093/api/v1/jobs/${JOB_ID}/plan | python3 -m json.tool
```

### 5. (Optional) Auto-verify if --verify flag
```bash
VIDEO_ID="<from step 2>"
curl -s -X POST http://localhost:8093/api/v1/verify \
  -H "Content-Type: application/json" \
  -d "{\"video_id\": \"${VIDEO_ID}\", \"max_claims\": 20}" | python3 -m json.tool
```

### 6. Report
Print summary:
- Video ID, duration, segments processed
- Opus plan: how many segments selected for CLIP, risk scores
- Contradictions found (if --verify)
- Time taken, cost (if x402 enabled)

## Error Handling
- If health check fails → abort with "CortexOS not running. Start with: docker-compose up -d"
- If job fails → show error message from job status
- If timeout (>15 min) → warn but keep polling
- If CLIP hangs → suggest --skip-clip flag

## Dependencies
- CortexOS running at http://localhost:8093
- For URLs: yt-dlp installed in container
- For --verify: ANTHROPIC_API_KEY set, PARALLEL_API_KEY set
