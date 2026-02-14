#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import weaviate
from weaviate.classes.config import Configure, Property, DataType
from weaviate.util import generate_uuid5

try:
    import tiktoken  # type: ignore
except Exception:
    tiktoken = None

SENT_SPLIT_RE = re.compile(r"(?<=[.!?])(?:[\"'\)\]]+)?\s+(?=[A-Z0-9])")


def get_encoder(model: str):
    if tiktoken is None:
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def token_count(text: str, enc) -> int:
    if not text:
        return 0
    if enc is None:
        # rough heuristic
        return int(len(text.split()) * 1.33)
    return len(enc.encode(text))


def split_paragraphs(text: str) -> List[str]:
    t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not t:
        return []
    parts = re.split(r"\n\s*\n+", t)
    return [p.strip() for p in parts if p.strip()]


def split_sentences(paragraph: str) -> List[str]:
    p = paragraph.strip()
    if not p:
        return []
    if not re.search(r"[.!?]", p):
        return [p]
    sents = SENT_SPLIT_RE.split(p)
    return [s.strip() for s in sents if s.strip()]


@dataclass
class AtomicSpan:
    text: str
    start_s: float
    end_s: float


@dataclass
class Chunk:
    text: str
    start_s: float
    end_s: float
    chunk_index: int


def atomic_spans_from_segments(segments: List[Dict[str, Any]]) -> List[AtomicSpan]:
    atoms: List[AtomicSpan] = []
    for seg in segments:

        #Your transcript segments do not use start/end keys. They use:
        #start_abs, end_abs
        #start_rel, end_rel
        #text
        #So your ingester is looking for the wrong timestamp fields, producing atoms = 0 ⇒ chunks = 0 ⇒ inserted 0.
        #The fix
        ##    Update atomic_spans_from_segments() to read start_abs/end_abs (preferable for global timeline), with fallback to start_rel/end_rel.
        #    Replace these two lines:

        #s0 = float(seg.get("start", seg.get("start_seconds", 0.0)))
        #s1 = float(seg.get("end", seg.get("end_seconds", 0.0)))

        # Prefer absolute timeline if available, else relative, else legacy fields
        s0 = seg.get("start_abs", seg.get("start_rel", seg.get("start", seg.get("start_seconds", 0.0))))
        s1 = seg.get("end_abs", seg.get("end_rel", seg.get("end", seg.get("end_seconds", 0.0))))
        s0 = float(s0)
        s1 = float(s1)

        txt = str(seg.get("text", seg.get("transcript", ""))).strip()
        if not txt or s1 <= s0:
            continue

        # semantic split inside segment
        paras = split_paragraphs(txt) or [txt]
        sents: List[str] = []
        for p in paras:
            sents.extend(split_sentences(p))

        if not sents:
            continue

        total_chars = sum(len(s) for s in sents) or 1
        dur = s1 - s0
        cur = s0
        for i, sent in enumerate(sents):
            if i == len(sents) - 1:
                end = s1
            else:
                end = cur + dur * (len(sent) / total_chars)
            atoms.append(AtomicSpan(text=sent, start_s=cur, end_s=end))
            cur = end

    atoms.sort(key=lambda a: (a.start_s, a.end_s))
    return atoms


def pack_atoms(atoms: List[AtomicSpan], max_tokens: int, enc) -> List[Chunk]:
    chunks: List[Chunk] = []
    buf: List[AtomicSpan] = []
    buf_toks = 0
    idx = 0

    def flush():
        nonlocal idx, buf, buf_toks
        if not buf:
            return
        chunks.append(Chunk(
            text=" ".join(a.text for a in buf).strip(),
            start_s=buf[0].start_s,
            end_s=buf[-1].end_s,
            chunk_index=idx,
        ))
        idx += 1
        buf = []
        buf_toks = 0

    for a in atoms:
        t = token_count(a.text, enc)
        if not buf and t >= max_tokens:
            chunks.append(Chunk(text=a.text.strip(), start_s=a.start_s, end_s=a.end_s, chunk_index=idx))
            idx += 1
            continue
        if buf and buf_toks + t > max_tokens:
            flush()
        buf.append(a)
        buf_toks += t

    flush()
    return chunks


def connect_weaviate_BAD(url: str, api_key: Optional[str], openai_key: str):
    headers = {"X-OpenAI-Api-Key": openai_key}
    if "localhost" in url or "127.0.0.1" in url:
        return weaviate.connect_to_local(headers=headers)
    if api_key:
        return weaviate.connect_to_weaviate_cloud(
            cluster_url=url,
            auth_credentials=weaviate.auth.AuthApiKey(api_key),
            headers=headers,
        )
    # Remote without auth (rare)
    host = url.replace("https://", "").replace("http://", "")
    return weaviate.connect_to_custom(http_host=host, http_port=443 if url.startswith("https://") else 80,
                                      http_secure=url.startswith("https://"), headers=headers)


