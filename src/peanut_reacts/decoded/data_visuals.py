"""
Data visualization generator for Sidemen Among Us Decoded.

Creates ESPN-style animated graphics:
- Player stat cards with animated counters
- Comparison bar charts
- Win rate pie charts
- Accusation network maps
- Rivalry heat maps
- Timeline graphics
- Quote callouts

All rendered via PIL → video frames → ffmpeg encode.
No copyrighted footage needed.
"""

from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Color scheme (dark sports broadcast style)
BG_COLOR = (18, 18, 35)        # deep navy
ACCENT_RED = (220, 40, 40)
ACCENT_YELLOW = (255, 215, 0)
ACCENT_GREEN = (40, 200, 80)
ACCENT_BLUE = (50, 130, 255)
TEXT_WHITE = (255, 255, 255)
TEXT_GRAY = (160, 160, 180)
CARD_BG = (30, 30, 55)

# Player colors
PLAYER_COLORS = {
    "Simon": (50, 200, 80),    # green (Among Us character)
    "KSI": (220, 40, 40),     # red
    "Harry": (50, 130, 255),   # blue
    "Tobi": (255, 165, 0),    # orange
    "Ethan": (255, 105, 180), # pink
    "Vik": (148, 103, 189),   # purple
    "Josh": (255, 255, 100),  # yellow
    "Deji": (0, 200, 200),    # cyan
}

