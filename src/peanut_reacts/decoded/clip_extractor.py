"""
Fair-use clip extractor with analysis overlays.

Extracts short clips from source videos and adds analytical
annotations: text overlays, highlight boxes, stat cards.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from peanut_reacts.shorts.editor import _get_encoder

logger = logging.getLogger(__name__)


def find_source_video(query: str, video_dirs: list[Path]) -> Path | None:
    """Find a source video matching the query string.

    Searches by partial title match in the download directories.
    """
    query_lower = query.lower()
    for vdir in video_dirs:
        if not vdir.exists():
            continue
        for f in vdir.iterdir():
            if f.suffix in (".mp4", ".mkv", ".webm") and query_lower in f.name.lower():
                return f
    return None


def extract_clip(
    source_video: Path,
    start: float,
    end: float,
    output_path: Path,
    max_duration: float = 15.0,
) -> Path:
    """Extract a fair-use clip (5-15s) from a source video.

    Clips are short excerpts for commentary — transformative fair use.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = min(end - start, max_duration)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(max(0, start - 0.5)),
        "-i", str(source_video),
        "-t", str(duration),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black",
        *_get_encoder(),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("Extracted clip: %.1fs from %s", duration, source_video.name)
    return output_path


def add_analysis_overlay(
    clip_path: Path,
    output_path: Path,
    top_text: str = "",
    bottom_text: str = "",
    highlight_color: str = "yellow",
) -> Path:
    """Add analytical text overlays to a clip.

    Top text: context (e.g., "KSI as Imposter — Episode 47")
    Bottom text: analysis note (e.g., "Notice how he avoids eye contact")
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    vf_parts = []

    if top_text:
        safe = top_text.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
        vf_parts.append(
            f"drawtext=text='{safe}':"
            f"fontsize=28:fontcolor={highlight_color}:borderw=2:bordercolor=black:"
            f"box=1:boxcolor=black@0.7:boxborderw=8:"
            f"x=(w-tw)/2:y=20"
        )

    if bottom_text:
        safe = bottom_text.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
        vf_parts.append(
            f"drawtext=text='{safe}':"
            f"fontsize=24:fontcolor=white:borderw=2:bordercolor=black:"
            f"box=1:boxcolor=black@0.7:boxborderw=6:"
            f"x=(w-tw)/2:y=h-60"
        )

    if not vf_parts:
        import shutil
        shutil.copy2(str(clip_path), str(output_path))
        return output_path

    vf = ",".join(vf_parts)

    cmd = [
        "ffmpeg", "-y", "-i", str(clip_path),
        "-vf", vf,
        *_get_encoder(),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path


def create_data_card(
    stats: dict[str, str],
    title: str,
    output_path: Path,
    duration: float = 5.0,
    canvas_w: int = 1920,
    canvas_h: int = 1080,
) -> Path:
    """Create an animated stat card video (like sports broadcast graphics).

    Shows key statistics with a dark background and clean typography.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build drawtext filters for each stat (no enable expressions for Windows compat)
    vf_parts = []

    # Title
    title_safe = title.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
    vf_parts.append(
        f"drawtext=text='{title_safe}':"
        f"fontsize=56:fontcolor=red:borderw=3:bordercolor=white:"
        f"x=(w-tw)/2:y=80"
    )

    # Stats
    y_pos = 200
    for key, value in stats.items():
        key_safe = key.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
        val_safe = str(value).replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")

        vf_parts.append(
            f"drawtext=text='{key_safe}':"
            f"fontsize=36:fontcolor=white:borderw=2:bordercolor=black:"
            f"x=200:y={y_pos}"
        )
        vf_parts.append(
            f"drawtext=text='{val_safe}':"
            f"fontsize=42:fontcolor=yellow:borderw=2:bordercolor=black:"
            f"x=w-tw-200:y={y_pos}"
        )
        y_pos += 70

    vf = ",".join(vf_parts)

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={canvas_w}x{canvas_h}:d={duration}:r=25",
        "-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", vf,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("Data card created: %s (%d stats)", title, len(stats))
    return output_path


def create_narration_segment(
    narration_path: Path,
    output_path: Path,
    visual_text: str = "",
    canvas_w: int = 1920,
    canvas_h: int = 1080,
) -> Path:
    """Create a segment with narration over a dark background with text.

    Used for analysis sections where no clip is available.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get narration duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(narration_path)],
        capture_output=True, text=True,
    )
    dur = float(probe.stdout.strip()) + 1.0

    vf = ""
    if visual_text:
        safe = visual_text.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
        # Word wrap long text by splitting
        lines = []
        words = safe.split()
        line = ""
        for word in words:
            if len(line + " " + word) > 40:
                lines.append(line.strip())
                line = word
            else:
                line += " " + word
        if line.strip():
            lines.append(line.strip())

        vf_parts = []
        for i, ln in enumerate(lines[:4]):
            y = 400 + i * 50
            vf_parts.append(
                f"drawtext=text='{ln}':"
                f"fontsize=32:fontcolor=white:borderw=2:bordercolor=black:"
                f"x=(w-tw)/2:y={y}"
            )
        vf = ",".join(vf_parts)

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={canvas_w}x{canvas_h}:d={dur}:r=25",
        "-i", str(narration_path),
        "-map", "0:v", "-map", "1:a",
        "-shortest",
    ]

    if vf:
        cmd.extend(["-vf", vf])

    cmd.extend([
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(output_path),
    ])
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path
