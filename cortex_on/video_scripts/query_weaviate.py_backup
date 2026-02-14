#!/usr/bin/env python3
"""
query_weaviate.py

End-to-end "ask video" helper:
1) Takes a natural-language question
2) Retrieves top-k transcript chunks from Weaviate (nearText)
3) Merges hits into a single best time window
4) Calls get_clip.py to materialize a stitched clip (t1..t2)
5) Calls an LLM (OpenAI Responses API) to answer using ONLY the retrieved evidence.
   If evidence is weak/absent -> prints: "The answer is not there in the video."

Designed to run inside docker-compose alongside Weaviate.

Env:
  WEAVIATE_URL     e.g. http://weaviate:8080   (inside compose) or http://localhost:8080 (host)
  WEAVIATE_API_KEY (optional, for WCS)
  EMBEDDING_MODEL  (optional; default all-MiniLM-L6-v2 via sentence-transformers)
  OPENAI_API_KEY   (optional; only needed for the LLM answer step, not for search)
  OPENAI_BASE_URL  (optional; default https://api.openai.com/v1)

Example (inside docker compose network):
  python3 /app/query_weaviate.py \
    --question "Where does he explain flatten tables?" \
    --collection VideoChunks \
    --video-id dQw4w9WgXcQ \
    --top-k 8 \
    --index-json /data/out/snippets_with_transcripts.json \
    --out-clip /data/out/answer_clip.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import weaviate
from weaviate.classes.query import MetadataQuery, Filter

# Import observability (gracefully handle if not available)
try:
    from observability import trace_search, metrics, trace_span
    OBSERVABILITY_ENABLED = True
except ImportError:
    OBSERVABILITY_ENABLED = False
    def trace_search(f): return f
    class DummyMetrics:
        def record(self, *args, **kwargs): pass
    metrics = DummyMetrics()
    from contextlib import contextmanager
    @contextmanager
    def trace_span(name, metadata=None):
        yield type('obj', (object,), {'set_metadata': lambda self, x: None})()


@dataclass
class Hit:
    text: str
    start_s: float
    end_s: float
    distance: Optional[float]
    snippet_index: Optional[int]
    chunk_index: Optional[int]
    video_path: Optional[str]
    uuid: str


def _connect_weaviate() -> Any:
    from urllib.parse import urlparse

    url = os.environ.get("WEAVIATE_URL", "http://weaviate:8080")
    api_key = os.environ.get("WEAVIATE_API_KEY")
    headers = {}

    u = urlparse(url)
    host = u.hostname or "localhost"
    scheme = u.scheme or "http"
    http_port = u.port or (443 if scheme == "https" else 8080)

    # True localhost (running on host, not in a container)
    if host in ("localhost", "127.0.0.1"):
        return weaviate.connect_to_local(headers=headers)

    # Weaviate Cloud (WCS)
    if api_key:
        return weaviate.connect_to_weaviate_cloud(
            cluster_url=url,
            auth_credentials=weaviate.auth.AuthApiKey(api_key),
            headers=headers,
        )

    # Docker-compose / remote self-hosted — need both HTTP + gRPC
    return weaviate.connect_to_custom(
        http_host=host,
        http_port=http_port,
        http_secure=(scheme == "https"),
        grpc_host=host,
        grpc_port=50051,
        grpc_secure=(scheme == "https"),
        headers=headers,
    )


@trace_search
def _fetch_hits(
    client: Any,
    collection_name: str,
    question: str,
    top_k: int,
    video_id: Optional[str],
) -> List[Hit]:
    start_time = time.time()
    col = client.collections.get(collection_name)

    where = None
    if video_id:
        where = Filter.by_property("video_id").equal(video_id)

    # Determine which properties to request based on collection type
    is_keyframe = collection_name.lower().startswith("videokeyframe")

    # Use CLIP text encoder for keyframe queries, sentence-transformers for transcript
    if is_keyframe:
        from clip_embedding_service import get_clip_embedding_service
        clip_svc = get_clip_embedding_service()
        query_vec = clip_svc.embed_text(question)
        return_props = [
            "description",
            "frame_time_s",
            "absolute_time_s",
            "video_id",
            "clip_id",
            "t1_abs",
            "t2_abs",
        ]
    else:
        from embedding_service import get_embedding_service
        svc = get_embedding_service()
        query_vec = svc.embed_text(question)
        return_props = [
            "text",
            "start_seconds",
            "end_seconds",
            "snippet_index",
            "chunk_index",
            "video_path",
            "video_id",
        ]

    res = col.query.near_vector(
        near_vector=query_vec,
        limit=top_k,
        filters=where,
        return_metadata=MetadataQuery(distance=True),
        return_properties=return_props,
    )

    hits: List[Hit] = []
    for o in res.objects:
        props = o.properties or {}
        md = o.metadata  # MetadataReturn object — use attribute access, not .get()
        dist = getattr(md, "distance", None) if md else None

        if is_keyframe:
            # Map keyframe schema to Hit dataclass
            abs_t = float(props.get("absolute_time_s", props.get("frame_time_s", 0.0)))
            t1_abs = props.get("t1_abs")
            t2_abs = props.get("t2_abs")
            # Create a time window around the keyframe
            start_s = abs_t
            end_s = abs_t + 3.0  # keyframes represent a point in time
            if t1_abs is not None and t2_abs is not None:
                # Use the segment window if available
                start_s = max(float(t1_abs), abs_t - 2.0)
                end_s = min(float(t2_abs), abs_t + 5.0)

            hits.append(
                Hit(
                    text=str(props.get("description", "")),
                    start_s=start_s,
                    end_s=end_s,
                    distance=float(dist) if dist is not None else None,
                    snippet_index=None,
                    chunk_index=None,
                    video_path=None,
                    uuid=str(o.uuid),
                )
            )
        else:
            hits.append(
                Hit(
                    text=str(props.get("text", "")),
                    start_s=float(props.get("start_seconds", 0.0)),
                    end_s=float(props.get("end_seconds", 0.0)),
                    distance=float(dist) if dist is not None else None,
                    snippet_index=int(props["snippet_index"]) if props.get("snippet_index") is not None else None,
                    chunk_index=int(props["chunk_index"]) if props.get("chunk_index") is not None else None,
                    video_path=str(props.get("video_path")) if props.get("video_path") else None,
                    uuid=str(o.uuid),
                )
            )

    # Sort best-first: smaller distance is better for cosine distance
    hits.sort(key=lambda h: (h.distance if h.distance is not None else 9999.0))
    
    # Record metrics
    latency_ms = (time.time() - start_time) * 1000
    metrics.record("weaviate_search_latency_ms", latency_ms, {
        "collection": collection_name,
        "top_k": str(top_k),
        "hit_count": str(len(hits))
    })
    
    return hits


def _merge_hits_into_windows(
    hits: List[Hit],
    merge_gap_s: float,
    max_window_s: float,
) -> List[Tuple[float, float, List[Hit]]]:
    """Merge sorted hits into windows if they're close in time."""
    if not hits:
        return []

    # Sort by start time for window merging
    hits_by_time = sorted(hits, key=lambda h: h.start_s)
    windows: List[Tuple[float, float, List[Hit]]] = []

    cur_hits: List[Hit] = [hits_by_time[0]]
    cur_start = hits_by_time[0].start_s
    cur_end = hits_by_time[0].end_s

    for h in hits_by_time[1:]:
        gap = h.start_s - cur_end
        proposed_end = max(cur_end, h.end_s)
        if gap <= merge_gap_s and (proposed_end - cur_start) <= max_window_s:
            cur_hits.append(h)
            cur_end = proposed_end
        else:
            windows.append((cur_start, cur_end, cur_hits))
            cur_hits = [h]
            cur_start, cur_end = h.start_s, h.end_s

    windows.append((cur_start, cur_end, cur_hits))
    return windows


