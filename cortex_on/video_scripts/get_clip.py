#!/usr/bin/env python3
"""
get_clip.py

Create an exact video clip for a requested time window [t1, t2] (seconds)
by stitching across existing snippet MP4s produced by yt_slice_chatgpt.py.

It reads the JSON produced by the slicer (snippets_with_transcripts.json),
finds which snippet(s) overlap the window, trims the needed ranges, and
concatenates them into a single output MP4.

Requirements:
  - ffmpeg, ffprobe in PATH
  - Python 3.9+

Usage:
  python3 get_clip.py \
    --index-json /path/to/snippets_with_transcripts.json \
    --t1 50.0 \
    --t2 120.0 \
    --out /path/to/output/clip_50_120.mp4

Notes:
  - Uses stream copy (-c copy) for speed and no quality loss.
  - If you see A/V sync issues on some sources, rerun with --reencode.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional


def which_or_die(bin_name: str) -> None:
    if shutil.which(bin_name) is None:
        print(f"ERROR: '{bin_name}' not found in PATH. Please install it.", file=sys.stderr)
        sys.exit(2)


def run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)


def load_index(json_path: Path) -> Dict[str, Any]:
    with json_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_segments(index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Returns a sorted list of segments with required fields:
      - start_seconds
      - end_seconds
      - video_path
    """
    segs = index.get("segments", [])
    if not isinstance(segs, list) or not segs:
        raise ValueError("Index JSON has no 'segments' list or it is empty.")

    out = []
    for s in segs:
        try:
            out.append({
                "start_seconds": float(s["start_seconds"]),
                "end_seconds": float(s["end_seconds"]),
                "video_path": str(s["video_path"]),
                "index": int(s.get("index", len(out))),
            })
        except Exception as e:
            raise ValueError(f"Bad segment entry in JSON: {s}") from e

    out.sort(key=lambda x: x["start_seconds"])
    return out


def find_overlapping_segments(segments: List[Dict[str, Any]], t1: float, t2: float) -> List[Dict[str, Any]]:
    """
    Return segments that overlap [t1, t2]:
      seg.start < t2 AND seg.end > t1
    """
    hits = []
    for s in segments:
        if s["start_seconds"] < t2 and s["end_seconds"] > t1:
            hits.append(s)
    return hits


def fmt_seconds(x: float) -> str:
    """
    ffmpeg is fine with decimals; keep it stable.
    """
    return f"{x:.3f}"


def cut_subclip(
    in_path: Path,
    out_path: Path,
    local_start: float,
    local_end: float,
    reencode: bool = False,
    vcodec: str = "libx264",
    acodec: str = "aac",
    crf: int = 20,
    preset: str = "veryfast",
) -> None:
    """
    Create subclip [local_start, local_end] from in_path and write to out_path.
    local_* are seconds relative to in_path.

    Stream-copy is default for speed/no loss.
    If reencode=True, re-encodes to improve compatibility/sync across joins.
    """
    if local_end <= local_start:
        raise ValueError(f"Invalid subclip range: {local_start} to {local_end}")

    # Place -ss before -i for speed; for frame-accurate cutting, you'd typically
    # reencode or put -ss after -i. For most clipped content, this is fine.
    cmd = ["ffmpeg", "-y", "-ss", fmt_seconds(local_start), "-to", fmt_seconds(local_end), "-i", str(in_path)]

    if reencode:
        cmd += ["-c:v", vcodec, "-preset", preset, "-crf", str(crf), "-c:a", acodec]
    else:
        cmd += ["-c", "copy"]

    cmd += [str(out_path)]
    cp = run(cmd, check=False)
    if cp.returncode != 0:
        raise RuntimeError(
            "ffmpeg cut failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"STDERR:\n{cp.stderr}"
        )


