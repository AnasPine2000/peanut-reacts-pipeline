"""
Peanut character renderer.

Generates animated peanut character frames with blinking, talking,
bouncing, and wobbling animations. Exports as GIF or alpha-channel WebM.

Refactored from archive/charachter_creation.py.
"""

from __future__ import annotations

import math
import os
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ---------------------------------------------------------------------------
# Character drawing
# ---------------------------------------------------------------------------

def draw_peanut_character(
    t: float,
    size: int = 512,
    seed: int = 0,
    *,
    mouth_open_override: float | None = None,
    emotion: str = "neutral",
) -> Image.Image:
    """Draw a single frame of the peanut character at time *t* seconds.

    If *mouth_open_override* is provided (0.0–1.0), it drives the mouth
    instead of the default sine-wave animation. This enables speech-synced
    animation from TTS word timings.

    *emotion* affects the character's expression:
    - "neutral": default relaxed face
    - "amused" / "sarcastic": slight smirk, half-closed eyes
    - "excited": wide eyes, big smile
    - "shocked": very wide eyes, open round mouth
    - "confused": one eyebrow raised, squinted
    - "angry": furrowed brows, downturned mouth

    Returns an RGBA PIL Image.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    base = (205, 165, 105, 255)
    shadow = (170, 125, 75, 255)
    outline = (120, 80, 45, 255)
    highlight = (255, 235, 200, 110)

    # Emotion-specific modifiers — each emotion has a distinct visual signature
    eye_scale = 1.0           # eye size multiplier
    brow_show = False         # whether to draw eyebrows
    brow_left_angle = 0.0     # left eyebrow tilt (degrees)
    brow_right_angle = 0.0    # right eyebrow tilt (degrees)
    brow_offset_y = 0         # eyebrow vertical offset
    blush_boost = 0           # extra blush intensity
    extra_mouth_scale = 1.0   # mouth width/height multiplier
    mouth_style = "default"   # "default", "smirk", "frown", "grin", "O", "flat"
    pupil_size_mult = 1.0     # pupil size multiplier

    if emotion in ("excited",):
        eye_scale = 1.35
        blush_boost = 40
        extra_mouth_scale = 1.3
        mouth_style = "grin"
        brow_show = True
        brow_left_angle = -10.0   # raised brows
        brow_right_angle = -10.0
        pupil_size_mult = 0.8     # sparkly small pupils
    elif emotion in ("shocked",):
        eye_scale = 1.6
        extra_mouth_scale = 1.5
        mouth_style = "O"
        brow_show = True
        brow_left_angle = -15.0   # very raised
        brow_right_angle = -15.0
        pupil_size_mult = 0.6     # tiny shocked pupils
    elif emotion in ("amused",):
        eye_scale = 0.85
        blush_boost = 25
        extra_mouth_scale = 1.1
        mouth_style = "smirk"
        brow_show = True
        brow_left_angle = -5.0
        brow_right_angle = -5.0
    elif emotion in ("sarcastic",):
        eye_scale = 0.75          # half-lidded
        mouth_style = "smirk"
        brow_show = True
        brow_left_angle = 8.0     # one brow raised
        brow_right_angle = -8.0
    elif emotion in ("confused",):
        eye_scale = 1.1
        mouth_style = "flat"
        brow_show = True
        brow_left_angle = -12.0   # one brow up
        brow_right_angle = 6.0    # one brow down
        pupil_size_mult = 1.2
    elif emotion in ("angry",):
        eye_scale = 0.9
        mouth_style = "frown"
        brow_show = True
        brow_left_angle = 15.0    # furrowed
        brow_right_angle = 15.0
        brow_offset_y = -int(size * 0.015)

    cx, cy = size // 2, size // 2

    # Body: two overlapping ellipses
    top = (cx - int(size * 0.28), cy - int(size * 0.34), cx + int(size * 0.28), cy + int(size * 0.06))
    bot = (cx - int(size * 0.32), cy - int(size * 0.02), cx + int(size * 0.32), cy + int(size * 0.38))

    d.ellipse(top, fill=base)
    d.ellipse(bot, fill=base)

    # Shadow
    shadow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    sd.ellipse((top[0] + int(size * 0.04), top[1] + int(size * 0.03), top[2], top[3]), fill=shadow)
    sd.ellipse((bot[0] + int(size * 0.05), bot[1] + int(size * 0.03), bot[2], bot[3]), fill=shadow)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=int(size * 0.01)))
    img = Image.alpha_composite(img, shadow_layer)
    d = ImageDraw.Draw(img)

    # Outlines
    for w in range(3):
        d.ellipse((top[0] - w, top[1] - w, top[2] + w, top[3] + w), outline=outline)
        d.ellipse((bot[0] - w, bot[1] - w, bot[2] + w, bot[3] + w), outline=outline)

    # Speckles
    rng = random.Random(seed)
    speck_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sp = ImageDraw.Draw(speck_layer)
    speck_color = (150, 105, 60, 140)
    for _ in range(55):
        x = rng.randint(int(size * 0.30), int(size * 0.70))
        y = rng.randint(int(size * 0.18), int(size * 0.78))
        r = rng.randint(1, 3)
        sp.ellipse((x - r, y - r, x + r, y + r), fill=speck_color)
    speck_layer = speck_layer.filter(ImageFilter.GaussianBlur(radius=0.6))
    img = Image.alpha_composite(img, speck_layer)
    d = ImageDraw.Draw(img)

    # Highlight
    hl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.ellipse((cx - int(size * 0.22), cy - int(size * 0.30),
                cx - int(size * 0.02), cy - int(size * 0.02)), fill=highlight)
    hl = hl.filter(ImageFilter.GaussianBlur(radius=int(size * 0.02)))
    img = Image.alpha_composite(img, hl)
    d = ImageDraw.Draw(img)

    # Animation parameters
    blink_period = 4.0
    blink_phase = t % blink_period
    blink = 0.0
    if blink_phase < 0.14:
        blink = 1.0 - abs((blink_phase / 0.07) - 1.0)
        blink = _clamp(blink, 0.0, 1.0)

    look_x = 0.5 * math.sin(2 * math.pi * 0.23 * t)
    look_y = 0.35 * math.sin(2 * math.pi * 0.17 * t + 1.3)

    if mouth_open_override is not None:
        mouth_open = _lerp(0.12, 1.0, _clamp(mouth_open_override, 0.0, 1.0))
    else:
        talk = 0.5 + 0.5 * math.sin(2 * math.pi * 1.35 * t)
        talk = talk * talk
        mouth_open = _lerp(0.12, 1.0, talk)

    # Eyes (scaled by emotion)
    eye_y = cy - int(size * 0.05)
    eye_dx = int(size * 0.09)
    eye_w = int(size * 0.10 * eye_scale)
    eye_h = int(size * 0.12 * eye_scale)
    blink_factor = _lerp(1.0, 0.12, blink)
    eh = max(2, int(eye_h * blink_factor))

    def draw_eye(ex: int, ey: int, brow_tilt_deg: float = 0.0) -> None:
        # Eye white
        d.ellipse((ex - eye_w // 2, ey - eh // 2, ex + eye_w // 2, ey + eh // 2),
                   fill=(255, 255, 255, 255))
        d.ellipse((ex - eye_w // 2, ey - eh // 2, ex + eye_w // 2, ey + eh // 2),
                   outline=(70, 50, 35, 255), width=2)

        # Pupil (size varies by emotion)
        pupil_r = max(2, int(size * 0.018 * eye_scale * pupil_size_mult))
        px = ex + int(look_x * eye_w * 0.20) if blink <= 0.2 else ex
        py = ey + int(look_y * eye_h * 0.12) if blink <= 0.2 else ey

        d.ellipse((px - pupil_r, py - pupil_r, px + pupil_r, py + pupil_r),
                   fill=(20, 20, 20, 255))
        # Highlight dot
        hr = max(1, pupil_r // 3)
        d.ellipse((px - hr + 2, py - hr - 2, px + hr + 2, py + hr - 2),
                   fill=(255, 255, 255, 180))

        # Eyebrow (always drawn if brow_show is True)
        if brow_show:
            brow_y = ey - eh // 2 - int(size * 0.025) + brow_offset_y
            brow_hw = eye_w // 2 + int(size * 0.025)
            tilt_px = int(math.sin(math.radians(brow_tilt_deg)) * brow_hw * 0.5)
            brow_thickness = max(3, int(size * 0.012))
            d.line(
                [(ex - brow_hw, brow_y - tilt_px), (ex + brow_hw, brow_y + tilt_px)],
                fill=(80, 50, 30, 255), width=brow_thickness,
            )

    draw_eye(cx - eye_dx, eye_y, brow_tilt_deg=brow_left_angle)
    draw_eye(cx + eye_dx, eye_y, brow_tilt_deg=brow_right_angle)

    # Mouth — different shapes per emotion
    mouth_cx = cx
    mouth_cy = cy + int(size * 0.18)
    mw = int(size * 0.22 * extra_mouth_scale)
    mh = int(size * 0.10 * mouth_open * extra_mouth_scale)
    mouth_color = (70, 20, 25, 255)
    mouth_outline = (40, 10, 12, 255)
    line_w = max(3, int(size * 0.008))

    if mouth_open < 0.25:
        # Closed mouth — shape depends on emotion
        if mouth_style == "smirk":
            # Asymmetric upward curve (left side higher)
            points = [
                (mouth_cx - mw // 2, mouth_cy + int(size * 0.01)),
                (mouth_cx - mw // 4, mouth_cy - int(size * 0.02)),
                (mouth_cx, mouth_cy - int(size * 0.01)),
                (mouth_cx + mw // 3, mouth_cy - int(size * 0.03)),
            ]
            for i in range(len(points) - 1):
                d.line([points[i], points[i + 1]], fill=(60, 35, 25, 255), width=line_w)
        elif mouth_style == "grin":
            # Wide upward smile
            arc_box = (mouth_cx - int(mw * 0.6), mouth_cy - int(size * 0.04),
                       mouth_cx + int(mw * 0.6), mouth_cy + int(size * 0.10))
            d.arc(arc_box, start=190, end=350, fill=(60, 35, 25, 255), width=line_w + 1)
        elif mouth_style == "frown":
            # Downward curve
            arc_box = (mouth_cx - mw // 2, mouth_cy - int(size * 0.06),
                       mouth_cx + mw // 2, mouth_cy + int(size * 0.06))
            d.arc(arc_box, start=20, end=160, fill=(60, 35, 25, 255), width=line_w)
        elif mouth_style == "flat":
            # Flat line (confused/neutral)
            d.line(
                [(mouth_cx - mw // 3, mouth_cy), (mouth_cx + mw // 3, mouth_cy)],
                fill=(60, 35, 25, 255), width=line_w,
            )
        elif mouth_style == "O":
            # Small round O (surprise even when closed)
            o_r = int(size * 0.03)
            d.ellipse(
                (mouth_cx - o_r, mouth_cy - o_r, mouth_cx + o_r, mouth_cy + o_r),
                fill=mouth_color, outline=mouth_outline, width=2,
            )
        else:
            # Default: gentle smile arc
            arc_box = (mouth_cx - mw // 2, mouth_cy - int(size * 0.02),
                       mouth_cx + mw // 2, mouth_cy + int(size * 0.12))
            d.arc(arc_box, start=200, end=340, fill=(60, 35, 25, 255), width=line_w)
    else:
        # Open mouth — shape varies
        if mouth_style == "O":
            # Round open mouth (shocked)
            o_w = int(mw * 0.65)
            o_h = max(int(mh * 1.2), int(size * 0.06))
            mouth_box = (mouth_cx - o_w // 2, mouth_cy - o_h // 2,
                         mouth_cx + o_w // 2, mouth_cy + o_h // 2)
            d.ellipse(mouth_box, fill=mouth_color, outline=mouth_outline, width=3)
        elif mouth_style == "grin":
            # Wide D-shaped grin
            mouth_box = (mouth_cx - int(mw * 0.55), mouth_cy - mh // 3,
                         mouth_cx + int(mw * 0.55), mouth_cy + int(mh * 0.8))
            d.ellipse(mouth_box, fill=mouth_color, outline=mouth_outline, width=3)
            # Teeth across top
            teeth_h = max(3, int(mh * 0.30))
            teeth_box = (mouth_cx - int(mw * 0.45), mouth_cy - mh // 3 + 3,
                         mouth_cx + int(mw * 0.45), mouth_cy - mh // 3 + 3 + teeth_h)
            d.rounded_rectangle(teeth_box, radius=teeth_h // 2, fill=(245, 245, 245, 255))
        else:
            # Default open mouth with teeth and tongue
            mouth_box = (mouth_cx - mw // 2, mouth_cy - mh // 2,
                         mouth_cx + mw // 2, mouth_cy + mh // 2)
            d.ellipse(mouth_box, fill=mouth_color, outline=mouth_outline, width=3)

            teeth_h = max(3, int(mh * 0.35))
            teeth_box = (mouth_cx - int(mw * 0.42), mouth_cy - mh // 2 + 3,
                         mouth_cx + int(mw * 0.42), mouth_cy - mh // 2 + 3 + teeth_h)
            d.rounded_rectangle(teeth_box, radius=teeth_h // 2, fill=(245, 245, 245, 255))

            if mh > int(size * 0.04):
                tongue_box = (mouth_cx - int(mw * 0.28), mouth_cy + int(mh * 0.05),
                              mouth_cx + int(mw * 0.28), mouth_cy + int(mh * 0.45))
                d.ellipse(tongue_box, fill=(170, 60, 80, 200))

    # Cheek blush
    blush = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blush)
    blush_alpha = int(60 + blush_boost + 30 * math.sin(2 * math.pi * 0.5 * t + 0.8))
    blush_color = (255, 120, 140, _clamp(blush_alpha, 20, 110))
    for sign in (-1, 1):
        bx = cx + sign * int(size * 0.18)
        by = cy + int(size * 0.08)
        bd.ellipse((bx - 18, by - 12, bx + 18, by + 12), fill=blush_color)
    blush = blush.filter(ImageFilter.GaussianBlur(radius=6))
    img = Image.alpha_composite(img, blush)

    return img


# ---------------------------------------------------------------------------
# Frame rendering
# ---------------------------------------------------------------------------

def render_frames(
    out_dir: Path,
    duration: float,
    fps: int,
    canvas: int,
    char_size: int,
    seed: int,
) -> None:
    """Render *duration* seconds of peanut animation frames as PNGs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    total_frames = int(round(duration * fps))

    for i in range(total_frames):
        t = i / fps
        frame = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        char = draw_peanut_character(t, size=char_size, seed=seed)

        bounce = int(8 * math.sin(2 * math.pi * 0.7 * t))
        wobble_deg = 4.0 * math.sin(2 * math.pi * 0.35 * t)
        char_rot = char.rotate(wobble_deg, resample=Image.BICUBIC, expand=True)

        x = (canvas - char_rot.width) // 2
        y = (canvas - char_rot.height) // 2 + bounce
        frame.alpha_composite(char_rot, (x, y))
        frame.save(out_dir / f"{i:04d}.png", "PNG")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_gif(frames_dir: Path, fps: int, output_path: Path) -> Path:
    """Create an optimised GIF from rendered frames using ffmpeg."""
    palette_path = output_path.parent / "palette.png"

    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "%04d.png"),
        "-vf", "palettegen=stats_mode=diff",
        str(palette_path),
    ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "%04d.png"),
        "-i", str(palette_path),
        "-lavfi", "paletteuse=dither=bayer:bayer_scale=5",
        "-loop", "0",
        str(output_path),
    ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    palette_path.unlink(missing_ok=True)
    return output_path


def export_alpha_webm(frames_dir: Path, fps: int, output_path: Path) -> Path:
    """Create a VP9 WebM with alpha channel from rendered frames."""
    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "%04d.png"),
        "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p",
        "-b:v", "0",
        "-crf", "33",
        str(output_path),
    ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    return output_path
