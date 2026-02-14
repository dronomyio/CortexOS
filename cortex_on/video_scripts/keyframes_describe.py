#!/usr/bin/env python3
"""
keyframes_describe.py

Given a video clip (MP4), extract frames at a chosen FPS, drop near-duplicates,
pick representative keyframes via CLIP embedding clustering, and (optionally)
call an LLM vision model to describe each keyframe.

Outputs a JSON file with time-aligned keyframe descriptions.

Typical use:
  python3 /app/keyframes_describe.py \
    --clip /data/out/qa_clip.mp4 \
    --out /data/out/qa_clip_keyframes.json \
    --fps 1 \
    --k 6 \
    --max-hamming 6 \
    --llm-model gpt-4.1-mini \
    --no-llm 0

Notes:
- CPU-only works, but CLIP embedding will be slower than GPU.
- If you already have extracted frames (e.g., part_000_frames/fps_1), you can pass --frames-dir instead of --clip.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

import numpy as np
from PIL import Image
import imagehash

import torch
from transformers import CLIPProcessor, CLIPModel
from sklearn.cluster import KMeans

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


def run(cmd: List[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nSTDERR:\n{p.stderr}")


def ffprobe_duration_s(video_path: Path) -> Optional[float]:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        return None
    try:
        return float(p.stdout.strip())
    except Exception:
        return None


def extract_frames(clip: Path, out_dir: Path, fps: float, quality: int = 2, pattern: str = "frame_%06d.jpg") -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pat = out_dir / pattern
    # -q:v lower is better quality (2 is high quality)
    run(["ffmpeg", "-y", "-i", str(clip), "-vf", f"fps={fps}", "-q:v", str(quality), str(out_pat)])
    frames = sorted(out_dir.glob("frame_*.jpg"))
    return frames


def dedupe_phash(image_paths: List[Path], max_hamming: int) -> Tuple[List[Path], Dict[str, str]]:
    """
    Keep frames whose pHash is not within max_hamming of any previously kept hash.
    Returns kept frames and a map of frame->phash hex.
    """
    keep: List[Path] = []
    hashes: List[imagehash.ImageHash] = []
    phash_map: Dict[str, str] = {}

    for p in image_paths:
        h = imagehash.phash(Image.open(p))
        ph = str(h)  # hex-ish string
        phash_map[str(p)] = ph
        if all(abs(h - hh) > max_hamming for hh in hashes):
            keep.append(p)
            hashes.append(h)

    return keep, phash_map


def clip_embeddings(image_paths: List[Path], model_name: str) -> np.ndarray:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(model_name).to(device).eval()
    proc = CLIPProcessor.from_pretrained(model_name)

    embs: List[np.ndarray] = []
    for p in image_paths:
        img = Image.open(p).convert("RGB")
        inputs = proc(images=img, return_tensors="pt").to(device)
        with torch.no_grad():
            feat = model.get_image_features(**inputs)
            # Handle different return types from transformers versions
            if hasattr(feat, 'pooler_output'):
                feat = feat.pooler_output
            elif hasattr(feat, 'last_hidden_state'):
                feat = feat.last_hidden_state[:, 0, :]  # CLS token
            # Now feat should be a tensor
            feat = feat / feat.norm(dim=-1, keepdim=True)
        embs.append(feat.squeeze(0).detach().cpu().numpy())

    return np.vstack(embs) if embs else np.zeros((0, 512), dtype=np.float32)


def pick_keyframes_cluster_medoids(image_paths: List[Path], k: int, max_hamming: int, clip_model: str) -> Tuple[List[Path], Dict[str, Any]]:
    """
    Two-stage keyframe selection:
      1) pHash dedupe
      2) CLIP embeddings + KMeans + medoid selection per cluster
    Returns selected keyframes and debug metadata.
    """
    image_paths = sorted(image_paths)
    deduped, phash_map = dedupe_phash(image_paths, max_hamming=max_hamming)

    meta: Dict[str, Any] = {
        "total_frames": len(image_paths),
        "deduped_frames": len(deduped),
        "max_hamming": max_hamming,
        "clip_model": clip_model,
    }

    if not deduped:
        return [], {"reason": "no_frames", **meta}

    # If very few frames remain, just return them
    if len(deduped) <= k:
        return deduped, {"reason": "few_frames", "phash_map": phash_map, **meta}

    X = clip_embeddings(deduped, model_name=clip_model)
    k = min(k, len(deduped))

    km = KMeans(n_clusters=k, random_state=0, n_init="auto").fit(X)

    selected: List[Path] = []
    cluster_assignments: Dict[str, int] = {}
    for i, p in enumerate(deduped):
        cluster_assignments[str(p)] = int(km.labels_[i])

    # pick medoid: closest to centroid in each cluster
    for c in range(k):
        idxs = np.where(km.labels_ == c)[0]
        centroid = km.cluster_centers_[c]
        d = np.linalg.norm(X[idxs] - centroid, axis=1)
        best_local = idxs[int(np.argmin(d))]
        selected.append(deduped[int(best_local)])

    selected = sorted(set(selected))
    meta.update({
        "reason": "cluster_medoids",
        "phash_map": phash_map,
        "cluster_assignments": cluster_assignments,
    })
    return selected, meta


def frame_time_s(frame_path: Path, fps: float) -> float:
    """
    Infer time from frame file name frame_000123.jpg assuming 1-based ffmpeg numbering.
    time = (index-1)/fps
    """
    m = re.search(r"frame_(\d+)\.jpg$", frame_path.name)
    if not m:
        return 0.0
    idx = int(m.group(1))
    return max(0.0, (idx - 1) / fps)


def openai_describe_images(image_paths: List[Path], prompt: str, model: str, max_images: int = 8) -> List[str]:
    """
    Describe each image independently (one call per image) for simple, stable outputs.
    """
    if OpenAI is None:
        raise RuntimeError("openai python package not installed in this image.")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    client = OpenAI()

    outputs: List[str] = []
    for p in image_paths[:max_images]:
        b = p.read_bytes()
        b64 = base64.b64encode(b).decode("utf-8")
        # Responses API: image as input_image with data URL
        resp = client.responses.create(
            model=model,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
                ]
            }],
        )
        # Extract text output
        text = ""
        for out in resp.output:
            if out.type == "message":
                for c in out.content:
                    if c.type == "output_text":
                        text += c.text
        outputs.append(text.strip() if text.strip() else "")
    return outputs


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--clip", help="Input MP4 clip path (extract frames inside)")
    src.add_argument("--frames-dir", help="Directory containing frames named frame_000001.jpg ...")
    ap.add_argument("--out", required=True, help="Output JSON path")

    ap.add_argument("--fps", type=float, default=1.0, help="FPS for frame extraction / timestamp mapping")
    ap.add_argument("--k", type=int, default=6, help="Number of representative keyframes to select")
    ap.add_argument("--max-hamming", type=int, default=6, help="pHash hamming threshold for near-duplicate removal")
    ap.add_argument("--clip-model", default="openai/clip-vit-base-patch32", help="HuggingFace CLIP model name")

    ap.add_argument("--no-llm", type=int, default=0, help="Set to 1 to skip LLM description")
    ap.add_argument("--llm-model", default="gpt-4.1-mini", help="OpenAI vision-capable model name")
    ap.add_argument("--llm-prompt", default="Describe the scene in 1-2 sentences, focusing on visible objects, setting, and actions. Do not guess unseen details.",
                    help="Prompt for describing each keyframe")

    ap.add_argument("--clip-id", default=None, help="Optional: ID/name for this clip (for metadata)")
    ap.add_argument("--t1", type=float, default=None, help="Optional absolute start time of clip in the original video timeline")
    ap.add_argument("--t2", type=float, default=None, help="Optional absolute end time of clip in the original video timeline")

    args = ap.parse_args()

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    frames: List[Path] = []
    tmp_dir: Optional[tempfile.TemporaryDirectory] = None
    clip_path: Optional[Path] = Path(args.clip).expanduser().resolve() if args.clip else None

    try:
        if args.clip:
            if not clip_path.exists():
                raise FileNotFoundError(f"Clip not found: {clip_path}")
            tmp_dir = tempfile.TemporaryDirectory(prefix="frames_")
            frames_dir = Path(tmp_dir.name)
            frames = extract_frames(clip_path, frames_dir, fps=args.fps)
        else:
            frames_dir = Path(args.frames_dir).expanduser().resolve()
            if not frames_dir.exists():
                raise FileNotFoundError(f"Frames dir not found: {frames_dir}")
            frames = sorted(frames_dir.glob("frame_*.jpg"))
            if not frames:
                # also accept nested fps_1 pattern
                frames = sorted(frames_dir.rglob("frame_*.jpg"))

        if not frames:
            payload = {"error": "no_frames_found"}
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(json.dumps(payload))
            return

        selected, debug = pick_keyframes_cluster_medoids(
            image_paths=frames,
            k=args.k,
            max_hamming=args.max_hamming,
            clip_model=args.clip_model,
        )

        descriptions: List[str] = []
        if args.no_llm == 0:
            # one description per selected frame
            descriptions = openai_describe_images(selected, prompt=args.llm_prompt, model=args.llm_model, max_images=len(selected))
        else:
            descriptions = [""] * len(selected)

        # Build JSON output
        duration = ffprobe_duration_s(clip_path) if clip_path else None

        keyframes_out = []
        
        # Get CLIP embeddings for selected keyframes (for direct indexing)
        clip_vectors = []
        if selected:
            X = clip_embeddings(selected, model_name=args.clip_model)
            clip_vectors = X.tolist()
        
        for i, p in enumerate(selected):
            t = frame_time_s(p, fps=args.fps)
            abs_t = (args.t1 + t) if (args.t1 is not None) else None
            keyframes_out.append({
                "frame_path": str(p),
                "frame_file": p.name,
                "frame_time_s": t,
                "absolute_time_s": abs_t,
                "description": descriptions[i] if i < len(descriptions) else "",
                "clip_embedding": clip_vectors[i] if i < len(clip_vectors) else None,
                "phash": debug.get("phash_map", {}).get(str(p)),
                "cluster_id": debug.get("cluster_assignments", {}).get(str(p)),
            })

        payload = {
            "clip": {
                "clip_id": args.clip_id,
                "clip_path": str(clip_path) if clip_path else None,
                "duration_s": duration,
                "fps": args.fps,
                "t1_abs": args.t1,
                "t2_abs": args.t2,
            },
            "selection": {
                "k": args.k,
                "max_hamming": args.max_hamming,
                "clip_model": args.clip_model,
                "debug": {k: v for k, v in debug.items() if k not in ("phash_map", "cluster_assignments")},
            },
            "keyframes": keyframes_out,
        }

        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote: {out_path}")
    finally:
        if tmp_dir is not None:
            tmp_dir.cleanup()


if __name__ == "__main__":
    main()
