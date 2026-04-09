"""
Video compiler for scary/creepy compilation videos.

Assembles footage, narration, text overlays, and music into
long-form (8-15 min) compilation videos and extracts Shorts.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from peanut_reacts.compilations.script_generator import CompilationScript, ScriptSegment
from peanut_reacts.shorts.editor import _get_encoder

logger = logging.getLogger(__name__)


@dataclass
class CompiledSegment:
    """A rendered segment ready for concatenation."""
    number: int
    video_path: Path
    duration: float
    intensity: str


def render_segment(
    segment: ScriptSegment,
    footage_path: Path,
    narration_path: Path,
    output_path: Path,
    canvas_w: int = 1920,
    canvas_h: int = 1080,
) -> CompiledSegment:
    """Render a single numbered segment: footage + narration + text overlay.

    Creates a segment with:
    - The footage clip as background
    - Narration audio mixed with original audio (ducked)
    - "Number X" text overlay at the start
    - Segment title as lower-third text
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get narration duration to set segment length
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(narration_path)],
        capture_output=True, text=True,
    )
    narration_dur = float(probe.stdout.strip())
    segment_dur = narration_dur + 3.0  # 1.5s intro title + 1.5s buffer

    # Escape text for ffmpeg
    title_safe = segment.title.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
    number_text = f"Number {segment.number}" if segment.number > 0 else "Number 1"

    # Color based on intensity
    if segment.intensity == "high":
        number_color = "red"
        glow = "red@0.3"
    elif segment.intensity == "medium":
        number_color = "orange"
        glow = "orange@0.2"
    else:
        number_color = "white"
        glow = "white@0.1"

    # Video filter: scale footage + add text overlays
    vf = (
        f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
        f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        # Number text (big, centered, shown for first 2 seconds)
        f"drawtext=text='{number_text}':"
        f"fontsize=96:fontcolor={number_color}:borderw=4:bordercolor=black:"
        f"x=(w-tw)/2:y=(h-th)/2:"
        f"enable='between(t,0,2)',"
        # Title text (lower third, shown after number fades)
        f"drawtext=text='{title_safe}':"
        f"fontsize=36:fontcolor=white:borderw=2:bordercolor=black:"
        f"box=1:boxcolor=black@0.6:boxborderw=10:"
        f"x=(w-tw)/2:y=h-80:"
        f"enable='between(t,2,{segment_dur})'"
    )

    # Audio filter: mix narration with footage audio (ducked)
    af = (
        f"[0:a]volume=0.15[bg];"
        f"[1:a]adelay=1500|1500,volume=1.5[narr];"
        f"[bg][narr]amix=inputs=2:duration=first:dropout_transition=2[a]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(footage_path),  # loop footage if shorter
        "-i", str(narration_path),
        "-t", str(segment_dur),
        "-vf", vf,
        "-filter_complex", af,
        "-map", "0:v", "-map", "[a]",
        *_get_encoder(),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        # Fallback: try without audio mixing (footage may not have audio)
        cmd_fallback = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(footage_path),
            "-i", str(narration_path),
            "-t", str(segment_dur),
            "-vf", vf,
            "-map", "0:v", "-map", "1:a",
            *_get_encoder(),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ]
        subprocess.run(cmd_fallback, check=True, capture_output=True)

    logger.info("Segment #%d rendered: %s (%.1fs)", segment.number, output_path.name, segment_dur)
    return CompiledSegment(
        number=segment.number,
        video_path=output_path,
        duration=segment_dur,
        intensity=segment.intensity,
    )


