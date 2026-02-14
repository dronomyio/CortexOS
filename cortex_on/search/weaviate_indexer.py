"""
Weaviate Search & Indexer
=========================

Two collections for cross-modal search:

  VideoSegments — transcript chunks with sentence-transformer embeddings
  VideoKeyframes — keyframe images with CLIP embeddings

Search modes:
  - transcript: Semantic text search over transcripts
  - visual: CLIP text→image search over keyframes
  - hybrid: Both combined, merged and ranked

Clip extraction:
  Given a search result with timestamps, generates a downloadable MP4 clip.
"""
import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Weaviate Collection Schemas ──────────────────────────────────────────

VIDEO_SEGMENTS_SCHEMA = {
    "class": "VideoSegments",
    "description": "Transcribed video segments with sentence-transformer embeddings",
    "vectorizer": "text2vec-transformers",
    "moduleConfig": {
        "text2vec-transformers": {
            "vectorizeClassName": False,
        }
    },
    "properties": [
        {"name": "video_id", "dataType": ["text"], "moduleConfig": {"text2vec-transformers": {"skip": True}}},
        {"name": "text", "dataType": ["text"], "description": "Transcript text"},
        {"name": "start_seconds", "dataType": ["number"]},
        {"name": "end_seconds", "dataType": ["number"]},
        {"name": "segment_index", "dataType": ["int"]},
        {"name": "entities", "dataType": ["text[]"], "moduleConfig": {"text2vec-transformers": {"skip": True}}},
        {"name": "topics", "dataType": ["text[]"], "moduleConfig": {"text2vec-transformers": {"skip": True}}},
        {"name": "description", "dataType": ["text"], "moduleConfig": {"text2vec-transformers": {"skip": True}}},
    ],
}

VIDEO_KEYFRAMES_SCHEMA = {
    "class": "VideoKeyframes",
    "description": "Video keyframes with CLIP image embeddings for visual search",
    "vectorizer": "none",  # We provide CLIP embeddings manually
    "properties": [
        {"name": "video_id", "dataType": ["text"]},
        {"name": "segment_index", "dataType": ["int"]},
        {"name": "frame_path", "dataType": ["text"]},
        {"name": "absolute_time_s", "dataType": ["number"]},
        {"name": "description", "dataType": ["text"]},
    ],
}


