"""
Video Processing Pipeline
=========================

FFmpeg segmentation → Whisper transcription → Keyframe extraction

Takes a raw video file and produces structured segments ready for
Weaviate indexing and agent enrichment.

Each segment output:
{
    "index": 0,
    "video_path": "/data/out/vid123/segments/part_000.mp4",
    "transcript": "...",
    "start_seconds": 0.0,
    "end_seconds": 60.0,
    "keyframes": ["/data/out/vid123/segments/part_000_frames/frame_0001.jpg", ...],
    "keyframe_timestamps": [2.0, 18.0, 35.0, 52.0],
}
"""
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VideoProcessor:
    """
    End-to-end video processing pipeline.

    1. FFmpeg segments video into N-second chunks
    2. Extracts frames at 1 FPS per segment
    3. Selects diverse keyframes via perceptual hashing
    4. Transcribes each segment with Whisper
    """

    def __init__(
        self,
        segment_duration: int = 60,
        keyframes_per_segment: int = 4,
        whisper_model: str = "base",
        output_base: str = "/tmp/videx",
    ):
        self.segment_duration = segment_duration
        self.keyframes_per_segment = keyframes_per_segment
        self.whisper_model_name = whisper_model
        self.output_base = output_base
        self._whisper_model = None  # Lazy-loaded

    # ─── Main Entry Point ────────────────────────────────────────────────

    async def process(self, video_path: str, video_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Process a video file end-to-end.

        Returns:
            {
                "video_id": "...",
                "source_path": "...",
                "total_duration": 123.4,
                "segments": [...]
            }
        """
        video_path = str(Path(video_path).resolve())
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")

        if video_id is None:
            video_id = hashlib.md5(video_path.encode()).hexdigest()[:12]

        out_dir = os.path.join(self.output_base, video_id)
        segments_dir = os.path.join(out_dir, "segments")
        os.makedirs(segments_dir, exist_ok=True)

        logger.info(f"Processing video {video_id}: {video_path}")

        # Get duration
        duration = await self._get_duration(video_path)
        logger.info(f"Video duration: {duration:.1f}s")

        # Step 1: Segment
        segment_paths = await self._segment_video(video_path, segments_dir)
        logger.info(f"Segmented into {len(segment_paths)} parts")

        # Step 2: Process each segment (transcribe + extract keyframes)
        segments = []
        for idx, seg_path in enumerate(segment_paths):
            start_s = idx * self.segment_duration
            end_s = min(start_s + self.segment_duration, duration)

            # Extract frames
            frames_dir = os.path.join(segments_dir, f"part_{idx:03d}_frames")
            os.makedirs(frames_dir, exist_ok=True)
            all_frames = await self._extract_frames(seg_path, frames_dir)

            # Select diverse keyframes
            keyframes, kf_timestamps = self._select_keyframes(all_frames, start_s)

            # Transcribe
            transcript = await self._transcribe(seg_path)

            segments.append({
                "index": idx,
                "video_path": seg_path,
                "transcript": transcript,
                "start_seconds": start_s,
                "end_seconds": end_s,
                "keyframes": keyframes,
                "keyframe_timestamps": kf_timestamps,
            })

            logger.info(
                f"Segment {idx}: {start_s:.0f}s-{end_s:.0f}s | "
                f"{len(keyframes)} keyframes | "
                f"{len(transcript)} chars transcript"
            )

        return {
            "video_id": video_id,
            "source_path": video_path,
            "output_dir": out_dir,
            "total_duration": duration,
            "segment_count": len(segments),
            "segments": segments,
        }

    # ─── FFmpeg Operations ───────────────────────────────────────────────

    async def _get_duration(self, video_path: str) -> float:
        """Get video duration using ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", video_path,
        ]
        result = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await result.communicate()
        info = json.loads(stdout)
        return float(info["format"]["duration"])

    async def _segment_video(self, video_path: str, output_dir: str) -> List[str]:
        """Split video into segments using FFmpeg."""
        pattern = os.path.join(output_dir, "part_%03d.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-c", "copy",
            "-map", "0",
            "-segment_time", str(self.segment_duration),
            "-f", "segment",
            "-reset_timestamps", "1",
            pattern,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        await proc.communicate()

        # Collect output files (sorted)
        segment_files = sorted(
            [os.path.join(output_dir, f) for f in os.listdir(output_dir)
             if f.startswith("part_") and f.endswith(".mp4")]
        )
        return segment_files

    async def _extract_frames(self, segment_path: str, frames_dir: str) -> List[str]:
        """Extract frames at 1 FPS from a segment."""
        pattern = os.path.join(frames_dir, "frame_%04d.jpg")
        cmd = [
            "ffmpeg", "-y", "-i", segment_path,
            "-vf", "fps=1",
            "-q:v", "2",
            pattern,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        await proc.communicate()

        frames = sorted([
            os.path.join(frames_dir, f)
            for f in os.listdir(frames_dir) if f.endswith(".jpg")
        ])
        return frames

    def _select_keyframes(
        self, frames: List[str], segment_start: float,
    ) -> Tuple[List[str], List[float]]:
        """
        Select diverse keyframes using perceptual hashing.

        Strategy: cluster frames by visual similarity, pick one per cluster.
        Falls back to uniform sampling if imagehash unavailable.
        """
        n = self.keyframes_per_segment
        if len(frames) <= n:
            timestamps = [segment_start + i for i in range(len(frames))]
            return frames, timestamps

        try:
            import imagehash
            from PIL import Image

            hashes = []
            for fp in frames:
                img = Image.open(fp)
                h = imagehash.phash(img)
                hashes.append(h)

            # Greedy farthest-point sampling
            selected_indices = [0]
            for _ in range(n - 1):
                max_dist = -1
                best_idx = 0
                for i, h in enumerate(hashes):
                    if i in selected_indices:
                        continue
                    min_to_selected = min(h - hashes[j] for j in selected_indices)
                    if min_to_selected > max_dist:
                        max_dist = min_to_selected
                        best_idx = i
                selected_indices.append(best_idx)

            selected_indices.sort()
            keyframes = [frames[i] for i in selected_indices]
            timestamps = [segment_start + i for i in selected_indices]
            return keyframes, timestamps

        except ImportError:
            # Fallback: uniform sampling
            step = len(frames) / n
            indices = [int(i * step) for i in range(n)]
            keyframes = [frames[i] for i in indices]
            timestamps = [segment_start + i for i in indices]
            return keyframes, timestamps

    # ─── Whisper Transcription ───────────────────────────────────────────

    async def _transcribe(self, segment_path: str) -> str:
        """Transcribe audio using Whisper (local, free)."""
        try:
            if self._whisper_model is None:
                import whisper
                self._whisper_model = whisper.load_model(self.whisper_model_name)

            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._whisper_model.transcribe(segment_path, fp16=False),
            )
            return result.get("text", "").strip()
        except ImportError:
            logger.warning("Whisper not installed — returning empty transcript")
            return ""
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return ""

    # ─── Clip Extraction ─────────────────────────────────────────────────

    async def extract_clip(
        self,
        video_path: str,
        start_seconds: float,
        end_seconds: float,
        output_path: Optional[str] = None,
    ) -> str:
        """
        Extract a clip from a video between start and end timestamps.

        Uses FFmpeg with precise seeking for frame-accurate cuts.
        """
        if output_path is None:
            output_path = os.path.join(
                tempfile.gettempdir(),
                f"clip_{start_seconds:.0f}_{end_seconds:.0f}.mp4"
            )

        duration = end_seconds - start_seconds
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_seconds),
            "-i", video_path,
            "-t", str(duration),
            "-c:v", "libx264", "-c:a", "aac",
            "-avoid_negative_ts", "1",
            output_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        await proc.communicate()

        if not os.path.exists(output_path):
            raise RuntimeError(f"Clip extraction failed for {start_seconds}-{end_seconds}s")

        logger.info(f"Extracted clip: {start_seconds:.1f}s - {end_seconds:.1f}s → {output_path}")
        return output_path
