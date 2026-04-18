"""Clip extraction — cut peak moments from long videos with FFmpeg.

Produces both horizontal (16:9) and vertical (9:16) output formats.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _nvenc_available() -> bool:
    """Delegate to canonical check that actually tests nvenc at runtime."""
    from peanut_reacts.core.ffmpeg import nvenc_available
    return nvenc_available()


def _get_encoder() -> str:
    return "h264_nvenc" if _nvenc_available() else "libx264"


def extract_clip(
    source_video: Path,
    start_seconds: float,
    end_seconds: float,
    output_path: Path,
    padding_before: float = 2.0,
    padding_after: float = 1.0,
) -> Optional[Path]:
    """Cut a clip from a source video with optional padding."""
    if output_path.exists():
        log.info("Clip already exists: %s", output_path.name)
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    actual_start = max(0, start_seconds - padding_before)
    actual_duration = (end_seconds + padding_after) - actual_start

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(actual_start),
        "-i", str(source_video),
        "-t", str(actual_duration),
        "-c:v", _get_encoder(),
        "-preset", "fast" if _nvenc_available() else "veryfast",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        log.info("Cut clip: %s (%.1fs-%.1fs)", output_path.name, actual_start, actual_start + actual_duration)
        return output_path
    except subprocess.CalledProcessError as e:
        log.error("Failed to cut clip: %s", e.stderr.decode("utf-8", errors="replace")[:300])
        return None
    except Exception as e:
        log.error("Clip extraction error: %s", e)
        return None


def convert_to_vertical(
    source_clip: Path,
    output_path: Path,
    width: int = 1080,
    height: int = 1920,
    method: str = "blur_bg",
) -> Optional[Path]:
    """Convert a 16:9 clip to 9:16 vertical format.

    Methods:
    - blur_bg: Center original with blurred fullscreen background (recommended)
    - crop: Center crop to 9:16 (loses sides, zooms in)
    - pad: Black bars top/bottom (standard letterbox inverted)
    """
    if output_path.exists():
        log.info("Vertical clip exists: %s", output_path.name)
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if method == "blur_bg":
        # Blurred fullscreen bg + original centered
        filter_complex = (
            f"[0:v]split=2[bg][main];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=30[blurred];"
            f"[main]scale={width}:-2[scaled];"
            f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2[out]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(source_clip),
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "0:a?",
            "-c:v", _get_encoder(),
            "-preset", "fast" if _nvenc_available() else "veryfast",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ]
    elif method == "crop":
        # Center crop to 9:16
        filter_str = f"crop=ih*{width}/{height}:ih,scale={width}:{height}"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(source_clip),
            "-vf", filter_str,
            "-c:v", _get_encoder(),
            "-preset", "fast" if _nvenc_available() else "veryfast",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:  # pad
        filter_str = (
            f"scale={width}:-2,"
            f"pad={width}:{height}:0:(oh-ih)/2:black"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(source_clip),
            "-vf", filter_str,
            "-c:v", _get_encoder(),
            "-preset", "fast" if _nvenc_available() else "veryfast",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        log.info("Vertical clip: %s", output_path.name)
        return output_path
    except subprocess.CalledProcessError as e:
        log.error("Vertical conversion failed: %s", e.stderr.decode("utf-8", errors="replace")[:300])
        return None


def add_captions_and_hook(
    source_clip: Path,
    output_path: Path,
    hook_text: str = "",
    cta_text: str = "Full video on YouTube",
    hook_duration: float = 2.5,
) -> Optional[Path]:
    """Add hook text at start and CTA at end using FFmpeg drawtext.

    Note: Auto-captions from transcript would go here too.
    """
    if output_path.exists():
        return output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Escape text for FFmpeg drawtext
    def escape(t: str) -> str:
        return t.replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")

    hook_escaped = escape(hook_text)
    cta_escaped = escape(cta_text)

    # Get duration
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(source_clip)],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(r.stdout.strip())
    except Exception:
        duration = 30.0

    # Build drawtext filter
    filters = []
    if hook_text:
        filters.append(
            f"drawtext=text='{hook_escaped}':fontcolor=white:fontsize=72:"
            f"box=1:boxcolor=black@0.7:boxborderw=20:"
            f"x=(w-text_w)/2:y=h*0.15:"
            f"enable='between(t,0,{hook_duration})'"
        )
    if cta_text:
        cta_start = max(0, duration - 3)
        filters.append(
            f"drawtext=text='{cta_escaped}':fontcolor=yellow:fontsize=56:"
            f"box=1:boxcolor=black@0.8:boxborderw=15:"
            f"x=(w-text_w)/2:y=h*0.85:"
            f"enable='between(t,{cta_start},{duration})'"
        )

    vf = ",".join(filters) if filters else "null"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(source_clip),
        "-vf", vf,
        "-c:v", _get_encoder(),
        "-preset", "fast" if _nvenc_available() else "veryfast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        log.info("Captioned clip: %s", output_path.name)
        return output_path
    except subprocess.CalledProcessError as e:
        log.warning("Caption failed (using source): %s", e.stderr.decode("utf-8", errors="replace")[:200])
        import shutil
        shutil.copy2(source_clip, output_path)
        return output_path


def process_peak_to_shorts(
    source_video: Path,
    peak,
    output_dir: Path,
    hook_text: str = "",
    character_name: str = "Peanut",
) -> dict:
    """Full pipeline: peak -> horizontal clip -> vertical -> captioned.

    Returns dict with paths: {horizontal, vertical, captioned}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    video_id = getattr(peak, "source_video_id", "clip")
    start = int(peak.start_seconds)

    horizontal = output_dir / f"{video_id}_peak_{start:05d}_h.mp4"
    vertical = output_dir / f"{video_id}_peak_{start:05d}_v.mp4"
    captioned = output_dir / f"{video_id}_peak_{start:05d}_final.mp4"

    result = {"horizontal": None, "vertical": None, "captioned": None}

    # Step 1: Horizontal cut
    h = extract_clip(source_video, peak.start_seconds, peak.end_seconds, horizontal)
    if not h:
        return result
    result["horizontal"] = str(h)

    # Step 2: Vertical conversion
    v = convert_to_vertical(h, vertical, method="blur_bg")
    if not v:
        return result
    result["vertical"] = str(v)

    # Step 3: Hook + CTA
    if not hook_text:
        hook_text = _auto_hook(peak, character_name)
    c = add_captions_and_hook(v, captioned, hook_text=hook_text,
                              cta_text=f"{character_name} Reacts on YouTube")
    if c:
        result["captioned"] = str(c)

    return result


def _auto_hook(peak, character: str) -> str:
    """Generate a hook text from peak metadata."""
    if peak.peak_emotion:
        emoji_map = {
            "shocked": "SHOCKED", "laughing": "DYING", "mind_blown": "MIND BLOWN",
            "screaming": "NO WAY", "amazed": "WAIT FOR IT",
        }
        return emoji_map.get(peak.peak_emotion.lower(), "WAIT FOR IT")
    if peak.comment_count >= 5:
        return f"{peak.comment_count}+ FANS TIMESTAMPED THIS"
    if peak.replay_intensity > 0.8:
        return "MOST REPLAYED MOMENT"
    return "THIS IS WILD"
