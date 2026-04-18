"""
Video editor for oddly satisfying YouTube Shorts.

Handles cropping to 9:16 portrait, compiling multiple clips,
adding text overlays, transitions, and background music.
Uses NVENC GPU acceleration when available.
"""

from __future__ import annotations

import logging
import random
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Engaging text overlays for satisfying content
HOOK_TEXTS = [
    "Wait for it...",
    "So satisfying!",
    "Watch till the end!",
    "This is oddly satisfying",
    "Can you watch without smiling?",
    "Pure satisfaction",
    "This hits different",
    "I could watch this all day",
]

ENDING_TEXTS = [
    "Follow for more!",
    "Like if satisfied!",
    "Share this vibe!",
    "Subscribe for daily satisfying!",
]


def _get_encoder() -> list[str]:
    """Return NVENC encoder args if a usable GPU is present, else libx264."""
    from peanut_reacts.core.ffmpeg import nvenc_available
    if nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", "fast", "-b:v", "6M"]
    return ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]


def crop_to_portrait(input_path: Path, output_path: Path) -> Path:
    """Center-crop a landscape video to 9:16 portrait for Shorts."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get dimensions
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(input_path)],
        capture_output=True, text=True,
    )
    dims = probe.stdout.strip().split("\n")[0]
    w, h = map(int, dims.split(","))

    if w > h:
        # Landscape → crop to center portrait
        # Target: 9:16 ratio from the center
        target_w = int(h * 9 / 16)
        target_w = target_w if target_w % 2 == 0 else target_w + 1
        crop_filter = f"crop={target_w}:{h}:(iw-{target_w})/2:0"
    else:
        # Already portrait or square — just scale
        crop_filter = f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0"

    # Scale to 1080x1920 (standard Shorts resolution)
    vf = f"{crop_filter},scale=1080:1920:flags=lanczos"

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", vf,
        *_get_encoder(),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("Cropped to portrait: %s", output_path.name)
    return output_path


def compile_short(
    clips: list[Path],
    output_path: Path,
    target_duration: float = 50.0,
    crossfade: float = 0.3,
) -> Path:
    """Compile multiple clips into a single Short with crossfade transitions.

    Trims clips to fit within target_duration.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(clips) == 1:
        # Single clip — just trim
        cmd = [
            "ffmpeg", "-y", "-i", str(clips[0]),
            "-t", str(target_duration),
            *_get_encoder(),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return output_path

    # Multiple clips — get durations and calculate trim
    durations = []
    for clip in clips:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(clip)],
            capture_output=True, text=True,
        )
        durations.append(float(probe.stdout.strip()))

    total = sum(durations)
    if total > target_duration:
        # Proportionally trim each clip
        scale = target_duration / total
        durations = [d * scale for d in durations]

    # Build filter for crossfade concat
    if len(clips) == 2:
        # Simple 2-clip crossfade
        d0, d1 = durations
        fade_start = d0 - crossfade
        filter_complex = (
            f"[0:v]trim=0:{d0},setpts=PTS-STARTPTS[v0];"
            f"[1:v]trim=0:{d1},setpts=PTS-STARTPTS[v1];"
            f"[v0][v1]xfade=transition=fade:duration={crossfade}:offset={fade_start}[v];"
            f"[0:a]atrim=0:{d0},asetpts=PTS-STARTPTS[a0];"
            f"[1:a]atrim=0:{d1},asetpts=PTS-STARTPTS[a1];"
            f"[a0][a1]acrossfade=d={crossfade}[a]"
        )
        inputs = ["-i", str(clips[0]), "-i", str(clips[1])]
    else:
        # 3+ clips — use concat (simpler, no crossfade)
        from peanut_reacts.core.ffmpeg import concatenate

        # Trim each clip first
        trimmed = []
        for i, (clip, dur) in enumerate(zip(clips, durations)):
            trimmed_path = output_path.with_name(f"_trim_{i}.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-i", str(clip),
                "-t", str(dur), "-c", "copy",
                str(trimmed_path),
            ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            trimmed.append(trimmed_path)

        concatenate(trimmed, output_path)

        # Cleanup
        for t in trimmed:
            t.unlink(missing_ok=True)
        return output_path

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        *_get_encoder(),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("Compiled %d clips → %s", len(clips), output_path.name)
    return output_path


def add_text_overlay(
    input_path: Path,
    output_path: Path,
    hook_text: str = "",
    ending_text: str = "",
) -> Path:
    """Add engaging text overlays to a Short."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not hook_text:
        hook_text = random.choice(HOOK_TEXTS)
    if not ending_text:
        ending_text = random.choice(ENDING_TEXTS)

    # Get duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(input_path)],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())

    # Escape text for ffmpeg
    hook_safe = hook_text.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
    end_safe = ending_text.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")

    # Hook text: first 3 seconds, centered
    # Ending text: last 3 seconds, centered
    end_start = max(0, duration - 4)

    vf = (
        f"drawtext=text='{hook_safe}':"
        f"fontsize=48:fontcolor=white:borderw=3:bordercolor=black:"
        f"box=1:boxcolor=black@0.5:boxborderw=10:"
        f"x=(w-tw)/2:y=h*0.15:"
        f"enable='between(t,0.5,3.5)',"
        f"drawtext=text='{end_safe}':"
        f"fontsize=36:fontcolor=white:borderw=2:bordercolor=black:"
        f"box=1:boxcolor=black@0.5:boxborderw=8:"
        f"x=(w-tw)/2:y=h*0.85:"
        f"enable='between(t,{end_start},{duration})'"
    )

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", vf,
        *_get_encoder(),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("Added text overlays: '%s' / '%s'", hook_text, ending_text)
    return output_path


def add_background_music(
    input_path: Path,
    music_path: Path,
    output_path: Path,
    music_volume: float = 0.12,
) -> Path:
    """Mix background music under the video's original audio."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get video duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(input_path)],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())

    filter_complex = (
        f"[1:a]volume={music_volume},afade=t=out:st={duration-2}:d=2[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-stream_loop", "-1", "-i", str(music_path),
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration),
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("Added background music (vol=%.0f%%)", music_volume * 100)
    return output_path


def extract_thumbnail(input_path: Path, output_path: Path) -> Path:
    """Extract the most visually interesting frame as thumbnail.

    Takes frame at 30% of duration (usually the most satisfying moment).
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(input_path)],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())
    timestamp = duration * 0.3

    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-i", str(input_path),
        "-ss", str(timestamp), "-frames:v", "1",
        "-q:v", "2",
        str(output_path),
    ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path
