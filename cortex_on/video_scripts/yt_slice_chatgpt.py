#!/usr/bin/env python3
"""
yt_slice_chatgpt.py

Download a YouTube video (optional), split into ChatGPT-upload-friendly snippets,
generate transcripts, and output JSON mapping snippet -> transcript.

Defaults:
- target_upload_mb = 480 (keeps a buffer under the 512MB per-file hard limit)
- slicing uses stream-copy when possible; if any segment exceeds target, it will
  automatically "re-split" that segment into smaller pieces (no re-encode).
  (This keeps quality intact and is fast.)

Dependencies:
- ffmpeg, ffprobe in PATH
- pip install yt-dlp openai-whisper

Examples:
  # From YouTube URL
  python3 yt_slice_chatgpt.py --url "https://www.youtube.com/watch?v=...." --outdir out

  # From local file
  python3 yt_slice_chatgpt.py --input ./video.mp4 --outdir out

  # Smaller snippets (more conservative)
  python3 yt_slice_chatgpt.py --input video.mp4 --target-mb 200 --outdir out

  # Force a given segment duration (seconds), ignoring auto sizing
  python3 yt_slice_chatgpt.py --input video.mp4 --segment-seconds 600 --outdir out
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import zipfile


# ---------------------------
# Utilities
# ---------------------------

def run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)

def which_or_die(bin_name: str) -> None:
    if shutil.which(bin_name) is None:
        print(f"ERROR: '{bin_name}' not found in PATH. Please install it.", file=sys.stderr)
        sys.exit(2)

def ffprobe_json(video_path: Path) -> Dict[str, Any]:
    cp = run([
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path)
    ])
    return json.loads(cp.stdout)

def get_duration_seconds(meta: Dict[str, Any]) -> float:
    # Try format.duration first
    fmt = meta.get("format", {})
    if "duration" in fmt:
        return float(fmt["duration"])
    # fallback: max stream duration
    durs = []
    for s in meta.get("streams", []):
        if "duration" in s:
            try:
                durs.append(float(s["duration"]))
            except Exception:
                pass
    return max(durs) if durs else 0.0

def get_total_bitrate_bps(meta: Dict[str, Any]) -> Optional[int]:
    # format.bit_rate is often total bitrate
    fmt = meta.get("format", {})
    br = fmt.get("bit_rate")
    if br:
        try:
            return int(float(br))
        except Exception:
            return None
    return None

def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def bytesize(p: Path) -> int:
    return p.stat().st_size

def to_rel_seconds(t: float, start: float) -> float:
    return round(max(0.0, t - start), 3)


# ---------------------------
# YouTube download (optional)
# ---------------------------

def download_youtube(url: str, outdir: Path) -> Path:
    """
    Downloads best MP4 (or best overall) and returns the downloaded filepath.
    """
    safe_mkdir(outdir)
    tmpl = str(outdir / "%(title).200B__%(id)s.%(ext)s")

    # Prefer mp4; fallback allowed.
    cmd = [
        "yt-dlp",
        "-f", "bv*+ba/best",
        "--merge-output-format", "mp4",
        "-o", tmpl,
        url,
    ]
    cp = run(cmd)
    # yt-dlp prints the final file in stderr/stdout; easiest robust method: pick newest mp4 in outdir
    mp4s = sorted(outdir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mp4s:
        # fallback: any file
        files = sorted(outdir.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            raise RuntimeError("yt-dlp succeeded but no file was found in output directory.")
        return files[0]
    return mp4s[0]


# ---------------------------
# Whisper transcription
# ---------------------------

def whisper_transcribe(video_path: Path, model: str = "small") -> Dict[str, Any]:
    """
    Uses openai-whisper CLI via python module import if available; otherwise uses subprocess.
    We produce JSON with segment timestamps.
    """
    # We'll call the whisper CLI to avoid API surface changes.
    # Output: <video>.json next to file unless --output_dir is used.
    outdir = video_path.parent / "_whisper"
    safe_mkdir(outdir)

    cmd = [
        sys.executable, "-m", "whisper",
        str(video_path),
        "--model", model,
        "--task", "transcribe",
        "--language", "en",
        "--output_format", "json",
        "--output_dir", str(outdir),
        "--verbose", "False",
    ]
    cp = run(cmd, check=True)

    # Find the JSON output
    candidates = sorted(outdir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError(f"Whisper did not produce JSON output in {outdir}")
    with candidates[0].open("r", encoding="utf-8") as f:
        return json.load(f)

# ---------------------------
# helper func
# ---------------------------
def extract_frames_1fps(video_path: Path, frames_dir: Path, fps: int = 1, jpg_quality: int = 2) -> int:
    """
    Extract frames at `fps` using ffmpeg.
    - Uses 6-digit numbering for stable sort.
    - `jpg_quality`: 2 is high quality, 31 is low (ffmpeg -q:v scale).
    Returns number of frames produced.
    """
    safe_mkdir(frames_dir)
    pattern = str(frames_dir / "frame_%06d.jpg")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", str(jpg_quality),
        pattern
    ]
    run(cmd, check=True)
    return len(list(frames_dir.glob("frame_*.jpg")))

def zip_dir(src_dir: Path, zip_path: Path) -> None:
    safe_mkdir(zip_path.parent)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(src_dir.rglob("*")):
            if p.is_file():
                z.write(p, arcname=str(p.relative_to(src_dir.parent)))


# --------------------------
# Segmenting logic
# ---------------------------

@dataclass
class Segment:
    index: int
    start: float
    end: float
    path: Path
    size_bytes: int
    transcript_text: str
    transcript_segments: List[Dict[str, Any]]

def compute_segment_seconds_auto(video_path: Path, target_bytes: int) -> int:
    meta = ffprobe_json(video_path)
    dur = get_duration_seconds(meta)
    if dur <= 0:
        # fallback: 10 minutes
        return 600

    total_br = get_total_bitrate_bps(meta)
    if not total_br or total_br <= 0:
        # fallback: estimate average bitrate from file size / duration
        est_br = int((bytesize(video_path) * 8) / dur)
        total_br = max(est_br, 1)

    # time ~= target_bits / bitrate
    target_bits = target_bytes * 8
    seg_seconds = int(max(30, math.floor(target_bits / total_br)))
    # Keep segments reasonably sized (avoid giant slices)
    seg_seconds = min(seg_seconds, 60 * 60)  # cap at 1 hour slices
    return seg_seconds

def ffmpeg_split_by_time(input_path: Path, outdir: Path, segment_seconds: int, prefix: str) -> List[Path]:
    """
    Fast split with stream copy. Produces outdir/prefix_000.mp4 etc.
    """
    safe_mkdir(outdir)
    pattern = str(outdir / f"{prefix}_%03d.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-map", "0",
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1",
        pattern
    ]
    cp = run(cmd, check=True)

    parts = sorted(outdir.glob(f"{prefix}_*.mp4"))
    return parts

def refine_oversize_segments(parts: List[Path], target_bytes: int, outdir: Path, base_prefix: str, start_time_base: float, segment_seconds_guess: int) -> List[Tuple[Path, float, float]]:
    """
    Ensures each part <= target_bytes by recursively splitting oversize parts into smaller time slices.
    Returns list of tuples: (path, abs_start, abs_end)
    We approximate abs_start/abs_end based on ordering and segment_seconds_guess; then correct with ffprobe durations.
    """
    refined: List[Tuple[Path, float, float]] = []
    cur_start = start_time_base

    for i, p in enumerate(parts):
        # Compute actual duration of this piece
        meta = ffprobe_json(p)
        dur = get_duration_seconds(meta)
        if dur <= 0:
            dur = float(segment_seconds_guess)
        cur_end = cur_start + dur

        if bytesize(p) <= target_bytes:
            refined.append((p, cur_start, cur_end))
        else:
            # Oversize: split it further (halve segment time until it's under target)
            # We'll do a simple loop: try smaller times progressively.
            tmp_prefix = f"{base_prefix}_r{i:03d}"
            # remove original file? keep it for debugging; user can delete later.
            sub_seg_seconds = max(15, int(dur / 2))
            # Worst case: keep splitting until <= target or time slice becomes tiny
            subparts = [p]
            abs0 = cur_start

            while True:
                # If all are under target -> done
                if all(bytesize(sp) <= target_bytes for sp in subparts):
                    # Add them in order with corrected durations
                    abs_t = abs0
                    for sp in subparts:
                        m = ffprobe_json(sp)
                        d = get_duration_seconds(m) or sub_seg_seconds
                        refined.append((sp, abs_t, abs_t + d))
                        abs_t += d
                    break

                # Otherwise split each oversize subpart
                new_subparts: List[Path] = []
                for sp in subparts:
                    if bytesize(sp) <= target_bytes:
                        new_subparts.append(sp)
                        continue

                    # split this oversize sp
                    split_dir = outdir / f"_refine_{tmp_prefix}"
                    safe_mkdir(split_dir)
                    # Use a deterministic prefix per round
                    round_prefix = f"{sp.stem}_s"
                    smaller = ffmpeg_split_by_time(sp, split_dir, sub_seg_seconds, round_prefix)
                    if not smaller:
                        # cannot split; give up and keep it (will exceed)
                        new_subparts.append(sp)
                    else:
                        new_subparts.extend(smaller)

                subparts = sorted(new_subparts)
                # reduce further for next round if still oversize
                sub_seg_seconds = max(10, int(sub_seg_seconds / 2))
                if sub_seg_seconds <= 10:
                    # stop trying; add what we have even if slightly oversize
                    abs_t = abs0
                    for sp in subparts:
                        m = ffprobe_json(sp)
                        d = get_duration_seconds(m) or 10
                        refined.append((sp, abs_t, abs_t + d))
                        abs_t += d
                    break

        cur_start = cur_end

    return refined

def slice_transcript_to_window(whisper_json: Dict[str, Any], win_start: float, win_end: float) -> Tuple[str, List[Dict[str, Any]]]:
    """
    whisper_json has "segments": [{start, end, text, ...}, ...]
    We select segments overlapping [win_start, win_end] and clip timestamps.
    """
    segs = whisper_json.get("segments", []) or []
    out_segs: List[Dict[str, Any]] = []
    texts: List[str] = []

    for s in segs:
        s0 = float(s.get("start", 0.0))
        s1 = float(s.get("end", 0.0))
        if s1 <= win_start or s0 >= win_end:
            continue

        # clipped absolute
        c0 = max(s0, win_start)
        c1 = min(s1, win_end)
        txt = (s.get("text") or "").strip()
        if txt:
            texts.append(txt)

        out_segs.append({
            "start_abs": round(c0, 3),
            "end_abs": round(c1, 3),
            "start_rel": to_rel_seconds(c0, win_start),
            "end_rel": to_rel_seconds(c1, win_start),
            "text": txt
        })

    # Join with spaces, lightly normalized
    transcript_text = " ".join([t for t in texts if t]).strip()
    return transcript_text, out_segs


# ---------------------------
# Main
#Practical notes
 #If you want perfect timestamp mapping per frame, you can also store frame_index -> timestamp = start_seconds + index/fps in JSON (easy to generate).
 #If storage becomes huge, switch from jpg to webp (frame_%06d.webp) to cut size a lot, or extract fewer frames (e.g., fps=0.2 = 1 frame every 5 seconds).
 #If you want, I can paste a fully integrated version of the script (with a --extract-frames flag and --fps option) so you can toggle it on/off in Docker.
# ---------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="YouTube URL to download")
    parser.add_argument("--input", help="Local video file path (skips download)")
    parser.add_argument("--outdir", required=True, help="Output directory")
    parser.add_argument("--target-mb", type=int, default=480, help="Target max MB per snippet (default 480)")
    parser.add_argument("--segment-seconds", type=int, default=0, help="Force segment duration in seconds (0 = auto)")
    parser.add_argument("--whisper-model", default="small", help="Whisper model: tiny/base/small/medium/large")
    parser.add_argument("--no-download", action="store_true", help="If set with --url, do not download (expects existing file in outdir)")
    args = parser.parse_args()

    which_or_die("ffmpeg")
    which_or_die("ffprobe")

    outdir = Path(args.outdir).expanduser().resolve()
    safe_mkdir(outdir)

    # 1) Acquire video
    src_url = args.url
    if args.input:
        video_path = Path(args.input).expanduser().resolve()
        if not video_path.exists():
            print(f"ERROR: input file not found: {video_path}", file=sys.stderr)
            sys.exit(2)
    elif args.url:
        if args.no_download:
            print("ERROR: --no-download set but --input not provided.", file=sys.stderr)
            sys.exit(2)
        #dl_dir = Path(os.environ.get("DOWNLOAD_DIR", str(outdir / "_download"))).resolve()
        dl_dir = Path(os.environ.get("DOWNLOAD_DIR", str(outdir / "_download"))).resolve()
        video_path = download_youtube(args.url, dl_dir)

    else:
        print("ERROR: Provide --input or --url", file=sys.stderr)
        sys.exit(2)

    # 2) Transcribe full video once
    whisper_data = whisper_transcribe(video_path, model=args.whisper_model)

    # 3) Split video into parts
    target_bytes = int(args.target_mb * 1024 * 1024)

    seg_seconds = args.segment_seconds if args.segment_seconds > 0 else compute_segment_seconds_auto(video_path, target_bytes)
    segments_dir = outdir / "segments"
    safe_mkdir(segments_dir)

    base_prefix = "part"
    initial_parts = ffmpeg_split_by_time(video_path, segments_dir, seg_seconds, base_prefix)
    if not initial_parts:
        print("ERROR: ffmpeg did not produce any segments.", file=sys.stderr)
        sys.exit(2)

    refined = refine_oversize_segments(
        initial_parts,
        target_bytes=target_bytes,
        outdir=segments_dir,
        base_prefix=base_prefix,
        start_time_base=0.0,
        segment_seconds_guess=seg_seconds
    )

    # 4) Build JSON
    seg_objs: List[Dict[str, Any]] = []
    for idx, (p, abs_start, abs_end) in enumerate(refined):
        txt, segs = slice_transcript_to_window(whisper_data, abs_start, abs_end)
        # ---- Frame extraction per snippet ----
        frames_dir = p.parent / f"{p.stem}_frames" / "fps_1"
        frame_count = extract_frames_1fps(p, frames_dir, fps=1, jpg_quality=2)

        frames_zip_path = p.parent / f"{p.stem}_frames_fps1.zip"
        zip_dir(frames_dir.parent, frames_zip_path)  # zip the "fps_1" folder (and its parent)
        #now each snippet has its own frame folder + zip right next to the video file, and your JSON points to both.
        seg_objs.append({
            "index": idx,
            "start_seconds": round(abs_start, 3),
            "end_seconds": round(abs_end, 3),
            "duration_seconds": round(abs_end - abs_start, 3),
            "video_path": str(p),
            "video_size_bytes": bytesize(p),
            "video_size_mb": round(bytesize(p) / (1024 * 1024), 2),
            "transcript_text": txt,
            "transcript_segments": segs,
            "frames": {
              "fps": 1,
              "frames_dir": str(frames_dir),
              "frames_zip_path": str(frames_zip_path),
              "frame_count": frame_count
             },
        })

    output = {
        "source": {
            "youtube_url": src_url,
            "input_video_path": str(video_path),
            "input_video_size_bytes": bytesize(video_path),
            "input_video_size_mb": round(bytesize(video_path) / (1024 * 1024), 2),
        },
        "chatgpt_upload": {
            "hard_limit_mb": 512,   # OpenAI File Uploads FAQ: 512MB per file
            "target_segment_mb": args.target_mb,
            "notes": "Target is set below hard limit to reduce upload failures."
        },
        "segmentation": {
            "segment_seconds_requested": args.segment_seconds if args.segment_seconds > 0 else None,
            "segment_seconds_used_initial": seg_seconds,
            "segments_count": len(seg_objs),
        },
        "segments": seg_objs,
    }

    json_path = outdir / "snippets_with_transcripts.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(str(json_path))


if __name__ == "__main__":
    main()