def concat_clips(clips: List[Path], out_path: Path, reencode: bool = False) -> None:
    """
    Concatenate clips in order using ffmpeg concat demuxer.
    With -c copy, codecs/params must match. If not, use reencode.
    """
    if not clips:
        raise ValueError("No clips to concatenate.")

    with tempfile.TemporaryDirectory(prefix="concat_") as td:
        list_path = Path(td) / "list.txt"
        lines = []
        for c in clips:
            # ffmpeg concat demuxer requires file lines
            # Use absolute paths for safety.
            lines.append(f"file '{c.resolve()}'")
        list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path)]
        if reencode:
            # If reencoding on concat, choose widely compatible settings
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac"]
        else:
            cmd += ["-c", "copy"]
        cmd += [str(out_path)]

        cp = run(cmd, check=False)
        if cp.returncode != 0:
            raise RuntimeError(
                "ffmpeg concat failed.\n"
                f"Command: {' '.join(cmd)}\n"
                f"STDERR:\n{cp.stderr}"
            )


def get_clip(
    index_json: Path,
    t1: float,
    t2: float,
    out_path: Path,
    reencode: bool = False,
) -> Dict[str, Any]:
    """
    Main function:
      - loads segments from index_json
      - finds overlap with [t1, t2]
      - trims and concatenates into out_path
    Returns metadata about the build.
    """
    if t2 <= t1:
        raise ValueError("t2 must be greater than t1.")

    index = load_index(index_json)
    segments = normalize_segments(index)
    hits = find_overlapping_segments(segments, t1, t2)

    if not hits:
        raise ValueError(f"No snippet overlaps the requested window [{t1}, {t2}].")

    # Ensure output directory exists
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Build subclips in a temp dir
    with tempfile.TemporaryDirectory(prefix="subclips_") as td:
        td_path = Path(td)
        subclips: List[Path] = []
        used_parts: List[Dict[str, Any]] = []

        for i, s in enumerate(hits):
            seg_start = s["start_seconds"]
            seg_end = s["end_seconds"]
            in_path = Path(s["video_path"])
            if not in_path.exists():
                raise FileNotFoundError(f"Snippet file not found: {in_path}")

            # Intersection with [t1, t2] in absolute time
            abs_start = max(t1, seg_start)
            abs_end = min(t2, seg_end)

            # Convert to snippet-local time
            local_start = abs_start - seg_start
            local_end = abs_end - seg_start

            sub_path = td_path / f"sub_{i:03d}.mp4"
            cut_subclip(in_path, sub_path, local_start, local_end, reencode=reencode)

            subclips.append(sub_path)
            used_parts.append({
                "snippet_index": s["index"],
                "snippet_path": str(in_path),
                "snippet_abs_start": seg_start,
                "snippet_abs_end": seg_end,
                "used_abs_start": abs_start,
                "used_abs_end": abs_end,
                "used_local_start": local_start,
                "used_local_end": local_end,
            })

        if len(subclips) == 1:
            # Single overlap -> move/copy directly
            shutil.copy2(subclips[0], out_path)
        else:
            concat_clips(subclips, out_path, reencode=reencode)

    return {
        "index_json": str(index_json),
        "t1": t1,
        "t2": t2,
        "out_path": str(out_path),
        "reencode": reencode,
        "parts_used": used_parts,
    }


def main():
    which_or_die("ffmpeg")
    which_or_die("ffprobe")

    ap = argparse.ArgumentParser()
    ap.add_argument("--index-json", required=True, help="Path to snippets_with_transcripts.json")
    ap.add_argument("--t1", required=True, type=float, help="Start time in seconds (absolute in original video)")
    ap.add_argument("--t2", required=True, type=float, help="End time in seconds (absolute in original video)")
    ap.add_argument("--out", required=True, help="Output MP4 path")
    ap.add_argument(
        "--reencode",
        action="store_true",
        help="Re-encode subclips/concat for better compatibility (slower, slight quality loss).",
    )
    args = ap.parse_args()

    meta = get_clip(
        index_json=Path(args.index_json).expanduser().resolve(),
        t1=args.t1,
        t2=args.t2,
        out_path=Path(args.out).expanduser().resolve(),
        reencode=args.reencode,
    )

    # Also emit a small sidecar JSON so you can store lineage in Vertica
    sidecar = Path(args.out).with_suffix(".clip_meta.json")
    with sidecar.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"Wrote clip: {args.out}")
    print(f"Wrote meta: {sidecar}")


if __name__ == "__main__":
    main()



