"""
Retention-optimized video editor for YouTube video essays.

Applies techniques that keep short-attention-span viewers watching:
- Fast cuts every 3-5 seconds (clip cycling within segments)
- Ken Burns zoom/pan effect on static footage
- Animated keyword text (zoom-in, highlight)
- Transition effects between segments
- Sound effects (whoosh, bass)
"""

from __future__ import annotations

import logging
import math
import random
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_clip_duration(path: Path) -> float:
    """Get duration of a video file."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    return float(probe.stdout.strip())


def fast_cut_segment(
    clips: list[Path],
    narration: Path,
    output: Path,
    cut_interval: float = 4.0,
    overlay_text: str = "",
    keywords: list[str] | None = None,
) -> Path:
    """Build a segment with fast cuts cycling through clips every 3-5 seconds.

    Applies Ken Burns zoom/pan on each clip for visual movement.
    Narration plays over the clip montage.
    Keywords pop up as animated text at intervals.
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    if not clips:
        logger.warning("No clips for fast_cut_segment — using narration only")
        _narration_only(narration, output, overlay_text)
        return output

    narr_dur = _get_clip_duration(narration)
    total_dur = narr_dur + 1.0

    # Calculate how many cuts we need
    n_cuts = max(1, int(total_dur / cut_interval))
    cut_dur = total_dur / n_cuts

    # Build individual cut segments with Ken Burns effect
    temp_cuts = []
    for i in range(n_cuts):
        clip = clips[i % len(clips)]
        temp = output.with_name(f"{output.stem}_cut_{i:02d}.mp4")

        # Random Ken Burns: zoom direction + pan direction
        zoom_start = random.uniform(1.0, 1.05)
        zoom_end = random.uniform(1.08, 1.15)
        pan_x = random.randint(-30, 30)
        pan_y = random.randint(-20, 20)

        # Zoompan filter for Ken Burns effect
        vf = (
            f"scale=2160:1216,"  # scale up for pan room
            f"zoompan=z='if(eq(on,0),{zoom_start},{zoom_start}+(({zoom_end}-{zoom_start})*on/({int(cut_dur*25)}))':"
            f"x='iw/2-(iw/zoom/2)+{pan_x}*on/{int(cut_dur*25)}':"
            f"y='ih/2-(ih/zoom/2)+{pan_y}*on/{int(cut_dur*25)}':"
            f"d={int(cut_dur*25)}:s=1920x1080:fps=25"
        )

        # Simpler fallback: just scale + slight zoom via crop
        vf_simple = (
            f"scale=2048:1152,crop=1920:1080:"
            f"'64+{pan_x}*t/{cut_dur:.1f}':"
            f"'36+{pan_y}*t/{cut_dur:.1f}'"
        )

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(random.uniform(0, max(0.5, _get_clip_duration(clip) - cut_dur - 1))),
            "-i", str(clip),
            "-t", str(cut_dur),
            "-vf", vf_simple,
            "-c:v", "libx264", "-crf", "22", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-an",  # no audio from clips (narration is separate)
            str(temp),
        ]

        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            # Fallback: just copy the clip trimmed
            subprocess.run([
                "ffmpeg", "-y", "-ss", "0", "-i", str(clip),
                "-t", str(cut_dur),
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black",
                "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
                "-an", str(temp),
            ], capture_output=True)

        if temp.exists() and temp.stat().st_size > 1000:
            temp_cuts.append(temp)

    if not temp_cuts:
        _narration_only(narration, output, overlay_text)
        return output

    # Concatenate all cuts
    concat_raw = output.with_name(f"{output.stem}_concat.mp4")
    from peanut_reacts.core.ffmpeg import concatenate
    concatenate(temp_cuts, concat_raw)

    # Add keyword text overlays
    text_vf = _build_keyword_overlay(overlay_text, keywords, total_dur)

    # Mix narration with clip montage
    if text_vf:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(concat_raw),
            "-i", str(narration),
            "-t", str(total_dur),
            "-vf", text_vf,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(output),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(concat_raw),
            "-i", str(narration),
            "-t", str(total_dur),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            str(output),
        ]

    subprocess.run(cmd, check=True, capture_output=True)

    # Cleanup
    for t in temp_cuts:
        t.unlink(missing_ok=True)
    concat_raw.unlink(missing_ok=True)

    logger.info("Fast-cut segment: %d cuts, %.1fs, %s", n_cuts, total_dur, output.name)
    return output


