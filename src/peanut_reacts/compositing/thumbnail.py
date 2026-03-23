"""
YouTube thumbnail generator.

Generates a 1280x720 thumbnail by extracting a key frame from the video
and overlaying the peanut character + bold title text.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from peanut_reacts.character.renderer import draw_peanut_character

logger = logging.getLogger(__name__)

THUMBNAIL_W = 1280
THUMBNAIL_H = 720


def extract_frame(video_path: Path, timestamp: float, output_path: Path) -> Path:
    """Extract a single frame from a video at the given timestamp."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-vframes", "1",
        "-s", f"{THUMBNAIL_W}x{THUMBNAIL_H}",
        str(output_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return output_path


def _draw_text_with_outline(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple = (255, 255, 255, 255),
    outline: tuple = (0, 0, 0, 255),
    outline_width: int = 4,
) -> None:
    """Draw text with thick outline for readability."""
    x, y = position
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if abs(dx) + abs(dy) > 0:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def generate_thumbnail(
    video_path: Path,
    output_path: Path,
    *,
    title_text: str = "",
    emotion: str = "excited",
    peanut_size: int = 280,
    video_duration: float = 0.0,
) -> Path:
    """Generate a 1280x720 YouTube thumbnail.

    Extracts a key frame, overlays peanut character and title text.
    Returns path to the generated thumbnail JPEG.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Extract frame at 30% of video
    timestamp = max(1.0, video_duration * 0.3) if video_duration > 0 else 10.0
    frame_path = output_path.with_suffix(".frame.png")

    try:
        extract_frame(video_path, timestamp, frame_path)
        bg = Image.open(frame_path).convert("RGBA")
    except Exception:
        # Fallback: solid dark background
        logger.warning("Could not extract frame, using dark background")
        bg = Image.new("RGBA", (THUMBNAIL_W, THUMBNAIL_H), (20, 20, 40, 255))

    # Ensure correct size
    bg = bg.resize((THUMBNAIL_W, THUMBNAIL_H), Image.LANCZOS)

    # Boost contrast slightly for visual pop
    enhancer = ImageEnhance.Contrast(bg.convert("RGB"))
    bg = enhancer.enhance(1.2).convert("RGBA")

    # Draw peanut character (bottom-right)
    peanut = draw_peanut_character(t=0.5, size=peanut_size, seed=0, emotion=emotion)
    peanut_x = THUMBNAIL_W - peanut_size - 40
    peanut_y = THUMBNAIL_H - peanut_size - 20
    bg.alpha_composite(peanut, (peanut_x, peanut_y))

    # Draw title text (top-left area, wrapped)
    if title_text:
        draw = ImageDraw.Draw(bg)

        # Try to load a bold font, fall back to default
        font_size = 52
        try:
            font = ImageFont.truetype("C:/WINDOWS/fonts/arialbd.ttf", font_size)
        except OSError:
            try:
                font = ImageFont.truetype("C:/WINDOWS/fonts/arial.ttf", font_size)
            except OSError:
                font = ImageFont.load_default()

        # Word-wrap the title
        words = title_text.split()
        lines: list[str] = []
        current = ""
        max_width = THUMBNAIL_W - peanut_size - 100  # leave space for peanut

        for word in words:
            test = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_width and current:
                lines.append(current)
                current = word
            else:
                current = test
        if current:
            lines.append(current)

        # Draw each line with outline
        y_pos = 40
        for line in lines[:3]:  # max 3 lines
            _draw_text_with_outline(
                draw, (40, y_pos), line, font,
                fill=(255, 255, 50, 255),  # yellow
                outline=(0, 0, 0, 255),
                outline_width=4,
            )
            y_pos += font_size + 10

    # Save as JPEG
    rgb = bg.convert("RGB")
    rgb.save(str(output_path), "JPEG", quality=95)

    # Cleanup frame
    frame_path.unlink(missing_ok=True)

    logger.info("Generated thumbnail: %s", output_path.name)
    return output_path