def connect_weaviate_localhost_issue(url: str, api_key: str | None, openai_key: str | None):
    headers = {}
    if openai_key:
        headers["X-OpenAI-Api-Key"] = openai_key

    # Docker Compose / local Weaviate
    if "weaviate" in url or "localhost" in url or "127.0.0.1" in url:
        return weaviate.connect_to_local(headers=headers)

    # Weaviate Cloud (WCS)
    if api_key:
        return weaviate.connect_to_weaviate_cloud(
            cluster_url=url,
            auth_credentials=weaviate.auth.AuthApiKey(api_key),
            headers=headers,
        )

    raise RuntimeError("Unsupported Weaviate connection configuration")


from urllib.parse import urlparse
import weaviate

def connect_weaviate(url: str, api_key: str | None, openai_key: str | None):
    headers = {}
    if openai_key:
        headers["X-OpenAI-Api-Key"] = openai_key

    u = urlparse(url)
    host = u.hostname or "localhost"
    scheme = u.scheme or "http"
    http_port = u.port or (443 if scheme == "https" else 8080)

    # If you are truly connecting to a local daemon from the SAME machine/process
    # (e.g., running python on your host, not inside a container):
    if host in ("localhost", "127.0.0.1"):
        return weaviate.connect_to_local(headers=headers)

    # Docker / remote self-hosted Weaviate: must provide BOTH HTTP + gRPC.
    # In your compose, gRPC is exposed at 50051.
    if host:
        return weaviate.connect_to_custom(
            http_host=host,
            http_port=http_port,
            http_secure=(scheme == "https"),
            grpc_host=host,
            grpc_port=50051,
            grpc_secure=(scheme == "https"),
            headers=headers,
        )

    # Weaviate Cloud (WCS)
    if api_key:
        return weaviate.connect_to_weaviate_cloud(
            cluster_url=url,
            auth_credentials=weaviate.auth.AuthApiKey(api_key),
            headers=headers,
        )

    raise RuntimeError("Unsupported Weaviate connection configuration")


def ensure_collection(client, name: str, dimensions: int):
    """Create or reuse a collection with vectorizer=none (local embeddings)."""
    try:
        col = client.collections.get(name)
        config = col.config.get()
        # If vectorizer is text2vec-openai, delete and recreate with none
        if config.vectorizer_config is not None and 'text2vec' in str(config.vectorizer_config):
            print(f"Collection {name} uses OpenAI vectorizer, recreating with none…")
            client.collections.delete(name)
        else:
            return col
    except Exception:
        pass

    props = [
        Property(name="video_id", data_type=DataType.TEXT),
        Property(name="snippet_index", data_type=DataType.INT),
        Property(name="chunk_index", data_type=DataType.INT),
        Property(name="start_seconds", data_type=DataType.NUMBER),
        Property(name="end_seconds", data_type=DataType.NUMBER),
        Property(name="text", data_type=DataType.TEXT),
        Property(name="video_path", data_type=DataType.TEXT),
    ]

    return client.collections.create(
        name=name,
        vectorizer_config=Configure.Vectorizer.none(),
        properties=props,
    )


