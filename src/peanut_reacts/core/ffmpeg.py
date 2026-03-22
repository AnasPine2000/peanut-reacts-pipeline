"""
Shared FFmpeg / FFprobe operations.

Consolidates video utility functions previously duplicated across 7+ scripts.
All functions accept Path objects and raise on failure.
"""

from __future__ import annotations

import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_video_duration(video_path: Path) -> float:
    """Return the duration (in seconds) of a video file using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    output = subprocess.check_output(cmd, stderr=subprocess.PIPE).decode().strip()
    return float(output)


def is_valid_video(video_path: Path) -> bool:
    """Check whether *video_path* contains a decodable video stream."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=nw=1:nk=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        )
        return bool(result.stdout.strip())
    except (subprocess.CalledProcessError, Exception):
        return False


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------

def split_video(
    video_path: Path,
    chunk_duration: int = 900,
    output_dir: Optional[Path] = None,
) -> list[Path]:
    """Split *video_path* into chunks of *chunk_duration* seconds via ffmpeg segment.

    Returns a sorted list of chunk file paths.
    """
    base_dir = output_dir or video_path.parent
    base_name = video_path.stem
    ext = video_path.suffix
    split_pattern = str(base_dir / f"{base_name}_chunk_%03d{ext}")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-c", "copy",
        "-map", "0",
        "-segment_time", str(chunk_duration),
        "-f", "segment",
        split_pattern,
    ]
    logger.info("Splitting video into chunks (%ds each) ...", chunk_duration)
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    chunk_files = sorted(
        base_dir / f
        for f in os.listdir(base_dir)
        if f.startswith(f"{base_name}_chunk_") and f.endswith(ext)
    )
    logger.info("Created %d chunk(s).", len(chunk_files))
    return chunk_files


# ---------------------------------------------------------------------------
# Cutting
# ---------------------------------------------------------------------------

def cut_segment(
    input_path: Path,
    start: float,
    end: float,
    output_path: Path,
    *,
    codec: str = "copy",
) -> Path:
    """Cut a time range [*start*, *end*] (seconds) from *input_path*.

    *codec* can be ``"copy"`` (fast, no re-encode) or e.g.
    ``"libx264"`` / ``"h264_nvenc"`` for re-encoding.
    """
    logger.info("Cutting segment %s (%.2fs \u2192 %.2fs)", output_path.name, start, end)

    if codec == "copy":
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-ss", str(start),
            "-to", str(end),
            "-c", "copy",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-ss", str(start),
            "-to", str(end),
            "-c:v", codec, "-c:a", "aac",
            str(output_path),
        ]

    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return output_path


# ---------------------------------------------------------------------------
# Concatenation
# ---------------------------------------------------------------------------

def concatenate(
    clip_paths: list[Path],
    output_path: Path,
    *,
    shuffle: bool = False,
) -> Path:
    """Concatenate *clip_paths* into *output_path* via ffmpeg's concat demuxer.

    If *shuffle* is True, the order is randomised before concatenation.
    """
    import random

    paths = list(clip_paths)
    if shuffle:
        random.shuffle(paths)

    list_file = output_path.parent / f"_concat_{uuid.uuid4().hex[:8]}.txt"
    try:
        with open(list_file, "w", encoding="utf-8") as f:
            for p in paths:
                abs_path = str(p.resolve()).replace("'", "'\\''")
                f.write(f"file '{abs_path}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(output_path),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logger.info("Concatenated %d clips into %s", len(paths), output_path.name)
    finally:
        list_file.unlink(missing_ok=True)

    return output_path


def concatenate_with_transitions(
    clip_paths: list[Path],
    transition_path: Path,
    output_path: Path,
) -> Path:
    """Concatenate clips with a transition video inserted between each pair."""
    list_file = output_path.parent / f"_concat_{uuid.uuid4().hex[:8]}.txt"
    try:
        with open(list_file, "w", encoding="utf-8") as f:
            for i, clip in enumerate(clip_paths):
                f.write(f"file '{clip.resolve()}'\n")
                if i < len(clip_paths) - 1:
                    f.write(f"file '{transition_path.resolve()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(output_path),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logger.info("Concatenated %d clips with transitions.", len(clip_paths))
    finally:
        list_file.unlink(missing_ok=True)

    return output_path


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------

def extract_audio(
    video_path: Path,
    audio_path: Path,
    *,
    sample_rate: int = 44100,
    channels: int = 2,
) -> Path:
    """Extract audio from *video_path* to WAV at *audio_path*.

    Skips extraction if *audio_path* already exists (caching).
    """
    if audio_path.exists():
        logger.info("Audio cache hit: %s", audio_path.name)
        return audio_path

    logger.info("Extracting audio from %s ...", video_path.name)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        str(audio_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return audio_path


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------

def overlay_video(
    base: Path,
    overlay: Path,
    output: Path,
    *,
    x: str = "W-w-10",
    y: str = "10",
) -> Path:
    """Overlay *overlay* video on top of *base* at position (*x*, *y*).

    *x* and *y* are ffmpeg filter expressions (e.g. ``"W-w-10"``).
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(base),
        "-i", str(overlay),
        "-filter_complex", f"[0:v][1:v]overlay={x}:{y}",
        "-c:a", "copy",
        str(output),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    logger.info("Overlay complete: %s", output.name)
    return output


# ---------------------------------------------------------------------------
# Re-encoding (GPU)
# ---------------------------------------------------------------------------

def reencode_nvenc(
    input_path: Path,
    output_path: Path,
    *,
    video_bitrate: str = "5M",
    audio_bitrate: str = "192k",
) -> Path:
    """Re-encode to MP4 using NVIDIA NVENC hardware acceleration."""
    cmd = [
        "ffmpeg", "-y",
        "-hwaccel", "cuda",
        "-i", str(input_path),
        "-c:v", "h264_nvenc",
        "-preset", "fast",
        "-b:v", video_bitrate,
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        str(output_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return output_path