def _window_score(hits: List[Hit]) -> float:
    # Higher is better. Convert distance to similarity-like score.
    # If distance missing, treat as weak.
    score = 0.0
    for h in hits:
        if h.distance is None:
            score += 0.0
        else:
            score += max(0.0, 1.0 - h.distance)
    # Prefer denser windows (bonus for more hits)
    score += 0.15 * len(hits)
    return score


def _pick_best_window(windows: List[Tuple[float, float, List[Hit]]]) -> Optional[Tuple[float, float, List[Hit]]]:
    if not windows:
        return None
    return max(windows, key=lambda w: _window_score(w[2]))


def _format_evidence(best_hits: List[Hit], max_chars: int = 6000) -> str:
    # Keep it compact and timestamped.
    lines: List[str] = []
    for h in sorted(best_hits, key=lambda x: x.start_s):
        t0 = f"{h.start_s:0.2f}"
        t1 = f"{h.end_s:0.2f}"
        txt = " ".join(h.text.split())
        if len(txt) > 450:
            txt = txt[:450].rstrip() + "…"
        dist = f"{h.distance:.3f}" if h.distance is not None else "n/a"
        lines.append(f"[{t0}s–{t1}s | dist={dist}] {txt}")
    evidence = "\n".join(lines)
    return evidence[:max_chars]


def _call_get_clip(index_json: str, t1: float, t2: float, out_clip: str, reencode: bool) -> None:
    cmd = [
        "python3",
        "/app/get_clip.py",
        "--index-json",
        index_json,
        "--t1",
        str(t1),
        "--t2",
        str(t2),
        "--out",
        out_clip,
    ]
    if reencode:
        cmd.append("--reencode")

    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"get_clip failed\nCMD: {' '.join(cmd)}\nSTDERR:\n{p.stderr}")
    # get_clip prints paths; keep quiet here.