WIDTH, HEIGHT = 1920, 1080


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a font, falling back to default."""
    try:
        if bold:
            return ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", size)
        return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
    except:
        return ImageFont.load_default()


def render_stat_card(
    player: str,
    stats: dict[str, str],
    title: str,
    output_dir: Path,
    duration: float = 6.0,
    fps: int = 25,
) -> Path:
    """Render an animated player stat card (ESPN-style).

    Stats appear one by one with a counter animation effect.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration * fps)
    player_color = PLAYER_COLORS.get(player, ACCENT_BLUE)

    frames_dir = output_dir / "stat_frames"
    frames_dir.mkdir(exist_ok=True)

    stat_items = list(stats.items())

    for frame_idx in range(n_frames):
        t = frame_idx / fps
        img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
        d = ImageDraw.Draw(img)

        # Title bar at top
        d.rectangle([0, 0, WIDTH, 80], fill=player_color)
        title_font = _get_font(42, bold=True)
        d.text((WIDTH // 2, 40), title.upper(), fill=TEXT_WHITE, font=title_font, anchor="mm")

        # Player name
        name_font = _get_font(72, bold=True)
        d.text((WIDTH // 2, 160), player.upper(), fill=player_color, font=name_font, anchor="mm")

        # Subtitle line
        d.line([(WIDTH // 2 - 200, 200), (WIDTH // 2 + 200, 200)], fill=TEXT_GRAY, width=2)
        sub_font = _get_font(24)
        d.text((WIDTH // 2, 220), "SIDEMEN AMONG US DECODED", fill=TEXT_GRAY, font=sub_font, anchor="mm")

        # Stats (staggered appearance)
        stat_font = _get_font(36)
        value_font = _get_font(56, bold=True)

        y_start = 290
        for i, (key, value) in enumerate(stat_items):
            appear_time = 0.5 + i * 0.6
            if t < appear_time:
                continue

            y = y_start + i * 100

            # Stat background card
            card_x1 = WIDTH // 2 - 350
            card_x2 = WIDTH // 2 + 350
            d.rounded_rectangle([card_x1, y, card_x2, y + 80], radius=10, fill=CARD_BG)

            # Key (left)
            d.text((card_x1 + 30, y + 40), key, fill=TEXT_GRAY, font=stat_font, anchor="lm")

            # Value (right) — animated counter effect
            elapsed = t - appear_time
            if elapsed < 0.5:
                # Counter animation
                progress = elapsed / 0.5
                try:
                    num = float(value.replace("%", "").replace("x", ""))
                    animated = f"{num * progress:.1f}" + ("%" if "%" in value else "x" if "x" in value else "")
                except:
                    animated = value
                d.text((card_x2 - 30, y + 40), animated, fill=ACCENT_YELLOW, font=value_font, anchor="rm")
            else:
                d.text((card_x2 - 30, y + 40), value, fill=ACCENT_YELLOW, font=value_font, anchor="rm")

        # Footer
        footer_font = _get_font(20)
        d.text((WIDTH // 2, HEIGHT - 40), "Data from 119 Sidemen Among Us episodes | sidemen-amongus-stats.streamlit.app",
               fill=TEXT_GRAY, font=footer_font, anchor="mm")

        img.save(frames_dir / f"frame_{frame_idx:04d}.png")

    # Encode
    output = output_dir / "stat_card.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(output),
    ], check=True, capture_output=True)

    # Cleanup frames
    import shutil
    shutil.rmtree(frames_dir, ignore_errors=True)

    logger.info("Stat card: %s", output.name)
    return output


def render_comparison_bars(
    data: dict[str, float],
    title: str,
    output_dir: Path,
    duration: float = 6.0,
    fps: int = 25,
    highlight_player: str = "",
    unit: str = "%",
) -> Path:
    """Render animated horizontal bar chart comparing players."""
    output_dir.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration * fps)

    frames_dir = output_dir / "bar_frames"
    frames_dir.mkdir(exist_ok=True)

    max_val = max(data.values()) if data else 1
    sorted_data = sorted(data.items(), key=lambda x: x[1], reverse=True)

    for frame_idx in range(n_frames):
        t = frame_idx / fps
        img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
        d = ImageDraw.Draw(img)

        # Title
        title_font = _get_font(48, bold=True)
        d.text((WIDTH // 2, 60), title.upper(), fill=TEXT_WHITE, font=title_font, anchor="mm")
        d.line([(100, 100), (WIDTH - 100, 100)], fill=TEXT_GRAY, width=1)

        # Bars
        bar_font = _get_font(32, bold=True)
        val_font = _get_font(28)
        bar_x_start = 300
        bar_max_width = WIDTH - 450
        bar_height = 50
        y_start = 150

        for i, (name, value) in enumerate(sorted_data):
            appear_time = 0.3 + i * 0.3
            if t < appear_time:
                continue

            y = y_start + i * (bar_height + 30)
            color = PLAYER_COLORS.get(name, ACCENT_BLUE)

            # Animate bar growth
            elapsed = t - appear_time
            progress = min(1.0, elapsed / 0.8)
            bar_width = int((value / max_val) * bar_max_width * progress)

            # Highlight the focus player
            if name == highlight_player:
                d.rounded_rectangle([bar_x_start - 5, y - 5, bar_x_start + bar_width + 5, y + bar_height + 5],
                                   radius=8, outline=ACCENT_YELLOW, width=3)

            # Player name
            d.text((bar_x_start - 20, y + bar_height // 2), name, fill=TEXT_WHITE, font=bar_font, anchor="rm")

            # Bar
            if bar_width > 0:
                d.rounded_rectangle([bar_x_start, y, bar_x_start + bar_width, y + bar_height],
                                   radius=5, fill=color)

            # Value
            animated_val = value * progress
            d.text((bar_x_start + bar_width + 15, y + bar_height // 2),
                   f"{animated_val:.1f}{unit}", fill=TEXT_WHITE, font=val_font, anchor="lm")

        # Footer
        footer_font = _get_font(20)
        d.text((WIDTH // 2, HEIGHT - 40), "SIDEMEN AMONG US DECODED",
               fill=TEXT_GRAY, font=footer_font, anchor="mm")

        img.save(frames_dir / f"frame_{frame_idx:04d}.png")

    output = output_dir / "comparison_bars.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(output),
    ], check=True, capture_output=True)

    import shutil
    shutil.rmtree(frames_dir, ignore_errors=True)
    return output


def render_quote_callout(
    quote: str,
    speaker: str,
    context: str,
    output_dir: Path,
    duration: float = 5.0,
    fps: int = 25,
) -> Path:
    """Render a dramatic quote callout graphic."""
    output_dir.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration * fps)

    frames_dir = output_dir / "quote_frames"
    frames_dir.mkdir(exist_ok=True)

    player_color = PLAYER_COLORS.get(speaker, ACCENT_BLUE)

    for frame_idx in range(n_frames):
        t = frame_idx / fps
        img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
        d = ImageDraw.Draw(img)

        # Dramatic quote marks
        if t > 0.3:
            quote_mark_font = _get_font(200)
            d.text((150, 200), "\u201C", fill=(50, 50, 80), font=quote_mark_font)
            d.text((WIDTH - 250, 500), "\u201D", fill=(50, 50, 80), font=quote_mark_font)

        # Quote text (centered, large)
        if t > 0.5:
            # Word wrap
            quote_font = _get_font(44, bold=True)
            words = quote.split()
            lines = []
            line = ""
            for word in words:
                test = f"{line} {word}".strip()
                bbox = d.textbbox((0, 0), test, font=quote_font)
                if bbox[2] - bbox[0] > WIDTH - 400:
                    lines.append(line)
                    line = word
                else:
                    line = test
            if line:
                lines.append(line)

            y = HEIGHT // 2 - len(lines) * 30
            for ln in lines:
                d.text((WIDTH // 2, y), ln, fill=TEXT_WHITE, font=quote_font, anchor="mm")
                y += 60

        # Speaker name + context
        if t > 1.0:
            d.line([(WIDTH // 2 - 100, HEIGHT // 2 + 100), (WIDTH // 2 + 100, HEIGHT // 2 + 100)],
                   fill=player_color, width=3)
            speaker_font = _get_font(32, bold=True)
            d.text((WIDTH // 2, HEIGHT // 2 + 130), f"— {speaker}", fill=player_color, font=speaker_font, anchor="mm")

            if context:
                ctx_font = _get_font(22)
                d.text((WIDTH // 2, HEIGHT // 2 + 170), context, fill=TEXT_GRAY, font=ctx_font, anchor="mm")

        img.save(frames_dir / f"frame_{frame_idx:04d}.png")

    output = output_dir / "quote_callout.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(output),
    ], check=True, capture_output=True)

    import shutil
    shutil.rmtree(frames_dir, ignore_errors=True)
    return output


def render_rivalry_graphic(
    player1: str,
    player2: str,
    co_mentions: int,
    stats1: dict[str, str],
    stats2: dict[str, str],
    output_dir: Path,
    duration: float = 6.0,
    fps: int = 25,
) -> Path:
    """Render a head-to-head rivalry comparison graphic."""
    output_dir.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration * fps)

    frames_dir = output_dir / "rivalry_frames"
    frames_dir.mkdir(exist_ok=True)

    c1 = PLAYER_COLORS.get(player1, ACCENT_BLUE)
    c2 = PLAYER_COLORS.get(player2, ACCENT_RED)

    for frame_idx in range(n_frames):
        t = frame_idx / fps
        img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
        d = ImageDraw.Draw(img)

        # Split screen: left = player1, right = player2
        d.rectangle([0, 0, WIDTH // 2, 80], fill=c1)
        d.rectangle([WIDTH // 2, 0, WIDTH, 80], fill=c2)

        # "VS" center
        vs_font = _get_font(64, bold=True)
        d.text((WIDTH // 2, 40), "VS", fill=TEXT_WHITE, font=vs_font, anchor="mm")

        # Player names
        name_font = _get_font(56, bold=True)
        if t > 0.3:
            d.text((WIDTH // 4, 150), player1.upper(), fill=c1, font=name_font, anchor="mm")
        if t > 0.5:
            d.text((3 * WIDTH // 4, 150), player2.upper(), fill=c2, font=name_font, anchor="mm")

        # Co-mentions
        if t > 0.8:
            co_font = _get_font(28)
            d.text((WIDTH // 2, 200), f"{co_mentions} interactions in transcripts", fill=TEXT_GRAY, font=co_font, anchor="mm")

        # Stats comparison
        stat_font = _get_font(30)
        val_font = _get_font(40, bold=True)
        y = 260

        for (k1, v1), (k2, v2) in zip(stats1.items(), stats2.items()):
            appear_time = 1.0 + y / 500
            if t < appear_time:
                y += 90
                continue

            # Label (center)
            d.text((WIDTH // 2, y + 20), k1, fill=TEXT_GRAY, font=stat_font, anchor="mm")

            # Player 1 value (left)
            d.text((WIDTH // 4, y + 60), v1, fill=c1, font=val_font, anchor="mm")

            # Player 2 value (right)
            d.text((3 * WIDTH // 4, y + 60), v2, fill=c2, font=val_font, anchor="mm")

            d.line([(100, y + 85), (WIDTH - 100, y + 85)], fill=(40, 40, 60), width=1)
            y += 90

        img.save(frames_dir / f"frame_{frame_idx:04d}.png")

    output = output_dir / "rivalry.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(output),
    ], check=True, capture_output=True)

    import shutil
    shutil.rmtree(frames_dir, ignore_errors=True)
    return output