def render_intro(
    narration_path: Path,
    output_path: Path,
    title: str = "",
    canvas_w: int = 1920,
    canvas_h: int = 1080,
) -> Path:
    """Render the intro segment with hook narration over dark background."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(narration_path)],
        capture_output=True, text=True,
    )
    dur = float(probe.stdout.strip()) + 1.5

    title_safe = title.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%") if title else "Top 10"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={canvas_w}x{canvas_h}:d={dur}:r=25",
        "-i", str(narration_path),
        "-vf", (
            f"drawtext=text='{title_safe}':"
            f"fontsize=64:fontcolor=red:borderw=3:bordercolor=black:"
            f"x=(w-tw)/2:y=(h-th)/2:"
            f"enable='between(t,0,{dur})'"
        ),
        "-map", "0:v", "-map", "1:a",
        "-shortest",
        *_get_encoder(),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info("Intro rendered: %.1fs", dur)
    return output_path


def render_outro(
    narration_path: Path,
    output_path: Path,
    canvas_w: int = 1920,
    canvas_h: int = 1080,
) -> Path:
    """Render the outro with CTA narration."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(narration_path)],
        capture_output=True, text=True,
    )
    dur = float(probe.stdout.strip()) + 2.0

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={canvas_w}x{canvas_h}:d={dur}:r=25",
        "-i", str(narration_path),
        "-vf", (
            "drawtext=text='SUBSCRIBE for more!':"
            "fontsize=56:fontcolor=red:borderw=3:bordercolor=white:"
            f"x=(w-tw)/2:y=(h-th)/2:enable='between(t,0,{dur})',"
            "drawtext=text='Like & Share if you were scared!':"
            "fontsize=32:fontcolor=white:borderw=2:bordercolor=black:"
            f"x=(w-tw)/2:y=(h-th)/2+80:enable='between(t,1,{dur})'"
        ),
        "-map", "0:v", "-map", "1:a",
        "-shortest",
        *_get_encoder(),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info("Outro rendered: %.1fs", dur)
    return output_path


def compile_long_form(
    segments: list[Path],
    output_path: Path,
    music_path: Optional[Path] = None,
    music_volume: float = 0.08,
) -> Path:
    """Concatenate all segments into the final long-form video.

    Optionally adds background music throughout.
    """
    from peanut_reacts.core.ffmpeg import concatenate

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if music_path and music_path.exists():
        # First concat without music
        raw = output_path.with_name(output_path.stem + "_raw.mp4")
        concatenate(segments, raw)

        # Then add music
        from peanut_reacts.shorts.editor import add_background_music
        add_background_music(raw, music_path, output_path, music_volume=music_volume)
        raw.unlink(missing_ok=True)
    else:
        concatenate(segments, output_path)

    logger.info("Long-form compiled: %s", output_path.name)
    return output_path


def extract_shorts(
    long_form: Path,
    compiled_segments: list[CompiledSegment],
    output_dir: Path,
    max_shorts: int = 5,
) -> list[Path]:
    """Extract the most intense moments as YouTube Shorts (<60s, 9:16).

    Picks the highest-intensity segments and crops them to portrait.
    """
    from peanut_reacts.shorts.editor import crop_to_portrait, add_text_overlay

    output_dir.mkdir(parents=True, exist_ok=True)

    # Sort by intensity (high first) then by number (lower number = more intense)
    intensity_order = {"high": 0, "medium": 1, "low": 2}
    ranked = sorted(
        compiled_segments,
        key=lambda s: (intensity_order.get(s.intensity, 2), s.number),
    )

    shorts: list[Path] = []
    for i, seg in enumerate(ranked[:max_shorts]):
        if seg.duration > 58:
            # Trim to 55 seconds
            trimmed = output_dir / f"trimmed_{i}.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-i", str(seg.video_path),
                "-t", "55", "-c", "copy", str(trimmed),
            ], check=True, capture_output=True)
            source = trimmed
        else:
            source = seg.video_path

        # Crop to portrait
        portrait = output_dir / f"short_{i + 1:02d}_portrait.mp4"
        crop_to_portrait(source, portrait)

        # Add hook text
        final = output_dir / f"short_{i + 1:02d}.mp4"
        add_text_overlay(
            portrait, final,
            hook_text=f"#{seg.number} will TERRIFY you!",
            ending_text="Subscribe for more scary content!",
        )

        shorts.append(final)
        logger.info("Short %d extracted from segment #%d", i + 1, seg.number)

        # Cleanup
        if source != seg.video_path:
            source.unlink(missing_ok=True)
        portrait.unlink(missing_ok=True)

    return shorts
