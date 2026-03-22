"""
Video concatenation utilities.

Provides plain concatenation, concatenation with transitions, and
parallel GPU re-encoding for mixed-format inputs.

Refactored from archive/conc.py and archive/conc2.py.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from peanut_reacts.core.ffmpeg import (
    concatenate,
    concatenate_with_transitions,
    is_valid_video,
    overlay_video,
    reencode_nvenc,
)

logger = logging.getLogger(__name__)


def concat_validated(
    input_dir: Path,
    output_path: Path,
    *,
    shuffle: bool = True,
    extensions: tuple[str, ...] = (".mkv", ".webm", ".mp4", ".avi"),
) -> Path:
    """Validate and concatenate all video files in *input_dir*.

    Skips files that fail ffprobe validation.
    """
    video_files = [
        input_dir / f
        for f in sorted(os.listdir(input_dir))
        if f.lower().endswith(extensions)
    ]

    if not video_files:
        raise FileNotFoundError(f"No video files found in {input_dir}")

    valid = [v for v in video_files if is_valid_video(v)]
    skipped = len(video_files) - len(valid)
    if skipped:
        logger.warning("Skipped %d invalid video file(s).", skipped)

    if not valid:
        raise RuntimeError("No valid video files to concatenate.")

    return concatenate(valid, output_path, shuffle=shuffle)


def parallel_reencode(
    input_dir: Path,
    output_dir: Path,
    *,
    extensions: tuple[str, ...] = (".webm",),
    workers: int = 4,
    video_bitrate: str = "5M",
) -> list[Path]:
    """Re-encode all files matching *extensions* to MP4 using NVENC in parallel."""
    output_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(
        input_dir / f
        for f in os.listdir(input_dir)
        if f.lower().endswith(extensions)
    )

    if not files:
        logger.warning("No files matching %s in %s", extensions, input_dir)
        return []

    encoded: list[Path] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for f in files:
            out = output_dir / f"{f.stem}.mp4"
            futures[executor.submit(reencode_nvenc, f, out, video_bitrate=video_bitrate)] = f

        for future in as_completed(futures):
            src = futures[future]
            try:
                result = future.result()
                encoded.append(result)
                logger.info("Encoded: %s", src.name)
            except Exception as exc:
                logger.error("Failed to encode %s: %s", src.name, exc)

    return encoded