def _openai_answer(model: str, question: str, evidence: str, clip_path: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set (needed unless --no-llm).")

    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    url = base.rstrip("/") + "/responses"

    system = (
        "You are a video Q&A assistant. "
        "You MUST answer ONLY using the provided evidence (timestamped transcript snippets). "
        "If the evidence does not contain the answer, reply exactly: 'The answer is not there in the video.' "
        "When you answer, include the most relevant timestamp range(s) in your response."
    )

    user = f"""Question:
{question}

Evidence (timestamped transcript hits):
{evidence}

A video clip has been materialized for this window at:
{clip_path}

Instruction:
Answer the question using ONLY the evidence above. If missing, say the exact fallback sentence.
"""

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "text", "text": system}]},
            {"role": "user", "content": [{"type": "text", "text": user}]},
        ],
        "max_output_tokens": 500,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"OpenAI Responses API call failed: {e}")

    # Responses API returns an array of output items; extract concatenated text.
    # See OpenAI API reference. citeturn0search0
    out_text_parts: List[str] = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    out_text_parts.append(c.get("text", ""))
    return ("".join(out_text_parts)).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", required=True, help="Natural-language question")
    ap.add_argument("--collection", default="VideoChunks", help="Weaviate collection name")
    ap.add_argument("--video-id", default=None, help="Filter to a specific video_id (recommended)")
    ap.add_argument("--top-k", type=int, default=8, help="Number of chunks to retrieve")
    ap.add_argument("--merge-gap", type=float, default=8.0, help="Max gap (seconds) to merge hits into one window")
    ap.add_argument("--max-window", type=float, default=140.0, help="Max window duration (seconds)")
    ap.add_argument("--pad", type=float, default=2.0, help="Pad seconds added before/after chosen window")
    ap.add_argument("--max-distance", type=float, default=0.35,
                    help="If best hit distance is higher than this, treat as 'no evidence' (cosine distance typical).")
    ap.add_argument("--index-json", required=True, help="Path to snippets_with_transcripts.json (for get_clip)")
    ap.add_argument("--out-clip", required=True, help="Output path for the generated clip MP4")
    ap.add_argument("--reencode", action="store_true", help="Re-encode during clipping/concat (more robust, slower)")
    ap.add_argument("--no-llm", action="store_true", help="Skip LLM; just output chosen window + clip")
    ap.add_argument("--llm-model", default="gpt-4.1-mini", help="OpenAI model for answer generation")  # citeturn0search1
    ap.add_argument("--out-json", default=None, help="Optional: write a JSON result to this path")
    args = ap.parse_args()

    client = _connect_weaviate()
    try:
        hits = _fetch_hits(
            client=client,
            collection_name=args.collection,
            question=args.question,
            top_k=args.top_k,
            video_id=args.video_id,
        )
    finally:
        try:
            client.close()
        except Exception:
            pass

    if not hits:
        result = {
            "question": args.question,
            "answer": "The answer is not there in the video.",
            "reason": "no_hits",
        }
        print(result["answer"])
        if args.out_json:
            Path(args.out_json).write_text(json.dumps(result, indent=2), encoding="utf-8")
        return

    best_dist = hits[0].distance if hits[0].distance is not None else 9999.0
    if best_dist > args.max_distance:
        result = {
            "question": args.question,
            "answer": "The answer is not there in the video.",
            "reason": "best_hit_too_far",
            "best_distance": best_dist,
        }
        print(result["answer"])
        if args.out_json:
            Path(args.out_json).write_text(json.dumps(result, indent=2), encoding="utf-8")
        return

    windows = _merge_hits_into_windows(hits, merge_gap_s=args.merge_gap, max_window_s=args.max_window)
    best = _pick_best_window(windows)
    if not best:
        result = {
            "question": args.question,
            "answer": "The answer is not there in the video.",
            "reason": "no_windows",
            "best_distance": best_dist,
        }
        print(result["answer"])
        if args.out_json:
            Path(args.out_json).write_text(json.dumps(result, indent=2), encoding="utf-8")
        return

    t1, t2, best_hits = best
    t1p = max(0.0, t1 - args.pad)
    t2p = t2 + args.pad

    # Materialize clip
    _call_get_clip(args.index_json, t1p, t2p, args.out_clip, reencode=args.reencode)

    evidence = _format_evidence(best_hits)

    answer = ""
    if args.no_llm:
        answer = "LLM disabled. Clip created for the best-matching time window."
    else:
        answer = _openai_answer(args.llm_model, args.question, evidence, args.out_clip)

        # Enforce the non-hallucination fallback if model didn't follow instructions
        if not answer:
            answer = "The answer is not there in the video."

    result = {
        "question": args.question,
        "answer": answer,
        "video_id": args.video_id,
        "top_k": args.top_k,
        "best_hit_distance": best_dist,
        "window": {"start_seconds": t1p, "end_seconds": t2p},
        "clip_path": args.out_clip,
        "evidence": evidence,
    }

    print(answer)
    print(f"\nClip: {args.out_clip}")
    print(f"Window: {t1p:.2f}s–{t2p:.2f}s")
    if args.out_json:
        from pathlib import Path
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

