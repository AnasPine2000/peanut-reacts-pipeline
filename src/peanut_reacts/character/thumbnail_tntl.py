"""TNTL thumbnail generator.

Builds a 1280x720 YouTube thumbnail in the KSI+ TNTL style:
  - Left half: Peanut face close-up (chroma-keyed off green) on a
    white-blue radial gradient background
  - Right half: rounded-corner rect with a thick sky-blue border
    containing the clip frame, with a yellow punchline caption
    burned in at the bottom ("NO GAPS", "I'M CRACKED", etc.)

Text uses bold sans-serif with drop shadow. No episode number,
no channel watermark, no logo — matches KSI+ grammar exactly.

The one design deviation from KSI: we use Peanut's laughing_preview
frame on the left (not a face-cam crop), because the animated
character IS the reactor and that frame is Peanut's most expressive
"cracked shell, big laugh" pose.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CLIPS_DIR = PROJECT_ROOT / "data" / "ai_peanut_clips"

THUMB_W = 1280
THUMB_H = 720

# Color palette (matches KSI+ thumbnail grammar)
COLOR_BG_TOP = (90, 160, 230)      # sky blue
COLOR_BG_BOT = (240, 250, 255)     # near white
COLOR_BORDER = (105, 195, 255)     # thick sky-blue border
COLOR_CAPTION_BG = (20, 20, 20)    # black caption pill
COLOR_CAPTION_TEXT = (255, 215, 0) # yellow-in-quotes
COLOR_SHADOW = (0, 0, 0, 180)


def _radial_gradient(size: tuple[int, int], inner, outer) -> Image.Image:
    """Build a radial gradient (inner color at center, outer at edges)."""
    w, h = size
    cx, cy = w / 2, h / 2
    max_r = (cx**2 + cy**2) ** 0.5

    img = Image.new("RGB", (w, h), outer)
    px = img.load()
    for y in range(h):
        for x in range(w):
            dx, dy = x - cx, y - cy
            t = min(1.0, (dx * dx + dy * dy) ** 0.5 / max_r)
            r = int(inner[0] * (1 - t) + outer[0] * t)
            g = int(inner[1] * (1 - t) + outer[1] * t)
            b = int(inner[2] * (1 - t) + outer[2] * t)
            px[x, y] = (r, g, b)
    return img


def _find_font(size: int) -> ImageFont.ImageFont:
    """Find a bold sans-serif font. Fall back to PIL default if nothing works."""
    candidates = [
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _rounded_rect(draw: ImageDraw.ImageDraw, box, radius: int, fill=None, outline=None, width=1):
    """PIL doesn't ship a nicely-antialiased rounded rect pre-9.2, so use
    draw.rounded_rectangle which is available in modern Pillow. This shim
    just forwards — kept separate for readability."""
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _paste_with_chromakey(
    canvas: Image.Image,
    source: Image.Image,
    box: tuple[int, int, int, int],
    key_color=(0, 255, 0),
    tolerance: int = 80,
) -> None:
    """Paste source onto canvas inside box, making green pixels transparent.

    Simple per-pixel distance check — good enough for our Peanut clips which
    have clean green screens. Not a production chromakey (no edge smoothing).
    """
    x1, y1, x2, y2 = box
    target_w, target_h = x2 - x1, y2 - y1

    # Scale source to fit box, preserving aspect ratio
    src_w, src_h = source.size
    scale = min(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    src = source.resize((new_w, new_h), Image.LANCZOS).convert("RGBA")

    # Build alpha channel by thresholding against the key color
    r, g, b, a = src.split()
    rp, gp, bp = r.load(), g.load(), b.load()
    ap = a.load()
    kr, kg, kb = key_color
    tol2 = tolerance * tolerance
    for y in range(new_h):
        for x in range(new_w):
            dr, dg, db = rp[x, y] - kr, gp[x, y] - kg, bp[x, y] - kb
            if dr * dr + dg * dg + db * db < tol2:
                ap[x, y] = 0
    src.putalpha(a)

    # Center inside box
    ox = x1 + (target_w - new_w) // 2
    oy = y1 + (target_h - new_h) // 2
    canvas.paste(src, (ox, oy), src)


def _extract_clip_frame(clip_path: Path, out_path: Path, t: Optional[float] = None) -> Optional[Path]:
    """Extract a single frame from a video using ffmpeg.

    If t is None, grabs the middle of the clip (most likely to contain the
    visual punchline). Writes a PNG for lossless quality.
    """
    if not clip_path.exists():
        return None

    # Probe duration to find the midpoint
    if t is None:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(clip_path)],
            capture_output=True, text=True, timeout=10,
        )
        try:
            dur = float(r.stdout.strip())
            t = max(0.5, dur / 2.0)
        except ValueError:
            t = 1.0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(clip_path),
            "-frames:v", "1", "-q:v", "2", str(out_path),
        ], check=True, capture_output=True, timeout=30)
        return out_path if out_path.exists() else None
    except subprocess.CalledProcessError as e:
        log.warning("Frame extract failed: %s", e.stderr.decode("utf-8", errors="replace")[-200:])
        return None


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font,
    fill=(255, 255, 255),
    shadow_offset: tuple[int, int] = (3, 3),
    shadow_fill=(0, 0, 0),
    anchor: str = "mm",
) -> None:
    """Draw text with a simple drop shadow."""
    x, y = xy
    draw.text((x + shadow_offset[0], y + shadow_offset[1]), text, font=font,
              fill=shadow_fill, anchor=anchor)
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def generate_tntl_thumbnail(
    peanut_face_path: Path,
    clip_frame_path: Path,
    caption: str,
    output_path: Path,
    size: tuple[int, int] = (THUMB_W, THUMB_H),
) -> Optional[Path]:
    """Compose a KSI+-style TNTL thumbnail.

    Args:
        peanut_face_path: PNG frame of Peanut (e.g. laughing_preview.png).
                          Green background, will be chroma-keyed out.
        clip_frame_path: PNG frame from one of the episode's clips (the
                         "punchline" visual).
        caption: Short all-caps reaction quote, e.g. "NO GAPS" or "I'M CRACKED".
                 Rendered in yellow-on-black pill at bottom of clip rect.
        output_path: Where to write the final JPG.
        size: Thumbnail size in px. Defaults to YouTube's 1280x720.
    """
    w, h = size
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Background: radial gradient (sky blue → near white) ──
    canvas = _radial_gradient((w, h), COLOR_BG_BOT, COLOR_BG_TOP)

    # ── Left half: Peanut face with chromakey ──
    # Reserve left 50% minus a small inner margin
    left_box = (20, 20, w // 2 - 20, h - 20)
    if peanut_face_path.exists():
        try:
            peanut = Image.open(peanut_face_path).convert("RGB")
            _paste_with_chromakey(canvas, peanut, left_box, key_color=(0, 255, 0), tolerance=90)
        except Exception as e:
            log.warning("Peanut face paste failed: %s", e)

    # ── Right half: blue-rounded-rect frame containing clip frame ──
    right_x1 = w // 2 + 20
    right_y1 = 60
    right_x2 = w - 40
    right_y2 = h - 60
    border_radius = 36
    border_width = 10

    # Draw a thick sky-blue rounded-rect border FIRST (slightly larger box),
    # then the inner dark frame, then paste the clip image into the inner box.
    draw = ImageDraw.Draw(canvas)
    _rounded_rect(
        draw,
        (right_x1 - border_width, right_y1 - border_width,
         right_x2 + border_width, right_y2 + border_width),
        radius=border_radius + border_width,
        fill=COLOR_BORDER,
    )
    _rounded_rect(
        draw,
        (right_x1, right_y1, right_x2, right_y2),
        radius=border_radius,
        fill=(15, 15, 25),
    )

    # Paste clip frame (cropped to fit, covered within the rounded rect)
    if clip_frame_path.exists():
        try:
            frame = Image.open(clip_frame_path).convert("RGB")
            fw, fh = frame.size
            target_w = right_x2 - right_x1
            target_h = right_y2 - right_y1
            # cover-fit (fill box, crop overflow)
            scale = max(target_w / fw, target_h / fh)
            new_w, new_h = int(fw * scale), int(fh * scale)
            frame = frame.resize((new_w, new_h), Image.LANCZOS)
            # center crop
            crop_x = (new_w - target_w) // 2
            crop_y = (new_h - target_h) // 2
            frame = frame.crop((crop_x, crop_y, crop_x + target_w, crop_y + target_h))

            # Mask for the rounded rect
            mask = Image.new("L", (target_w, target_h), 0)
            mdraw = ImageDraw.Draw(mask)
            mdraw.rounded_rectangle(
                (0, 0, target_w - 1, target_h - 1),
                radius=border_radius,
                fill=255,
            )
            canvas.paste(frame, (right_x1, right_y1), mask)
        except Exception as e:
            log.warning("Clip frame paste failed: %s", e)

    # ── Yellow-in-quotes caption pill at bottom of right rect ──
    if caption:
        caption_text = f'"{caption}"'
        font_size = max(28, min(64, int((right_x2 - right_x1) / max(10, len(caption_text)) * 1.6)))
        font = _find_font(font_size)

        # Measure caption
        bbox = font.getbbox(caption_text)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        pill_pad_x = 28
        pill_pad_y = 14
        pill_w = text_w + pill_pad_x * 2
        pill_h = text_h + pill_pad_y * 2

        # Position pill at bottom of right rect, centered
        pill_cx = (right_x1 + right_x2) // 2
        pill_y_center = right_y2 - pill_h // 2 - 24
        pill_x1 = pill_cx - pill_w // 2
        pill_y1 = pill_y_center - pill_h // 2
        pill_x2 = pill_cx + pill_w // 2
        pill_y2 = pill_y_center + pill_h // 2

        # Draw pill (semi-transparent black via overlay + alpha_composite)
        pill_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pdraw = ImageDraw.Draw(pill_overlay)
        pdraw.rounded_rectangle(
            (pill_x1, pill_y1, pill_x2, pill_y2),
            radius=pill_h // 2,
            fill=COLOR_CAPTION_BG + (230,),
        )
        canvas = Image.alpha_composite(canvas.convert("RGBA"), pill_overlay).convert("RGB")

        # Redraw text on top of composited canvas
        draw = ImageDraw.Draw(canvas)
        _draw_text_with_shadow(
            draw,
            (pill_cx, pill_y_center),
            caption_text,
            font=font,
            fill=COLOR_CAPTION_TEXT,
            shadow_offset=(2, 2),
            anchor="mm",
        )

    # Save as JPG (YouTube thumbnails must be under 2MB, JPG is safer than PNG)
    canvas.convert("RGB").save(output_path, format="JPEG", quality=92, optimize=True)
    log.info("[TNTL] Thumbnail written: %s (%dx%d)", output_path.name, *size)
    return output_path if output_path.exists() else None


def generate_tntl_thumbnail_for_episode(
    episode_clips: list[Path],
    output_path: Path,
    caption: str = "I'M CRACKED",
    peanut_face_path: Optional[Path] = None,
    clips_dir: Path = DEFAULT_CLIPS_DIR,
) -> Optional[Path]:
    """High-level: pick best clip frame + Peanut face, render thumbnail.

    Convenience wrapper for pipeline callers. Extracts a middle frame from
    the FIRST clip (usually strong content — the hook picks the last-fail
    clip so first-clip is free for the thumbnail).
    """
    if not episode_clips:
        log.error("[TNTL] No clips provided for thumbnail")
        return None

    if peanut_face_path is None:
        # Default to laughing preview — most expressive for TNTL thumb
        peanut_face_path = clips_dir / "laughing_preview.png"

    # Extract a frame from the first clip as the punchline visual
    work = output_path.parent / "_thumb_work"
    work.mkdir(parents=True, exist_ok=True)
    clip_frame = work / f"clip_frame_{output_path.stem}.png"
    extracted = _extract_clip_frame(episode_clips[0], clip_frame)
    if not extracted:
        # Try a couple more clips before giving up
        for c in episode_clips[1:3]:
            extracted = _extract_clip_frame(c, clip_frame)
            if extracted:
                break

    if not extracted:
        log.error("[TNTL] Could not extract any clip frame for thumbnail")
        return None

    result = generate_tntl_thumbnail(
        peanut_face_path=peanut_face_path,
        clip_frame_path=clip_frame,
        caption=caption,
        output_path=output_path,
    )

    # Cleanup
    try:
        clip_frame.unlink(missing_ok=True)
        work.rmdir()
    except Exception:
        pass

    return result