def iter_objects_BAD(json_path: Path, video_id: str, max_tokens: int, tokenizer_model: str) -> Iterable[Dict[str, Any]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    segs = data.get("segments", [])
    if not isinstance(segs, list) or not segs:
        raise ValueError("Expected JSON with key segments: [...] ")

    enc = get_encoder(tokenizer_model)

    for seg in segs:
        snippet_index = int(seg.get("index", 0))
        video_path = str(seg.get("video_path", ""))
        tseg = seg.get("transcript_segments", [])

        if not isinstance(tseg, list) or not tseg:
            # fallback
            tx = str(seg.get("transcript_text", "")).strip()
            if not tx:
                continue
            t0 = float(seg.get("start_seconds", 0.0))
            t1 = float(seg.get("end_seconds", t0))
            tseg = [{"start": t0, "end": t1, "text": tx}]

        atoms = atomic_spans_from_segments(tseg)
        chunks = pack_atoms(atoms, max_tokens=max_tokens, enc=enc)

        for ch in chunks:
            yield {
                "video_id": video_id,
                "snippet_index": snippet_index,
                "chunk_index": ch.chunk_index,
                "start_seconds": float(ch.start_s),
                "end_seconds": float(ch.end_s),
                "text": ch.text,
                "video_path": video_path,
            }



def iter_objects(json_path: Path, video_id: str, max_tokens: int, tokenizer_model: str):
    data = json.loads(json_path.read_text(encoding="utf-8"))

    # Accept multiple schemas
    segs = None
    if isinstance(data, list):
        segs = data
    elif isinstance(data, dict):
        for k in ("segments", "snippets", "parts", "items"):
            if isinstance(data.get(k), list):
                segs = data[k]
                break

    if not segs:
        raise ValueError("No segments/snippets found in JSON")

    enc = get_encoder(tokenizer_model)

    for seg in segs:
        snippet_index = int(seg.get("index", seg.get("snippet_index", 0)))
        video_path = str(seg.get("video_path", seg.get("path", "")))

        # Try transcript segments in a few common places/names
        tseg = seg.get("transcript_segments") or seg.get("transcriptSegments") or seg.get("whisper_segments")
        if not isinstance(tseg, list) or not tseg:
            # Nested whisper format: seg["whisper"]["segments"]
            w = seg.get("whisper")
            if isinstance(w, dict) and isinstance(w.get("segments"), list):
                tseg = w["segments"]

        # Fallback to a blob of text
        if not isinstance(tseg, list) or not tseg:
            tx = (seg.get("transcript_text") or seg.get("transcript") or seg.get("text") or "").strip()
            if not tx:
                continue
            t0 = float(seg.get("start_seconds", seg.get("start", 0.0)))
            t1 = float(seg.get("end_seconds", seg.get("end", t0)))
            tseg = [{"start": t0, "end": t1, "text": tx}]

        atoms = atomic_spans_from_segments(tseg)
        if not atoms:
            continue
        chunks = pack_atoms(atoms, max_tokens=max_tokens, enc=enc)

        for ch in chunks:
            yield {
                "video_id": video_id,
                "snippet_index": snippet_index,
                "chunk_index": ch.chunk_index,
                "start_seconds": float(ch.start_s),
                "end_seconds": float(ch.end_s),
                "text": ch.text,
                "video_path": video_path,
            }




def insert_all(collection, objs: Iterable[Dict[str, Any]], batch_size: int = 256):
    from embedding_service import get_embedding_service

    svc = get_embedding_service()
    buf: List[Dict[str, Any]] = []
    total = 0
    t0 = time.time()

    def flush():
        nonlocal total, buf
        if not buf:
            return
        texts = [o["text"] for o in buf]
        vectors = svc.embed_batch(texts, batch_size=len(texts))
        for o, vec in zip(buf, vectors):
            uid = generate_uuid5(
                f"{o['video_id']}|{o['snippet_index']}|{o['chunk_index']}"
                f"|{o['start_seconds']}|{o['end_seconds']}"
            )
            collection.data.insert(properties=o, uuid=uid, vector=vec)
        total += len(buf)
        buf = []

    for o in objs:
        buf.append(o)
        if len(buf) >= batch_size:
            flush()
            if total % (batch_size * 5) == 0:
                print(f"Inserted {total} chunks in {time.time()-t0:.1f}s")

    if buf:
        flush()

    print(f"Done: inserted {total} chunks in {time.time()-t0:.1f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="Path to snippets_with_transcripts.json")
    ap.add_argument("--video-id", required=True, help="YouTube ID or internal video id")
    ap.add_argument("--collection", default="VideoChunks", help="Weaviate collection name")
    ap.add_argument("--max-tokens", type=int, default=350, help="Token budget per chunk")
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--embedding-model", default=None,
                    help="sentence-transformers model (default: all-MiniLM-L6-v2)")
    ap.add_argument("--tokenizer-model", default="text-embedding-3-small")
    args = ap.parse_args()

    json_path = Path(args.json).expanduser().resolve()
    if not json_path.exists():
        print(f"ERROR: JSON not found: {json_path}", file=sys.stderr)
        sys.exit(2)

    weaviate_url = os.environ.get("WEAVIATE_URL", "http://localhost:8080")
    weaviate_api_key = os.environ.get("WEAVIATE_API_KEY")

    # Local embeddings — no OpenAI key needed
    from embedding_service import get_embedding_service
    svc = get_embedding_service(args.embedding_model)

    client = connect_weaviate(weaviate_url, weaviate_api_key, openai_key=None)
    try:
        col = ensure_collection(client, args.collection, svc.dimensions)
        objs = iter_objects(json_path, args.video_id, args.max_tokens, args.tokenizer_model)
        insert_all(col, objs, batch_size=args.batch_size)
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

