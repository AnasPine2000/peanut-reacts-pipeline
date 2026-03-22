"""
GIF/video overlay via ffmpeg.

Overlays an animated GIF (or video) on top of a base video with
configurable scale and position.

Refactored from archive/overlay_gif_video.py.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def overlay_gif(
    base_video: Path,
    gif_path: Path,
    output_path: Path,
    *,
    scale: float = 1.25,
    x: str = "W-w-40",
    y: str = "H-h-40",
) -> Path:
    """Overlay *gif_path* on *base_video* with looping, scaling, and positioning.

    *scale* is the overlay width as a fraction of the base video width.
    *x* and *y* are ffmpeg position expressions.
    """
    vf = (
        f"[1:v]format=rgba[gif];"
        f"[gif][0:v]scale2ref=w=main_w*{scale}:h=-1[ov][base];"
        f"[base][ov]overlay=x={x}:y={y}:format=auto[v]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(base_video),
        "-stream_loop", "-1", "-i", str(gif_path),
        "-filter_complex", vf,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]

    logger.info("Overlaying %s onto %s ...", gif_path.name, base_video.name)
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("Output: %s", output_path)
    return output_path
