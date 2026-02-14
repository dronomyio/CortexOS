"""
Modified weaviate_ingest.py to support local embeddings
This version uses sentence-transformers instead of OpenAI API
"""

import weaviate
from weaviate.classes.config import Configure, Property, DataType
from sentence_transformers import SentenceTransformer
import json
import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Any, Iterable, Optional

# Initialize local embedding model globally
embedding_model = None

def get_embedding_model(model_name: str = "all-MiniLM-L6-v2"):
    """Get or create the embedding model singleton"""
    global embedding_model
    if embedding_model is None:
        print(f"Loading embedding model: {model_name}...")
        embedding_model = SentenceTransformer(model_name)
        print(f"✓ Model loaded. Dimensions: {embedding_model.get_sentence_embedding_dimension()}")
    return embedding_model

def connect_weaviate(url: str, api_key: Optional[str] = None):
    """Connect to Weaviate without OpenAI headers"""
    from urllib.parse import urlparse
    
    u = urlparse(url)
    host = u.hostname or "localhost"
    scheme = u.scheme or "http"
    http_port = u.port or (443 if scheme == "https" else 8080)
    
    if host in ("localhost", "127.0.0.1"):
        return weaviate.connect_to_local()
    
    # Docker / remote self-hosted Weaviate
    if host:
        return weaviate.connect_to_custom(
            http_host=host,
            http_port=http_port,
            http_secure=(scheme == "https"),
            grpc_host=host,
            grpc_port=50051,
            grpc_secure=(scheme == "https"),
        )
    
    raise RuntimeError("Unsupported Weaviate connection configuration")

def ensure_collection(client, name: str, dimensions: int):
    """
    Ensure collection exists with NONE vectorizer (we'll provide vectors manually)
    """
    # Check if collection exists
    try:
        col = client.collections.get(name)
        config = col.config.get()
        # If it exists, return it
        return col
    except Exception:
        pass
    
    # Create collection with NO vectorizer (we provide vectors manually)
    props = [
        Property(name="video_id", data_type=DataType.TEXT),
        Property(name="snippet_index", data_type=DataType.INT),
        Property(name="chunk_index", data_type=DataType.INT),
        Property(name="start_seconds", data_type=DataType.NUMBER),
        Property(name="end_seconds", data_type=DataType.NUMBER),
        Property(name="text", data_type=DataType.TEXT),
        Property(name="video_path", data_type=DataType.TEXT),
    ]
    
    # Use none vectorizer - we'll provide vectors manually
    return client.collections.create(
        name=name,
        vectorizer_config=Configure.Vectorizer.none(),
        properties=props
    )

def iter_objects(json_path: Path, video_id: str) -> Iterable[Dict[str, Any]]:
    """
    Iterate over transcript segments and yield objects for insertion
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    segs = data.get("segments", [])
    
    if not isinstance(segs, list) or not segs:
        raise ValueError("Expected JSON with key segments: [...] ")
    
    for idx, seg in enumerate(segs):
        text = seg.get("text", "").strip()
        if not text:
            continue
        
        yield {
            "video_id": video_id,
            "snippet_index": idx,
            "chunk_index": 0,
            "start_seconds": float(seg.get("start_abs", 0)),
            "end_seconds": float(seg.get("end_abs", 0)),
            "text": text,
            "video_path": seg.get("video_path", ""),
        }

def insert_all_with_local_embeddings(collection, objs: Iterable[Dict[str, Any]], 
                                     embedding_model, batch_size: int = 256):
    """
    Insert objects with locally generated embeddings
    """
    import uuid
    from weaviate.util import generate_uuid5
    
    batch = []
    texts = []
    
    def flush():
        if not batch:
            return
        
        # Generate embeddings for all texts in batch
        print(f"Generating embeddings for {len(texts)} texts...")
        embeddings = embedding_model.encode(texts, show_progress_bar=True, convert_to_numpy=True)
        
        # Insert with vectors
        print(f"Inserting {len(batch)} objects into Weaviate...")
        for i, (obj, vector) in enumerate(zip(batch, embeddings)):
            uid = generate_uuid5(obj)
            collection.data.insert(
                properties=obj,
                uuid=uid,
                vector=vector.tolist()  # Provide the embedding vector
            )
        
        batch.clear()
        texts.clear()
        print(f"✓ Batch inserted")
    
    chunks = 0
    for obj in objs:
        batch.append(obj)
        texts.append(obj["text"])
        chunks += 1
        
        if len(batch) >= batch_size:
            flush()
    
    flush()  # Final flush
    return chunks

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="Path to snippets_with_transcripts.json")
    ap.add_argument("--video-id", required=True, help="YouTube ID or internal video id")
    ap.add_argument("--collection", default="VideoChunks", help="Weaviate collection name")
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--embedding-model", default="all-MiniLM-L6-v2", 
                    help="Sentence-transformers model name")
    args = ap.parse_args()
    
    json_path = Path(args.json).expanduser().resolve()
    if not json_path.exists():
        print(f"ERROR: JSON not found: {json_path}", file=sys.stderr)
        sys.exit(2)
    
    weaviate_url = os.environ.get("WEAVIATE_URL", "http://localhost:8080")
    
    # Load embedding model
    model = get_embedding_model(args.embedding_model)
    dimensions = model.get_sentence_embedding_dimension()
    
    # Connect to Weaviate
    client = connect_weaviate(weaviate_url)
    
    try:
        # Ensure collection exists
        col = ensure_collection(client, args.collection, dimensions)
        
        # Generate objects
        objs = iter_objects(json_path, args.video_id)
        
        # Insert with local embeddings
        import time
        t0 = time.time()
        chunks = insert_all_with_local_embeddings(col, objs, model, batch_size=args.batch_size)
        elapsed = time.time() - t0
        
        print(f"Done: inserted {chunks} chunks in {elapsed:.1f}s")
    finally:
        try:
            client.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