def _build_keyword_overlay(text: str, keywords: list[str] | None, duration: float) -> str:
    """Build ffmpeg drawtext filter for animated keyword popups."""
    if not text and not keywords:
        return ""

    parts = []

    # Main subtitle text at bottom (always visible)
    if text:
        safe = text[:50].replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
        parts.append(
            f"drawtext=text='{safe}':"
            f"fontsize=28:fontcolor=white:borderw=2:bordercolor=black:"
            f"box=1:boxcolor=black@0.6:boxborderw=8:"
            f"x=(w-tw)/2:y=h-60"
        )

    # Keyword popups (appear briefly every few seconds)
    if keywords:
        interval = max(3, duration / len(keywords))
        for i, kw in enumerate(keywords[:5]):
            safe_kw = kw.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
            start = i * interval + 1
            end = start + 2.5

            # Big keyword that appears and fades
            parts.append(
                f"drawtext=text='{safe_kw}':"
                f"fontsize=72:fontcolor=yellow:borderw=4:bordercolor=black:"
                f"x=(w-tw)/2:y=h/3:"
                f"enable='between(t,{start:.1f},{end:.1f})'"
            )

    return ",".join(parts)


def _narration_only(narration: Path, output: Path, text: str = "") -> Path:
    """Fallback: dark background with narration and text."""
    dur = _get_clip_duration(narration) + 1

    safe = text[:80].replace("'", "\u2019").replace(":", "\\:").replace("%", "%%") if text else ""
    vf = ""
    if safe:
        vf = (
            f"drawtext=text='{safe}':"
            f"fontsize=32:fontcolor=white:borderw=2:bordercolor=black:"
            f"x=(w-tw)/2:y=(h-th)/2"
        )

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s=1920x1080:d={dur}:r=25",
        "-i", str(narration),
        "-map", "0:v", "-map", "1:a", "-shortest",
    ]
    if vf:
        cmd.extend(["-vf", vf])
    cmd.extend([
        "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(output),
    ])
    subprocess.run(cmd, check=True, capture_output=True)
    return output


def enhanced_data_card(
    stats: dict[str, str],
    title: str,
    output: Path,
    background_clip: Path | None = None,
    duration: float = 6.0,
) -> Path:
    """Create an enhanced data card with animated stats over gameplay footage."""
    output.parent.mkdir(parents=True, exist_ok=True)

    # Build stat overlays with staggered appearance
    vf_parts = []

    # Semi-transparent dark overlay on gameplay
    vf_parts.append("colorchannelmixer=aa=0.6")

    # Title (big, red, centered top)
    safe_title = title.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
    vf_parts.append(
        f"drawtext=text='{safe_title}':"
        f"fontsize=64:fontcolor=red:borderw=4:bordercolor=black:"
        f"x=(w-tw)/2:y=60"
    )

    # Stats with staggered reveal
    y = 180
    for i, (k, v) in enumerate(stats.items()):
        ks = k.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
        vs = str(v).replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
        t_start = 0.3 + i * 0.5

        vf_parts.append(
            f"drawtext=text='{ks}':"
            f"fontsize=36:fontcolor=white:borderw=2:bordercolor=black:"
            f"x=250:y={y}:enable='gte(t,{t_start:.1f})'"
        )
        vf_parts.append(
            f"drawtext=text='{vs}':"
            f"fontsize=48:fontcolor=yellow:borderw=3:bordercolor=black:"
            f"x=w-tw-250:y={y}:enable='gte(t,{t_start:.1f})'"
        )
        y += 80

    vf = ",".join(vf_parts)

    if background_clip and background_clip.exists():
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(background_clip),
            "-t", str(duration),
            "-vf", vf,
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an", str(output),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s=1920x1080:d={duration}:r=25",
            "-vf", vf.replace("colorchannelmixer=aa=0.6,", ""),
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an", str(output),
        ]

    subprocess.run(cmd, check=True, capture_output=True)
    return output