class WeaviateIndexer:
    """
    Manages Weaviate collections for video search.

    Usage:
        indexer = WeaviateIndexer(host="localhost", port=8080)
        await indexer.connect()
        await indexer.ensure_schema()
        await indexer.index_video(video_id, segments, vision_analyses)
        results = await indexer.search("CRISPR gene editing", mode="hybrid")
        clip_path = await indexer.extract_clip_for_result(result, source_video)
    """

    def __init__(self, host: str = "localhost", port: int = 8080, grpc_port: int = 50051):
        self.host = host
        self.port = port
        self.grpc_port = grpc_port
        self._client = None
        self._clip_model = None
        self._clip_processor = None

    # ─── Connection ──────────────────────────────────────────────────────

    async def connect(self):
        """Connect to Weaviate instance."""
        try:
            import weaviate
            self._client = weaviate.connect_to_local(
                host=self.host, port=self.port, grpc_port=self.grpc_port,
            )
            logger.info(f"Connected to Weaviate at {self.host}:{self.port}")
        except Exception as e:
            logger.warning(f"Weaviate connection failed: {e}. Search features disabled.")
            self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def ensure_schema(self):
        """Create collections if they don't exist."""
        if not self.connected:
            return

        for schema in [VIDEO_SEGMENTS_SCHEMA, VIDEO_KEYFRAMES_SCHEMA]:
            class_name = schema["class"]
            if not self._client.collections.exists(class_name):
                self._client.collections.create_from_dict(schema)
                logger.info(f"Created Weaviate collection: {class_name}")
            else:
                logger.info(f"Collection exists: {class_name}")

    # ─── CLIP Embedding ──────────────────────────────────────────────────

    def _load_clip(self):
        """Lazy-load CLIP model for keyframe embedding + visual search."""
        if self._clip_model is not None:
            return

        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor

            model_id = "openai/clip-vit-base-patch32"
            self._clip_processor = CLIPProcessor.from_pretrained(model_id)
            self._clip_model = CLIPModel.from_pretrained(model_id)
            if torch.cuda.is_available():
                self._clip_model = self._clip_model.to("cuda")
            logger.info("CLIP model loaded for visual search")
        except ImportError:
            logger.warning("transformers/torch not available — visual search disabled")

    def _embed_image(self, image_path: str) -> Optional[List[float]]:
        """Compute CLIP embedding for an image."""
        self._load_clip()
        if self._clip_model is None:
            return None

        try:
            import torch
            from PIL import Image

            image = Image.open(image_path).convert("RGB")
            inputs = self._clip_processor(images=image, return_tensors="pt")
            device = next(self._clip_model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            with torch.no_grad():
                features = self._clip_model.get_image_features(**inputs)
                features = features / features.norm(p=2, dim=-1, keepdim=True)

            return features[0].cpu().tolist()
        except Exception as e:
            logger.error(f"Image embedding failed for {image_path}: {e}")
            return None

    def _embed_text_clip(self, text: str) -> Optional[List[float]]:
        """Compute CLIP text embedding for visual search queries."""
        self._load_clip()
        if self._clip_model is None:
            return None

        try:
            import torch

            inputs = self._clip_processor(text=[text], return_tensors="pt", padding=True)
            device = next(self._clip_model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            with torch.no_grad():
                features = self._clip_model.get_text_features(**inputs)
                features = features / features.norm(p=2, dim=-1, keepdim=True)

            return features[0].cpu().tolist()
        except Exception as e:
            logger.error(f"Text embedding failed: {e}")
            return None

    # ─── Indexing ─────────────────────────────────────────────────────────

    async def index_video(
        self,
        video_id: str,
        segments: List[Dict[str, Any]],
        vision_analyses: Optional[List[Dict]] = None,
    ) -> Dict[str, int]:
        """
        Index processed video segments into Weaviate.

        Args:
            video_id: Unique video identifier
            segments: From VideoProcessor.process() — each has transcript, keyframes, timestamps
            vision_analyses: Optional VisionEngine analysis results per segment

        Returns:
            {"segments_indexed": N, "keyframes_indexed": M}
        """
        if not self.connected:
            logger.warning("Weaviate not connected — skipping indexing")
            return {"segments_indexed": 0, "keyframes_indexed": 0}

        seg_count = 0
        kf_count = 0

        seg_collection = self._client.collections.get("VideoSegments")
        kf_collection = self._client.collections.get("VideoKeyframes")

        for idx, segment in enumerate(segments):
            vision = vision_analyses[idx] if vision_analyses and idx < len(vision_analyses) else {}

            # Index transcript segment
            seg_data = {
                "video_id": video_id,
                "text": segment.get("transcript", ""),
                "start_seconds": segment.get("start_seconds", 0),
                "end_seconds": segment.get("end_seconds", 0),
                "segment_index": segment.get("index", idx),
                "entities": vision.get("entities", []),
                "topics": vision.get("topics", []),
                "description": vision.get("description", ""),
            }

            try:
                seg_collection.data.insert(seg_data)
                seg_count += 1
            except Exception as e:
                logger.error(f"Failed to index segment {idx}: {e}")

            # Index keyframes with CLIP embeddings
            keyframes = segment.get("keyframes", [])
            kf_timestamps = segment.get("keyframe_timestamps", [])

            for kf_idx, kf_path in enumerate(keyframes):
                embedding = self._embed_image(kf_path)
                if embedding is None:
                    continue

                kf_time = kf_timestamps[kf_idx] if kf_idx < len(kf_timestamps) else 0

                kf_data = {
                    "video_id": video_id,
                    "segment_index": segment.get("index", idx),
                    "frame_path": kf_path,
                    "absolute_time_s": kf_time,
                    "description": vision.get("description", "")[:200],
                }

                try:
                    kf_collection.data.insert(kf_data, vector=embedding)
                    kf_count += 1
                except Exception as e:
                    logger.error(f"Failed to index keyframe {kf_path}: {e}")

        logger.info(f"Indexed video {video_id}: {seg_count} segments, {kf_count} keyframes")
        return {"segments_indexed": seg_count, "keyframes_indexed": kf_count}

    # ─── Search ──────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        video_id: Optional[str] = None,
        mode: str = "hybrid",
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Search video corpus.

        Modes:
            hybrid: transcript + visual combined
            transcript: sentence-transformer text search only
            visual: CLIP text→image search only

        Returns:
            {
                "query": "...",
                "mode": "hybrid",
                "transcript_results": [...],
                "visual_results": [...],
                "merged": [...],
            }
        """
        if not self.connected:
            return {"query": query, "mode": mode, "transcript_results": [], "visual_results": [], "merged": []}

        transcript_results = []
        visual_results = []

        # ── Transcript search ──
        if mode in ("hybrid", "transcript"):
            transcript_results = await self._search_transcripts(query, video_id, limit)

        # ── Visual search (CLIP) ──
        if mode in ("hybrid", "visual"):
            visual_results = await self._search_visual(query, video_id, limit)

        # ── Merge & rank ──
        merged = self._merge_results(transcript_results, visual_results, limit)

        return {
            "query": query,
            "mode": mode,
            "transcript_results": transcript_results,
            "visual_results": visual_results,
            "merged": merged,
            "total_results": len(merged),
        }

    async def _search_transcripts(
        self, query: str, video_id: Optional[str], limit: int,
    ) -> List[Dict]:
        """Semantic search over VideoSegments using sentence-transformer embeddings."""
        try:
            collection = self._client.collections.get("VideoSegments")

            filters = None
            if video_id:
                from weaviate.classes.query import Filter
                filters = Filter.by_property("video_id").equal(video_id)

            results = collection.query.near_text(
                query=query, limit=limit, filters=filters,
                return_metadata=["certainty", "distance"],
            )

            return [
                {
                    "type": "transcript",
                    "text": obj.properties.get("text", ""),
                    "start_seconds": obj.properties.get("start_seconds", 0),
                    "end_seconds": obj.properties.get("end_seconds", 0),
                    "video_id": obj.properties.get("video_id", ""),
                    "segment_index": obj.properties.get("segment_index", 0),
                    "entities": obj.properties.get("entities", []),
                    "topics": obj.properties.get("topics", []),
                    "score": obj.metadata.certainty if obj.metadata and obj.metadata.certainty else 0,
                }
                for obj in results.objects
            ]
        except Exception as e:
            logger.error(f"Transcript search failed: {e}")
            return []

    async def _search_visual(
        self, query: str, video_id: Optional[str], limit: int,
    ) -> List[Dict]:
        """CLIP text→image search over VideoKeyframes."""
        text_embedding = self._embed_text_clip(query)
        if text_embedding is None:
            return []

        try:
            collection = self._client.collections.get("VideoKeyframes")

            filters = None
            if video_id:
                from weaviate.classes.query import Filter
                filters = Filter.by_property("video_id").equal(video_id)

            results = collection.query.near_vector(
                near_vector=text_embedding, limit=limit, filters=filters,
                return_metadata=["certainty", "distance"],
            )

            return [
                {
                    "type": "visual",
                    "frame_path": obj.properties.get("frame_path", ""),
                    "absolute_time_s": obj.properties.get("absolute_time_s", 0),
                    "video_id": obj.properties.get("video_id", ""),
                    "segment_index": obj.properties.get("segment_index", 0),
                    "description": obj.properties.get("description", ""),
                    "score": obj.metadata.certainty if obj.metadata and obj.metadata.certainty else 0,
                }
                for obj in results.objects
            ]
        except Exception as e:
            logger.error(f"Visual search failed: {e}")
            return []

    def _merge_results(
        self,
        transcript_results: List[Dict],
        visual_results: List[Dict],
        limit: int,
    ) -> List[Dict]:
        """Merge transcript + visual results, de-duplicate by time proximity."""
        all_results = []

        for r in transcript_results:
            r["match_source"] = "transcript"
            all_results.append(r)

        for r in visual_results:
            r["match_source"] = "visual"
            # Convert to comparable time range
            t = r.get("absolute_time_s", 0)
            r["start_seconds"] = max(0, t - 5)
            r["end_seconds"] = t + 5
            all_results.append(r)

        # Sort by score descending
        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)

        # De-duplicate by time proximity (within 10s = same moment)
        merged = []
        used_times = []
        for r in all_results:
            t = (r.get("start_seconds", 0) + r.get("end_seconds", 0)) / 2
            vid = r.get("video_id", "")
            is_dup = False
            for ut, uv in used_times:
                if uv == vid and abs(ut - t) < 10:
                    is_dup = True
                    break
            if not is_dup:
                merged.append(r)
                used_times.append((t, vid))
            if len(merged) >= limit:
                break

        return merged

    # ─── Clip Extraction from Search Results ─────────────────────────────

    async def extract_clip_for_result(
        self,
        result: Dict,
        source_video_path: str,
        padding_seconds: float = 3.0,
        output_dir: Optional[str] = None,
    ) -> Optional[str]:
        """
        Given a search result, extract the relevant clip from the source video.

        Adds configurable padding before/after the match for context.
        """
        from ..pipeline.video_processor import VideoProcessor

        start = max(0, result.get("start_seconds", 0) - padding_seconds)
        end = result.get("end_seconds", result.get("absolute_time_s", 0) + 10) + padding_seconds

        if output_dir is None:
            output_dir = "/tmp/videx/clips"
        os.makedirs(output_dir, exist_ok=True)

        vid = result.get("video_id", "unknown")
        filename = f"clip_{vid}_{start:.0f}_{end:.0f}.mp4"
        output_path = os.path.join(output_dir, filename)

        processor = VideoProcessor()
        try:
            clip_path = await processor.extract_clip(source_video_path, start, end, output_path)
            return clip_path
        except Exception as e:
            logger.error(f"Clip extraction failed: {e}")
            return None

    # ─── Lifecycle ───────────────────────────────────────────────────────

    async def delete_video(self, video_id: str):
        """Remove all indexed data for a video."""
        if not self.connected:
            return

        for collection_name in ["VideoSegments", "VideoKeyframes"]:
            try:
                collection = self._client.collections.get(collection_name)
                from weaviate.classes.query import Filter
                collection.data.delete_many(
                    where=Filter.by_property("video_id").equal(video_id)
                )
                logger.info(f"Deleted {collection_name} for video {video_id}")
            except Exception as e:
                logger.error(f"Delete failed for {collection_name}: {e}")

    def close(self):
        if self._client:
            self._client.close()
            self._client = None
