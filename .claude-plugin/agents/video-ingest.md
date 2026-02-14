# Video Ingest Agent

## Role
Video ingestion pipeline — download, transcribe, plan, extract keyframes, index to Weaviate.

## Code
`cortex_on/agents/video_ingest_agent.py` → `VideoIngestAgent` class

## Auto-Discovery
Auto-discovered by `cortex_on/main.py` via `register_routes(app)`:

```
GET  /api/v1/ingest/stats  → Videos ingested, segments processed, Opus skip rate
```

Additional endpoints registered by `cortex_on/main.py` directly:
```
POST /api/v1/videos/upload     → Upload local video file
POST /api/v1/ingest/url        → Ingest YouTube URL
POST /api/v1/ingest/urls       → Batch ingest multiple URLs
GET  /api/v1/jobs              → List all jobs
GET  /api/v1/jobs/{job_id}     → Job status
GET  /api/v1/jobs/{job_id}/plan → Opus plan for job
```

## 5-Step Pipeline
1. **Download** — yt-dlp for URLs, direct copy for uploads
2. **Transcribe** — Whisper (runs in Docker)
3. **Plan** — Opus 4.6 reads transcript, selects strategy
4. **Keyframes** — CLIP visual embedding extraction (optional, `SKIP_CLIP=true` to skip)
5. **Index** — Weaviate vector store (transcript + visual chunks)

## When to Invoke
- User uploads a video or provides a URL
- `/ingest-video` skill triggered
- Batch ingest from `urls.txt`

## Data Output
```
/data/out/{video_id}/
├── snippets_with_transcripts.json  → Whisper segments
├── opus_plan.json                  → Opus strategy + per-segment scores
├── clip_embeddings/                → CLIP keyframe vectors (if enabled)
└── weaviate_indexed.json           → Index confirmation
```

## Environment Variables
- `SKIP_CLIP` — Set `true` to skip CLIP (faster, transcript-only)
- `WEAVIATE_URL` — Default: `http://weaviate:8084`
- `OPENAI_API_KEY` — For Whisper API (if not using local Whisper)
