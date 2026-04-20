"""Flat-Peanut renderer — clean green-screen cartoon clips for PIP overlay.

Replaces the v0 AI-generated "environmental" Peanut clips (computer + chair +
room around him, ugly for chromakey) with procedurally-animated clips built
from the flat_peanut/ PNG library.

What this gives us vs. v0:
- Pure 0x00FF00 green-screen background → clean chromakey with no environment
- Real mouth-flap animation using the 3 mouth-shape variants per emotion
  (closed/open/wide), not just fake vertical scaling of a single frame
- Consistent cartoon style across all 9 clips (flat vector look, not
  stylistically-mixed AI renders)
- Zero new AI spend — procedural from existing art assets

The flat_peanut set ships 6 emotions × 3 mouth shapes = 18 PNGs:
  neutral / amused / sarcastic / excited / confused / shocked
  × closed / open / wide

Our pipeline needs 9 distinct clip files. Mapping:
  idle        → neutral (no flap)
  laughing    → amused (strong flap)
  excited     → excited (strong flap)
  celebrating → excited (alt variant)
  shocked     → shocked (small flap)
  scared      → shocked (slow motion, small flap)
  confused    → confused (tiny flap)
  facepalm    → sarcastic (tiny flap)
  sarcastic   → sarcastic (no flap)
"""
from __future__ import annotations

import logging
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FLAT_PEANUT_DIR = PROJECT_ROOT / "assets" / "flat_peanut"

GREEN_BG = (0, 255, 0, 255)

# Source-emotion families in flat_peanut/
_FLAT_EMOTIONS = {"neutral", "amused", "sarcastic", "excited", "confused", "shocked"}

# Each "clip name" we ship in ai_peanut_clips_v2 maps to:
#   (source_flat_emotion, mouth_flap_rate_hz, emotion_profile_key, speed_mult)
# mouth_flap_rate_hz=0 disables lip-sync and only uses the closed-mouth PNG.
CLIP_RECIPES = {
    "peanut_idle":        ("neutral",   0.0, "neutral",   1.0),
    "peanut_laughing":    ("amused",    7.5, "amused",    1.3),
    "peanut_excited":     ("excited",   6.5, "excited",   1.2),
    "peanut_celebrating": ("excited",   5.5, "excited",   1.1),
    "peanut_shocked":     ("shocked",   2.0, "shocked",   1.0),
    "peanut_scared":      ("shocked",   1.5, "shocked",   0.7),
    "peanut_confused":    ("confused",  2.5, "confused",  0.9),
    "peanut_facepalm":    ("sarcastic", 1.5, "sarcastic", 0.8),
    "peanut_sarcastic":   ("sarcastic", 0.0, "sarcastic", 0.9),
}

# Motion profiles per emotion family. These control the body animation
# (sway, bob, tilt, breathing) — mouth-flap is a separate channel driven
# by mouth_flap_rate_hz.
_EMOTION_PROFILES = {
    "neutral":   {"sway_amp": 8,  "bob_amp": 5,  "sway_period": 2.5, "bob_period": 2.0, "tilt_amp": 0.02, "tilt_period": 3.0, "breathe_amp": 0.02, "persistent_tilt": 0.0},
    "amused":    {"sway_amp": 14, "bob_amp": 12, "sway_period": 1.4, "bob_period": 0.9, "tilt_amp": 0.05, "tilt_period": 1.6, "breathe_amp": 0.04, "persistent_tilt": 0.0},
    "excited":   {"sway_amp": 18, "bob_amp": 14, "sway_period": 1.0, "bob_period": 0.7, "tilt_amp": 0.06, "tilt_period": 1.2, "breathe_amp": 0.05, "persistent_tilt": 0.0},
    "shocked":   {"sway_amp": 3,  "bob_amp": 2,  "sway_period": 4.0, "bob_period": 3.0, "tilt_amp": 0.01, "tilt_period": 5.0, "breathe_amp": 0.08, "persistent_tilt": 0.0},
    "sarcastic": {"sway_amp": 5,  "bob_amp": 3,  "sway_period": 3.5, "bob_period": 2.8, "tilt_amp": 0.08, "tilt_period": 4.0, "breathe_amp": 0.015, "persistent_tilt": 4.0},
    "confused":  {"sway_amp": 4,  "bob_amp": 3,  "sway_period": 3.0, "bob_period": 2.2, "tilt_amp": 0.10, "tilt_period": 2.5, "breathe_amp": 0.02, "persistent_tilt": 6.0},
}


@dataclass
class FlatPeanutConfig:
    canvas_size: int = 512
    fps: int = 25


def _load_mouth_shapes(emotion: str, assets_dir: Path = FLAT_PEANUT_DIR) -> dict[str, Image.Image]:
    """Return {'closed': img, 'open': img, 'wide': img} for an emotion."""
    if emotion not in _FLAT_EMOTIONS:
        raise ValueError(f"Unknown flat_peanut emotion: {emotion!r}. Known: {_FLAT_EMOTIONS}")

    shapes = {}
    for shape in ("closed", "open", "wide"):
        p = assets_dir / f"{emotion}_{shape}.png"
        if not p.exists():
            raise FileNotFoundError(f"Missing flat_peanut asset: {p}")
        shapes[shape] = Image.open(p).convert("RGBA")
    return shapes


def _pick_mouth_shape(t: float, flap_hz: float) -> str:
    """Cycle through mouth shapes at flap_hz. Returns closed|open|wide.

    When flap_hz > 0, we cycle closed -> open -> wide -> open -> closed
    which reads more like natural mouth motion than a binary flap.
    """
    if flap_hz <= 0:
        return "closed"

    # Phase in [0, 1) within each flap cycle
    phase = (t * flap_hz) % 1.0
    if phase < 0.25:
        return "closed"
    elif phase < 0.45:
        return "open"
    elif phase < 0.65:
        return "wide"
    elif phase < 0.85:
        return "open"
    else:
        return "closed"


def render_flat_peanut_clip(
    clip_name: str,
    duration: float,
    output_path: Path,
    config: Optional[FlatPeanutConfig] = None,
    assets_dir: Path = FLAT_PEANUT_DIR,
) -> Path:
    """Render a clean green-screen Peanut reaction clip.

    Args:
        clip_name: One of CLIP_RECIPES keys (peanut_idle, peanut_laughing,
                   peanut_shocked, etc.)
        duration: Clip length in seconds. We render a loop this long; the
                  downstream facecam timeline can stream_loop it if needed.
        output_path: Where to write the MP4.

    Returns the output path (also available as clip_name with .mp4 suffix).
    """
    cfg = config or FlatPeanutConfig()
    if clip_name not in CLIP_RECIPES:
        raise ValueError(f"Unknown clip_name {clip_name!r}. Known: {list(CLIP_RECIPES)}")

    emotion, flap_hz, profile_key, speed_mult = CLIP_RECIPES[clip_name]
    profile = _EMOTION_PROFILES[profile_key]
    shapes = _load_mouth_shapes(emotion, assets_dir)

    cs = cfg.canvas_size
    fps = cfg.fps
    n_frames = int(duration * fps) + 1

    # Apply speed multiplier to all motion periods (faster emotions tick faster)
    sway_period = profile["sway_period"] / speed_mult
    bob_period = profile["bob_period"] / speed_mult
    tilt_period = profile["tilt_period"] / speed_mult

    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = output_path.parent / f"_frames_{output_path.stem}"
    frames_dir.mkdir(exist_ok=True)

    try:
        for i in range(n_frames):
            t = i / fps

            canvas = Image.new("RGBA", (cs, cs), GREEN_BG)

            # ── Pick mouth shape this frame (if flap enabled) ──
            shape_key = _pick_mouth_shape(t, flap_hz)
            src = shapes[shape_key]

            # ── Scale: breathe pulse ──
            breathe = 1.0 + profile["breathe_amp"] * math.sin(2 * math.pi * t / 2.2)
            new_w = int(cs * breathe)
            new_h = int(cs * breathe)
            if new_w % 2: new_w += 1
            if new_h % 2: new_h += 1
            scaled = src.resize((new_w, new_h), Image.LANCZOS)

            # ── Rotation (tilt) ──
            angle_deg = math.degrees(profile["tilt_amp"] * math.sin(2 * math.pi * t / tilt_period))
            angle_deg += profile["persistent_tilt"]  # confused/sarcastic persistent head tilt
            rotated = scaled.rotate(angle_deg, resample=Image.BICUBIC, expand=False,
                                    fillcolor=GREEN_BG)

            # ── Position offset (sway + bob) ──
            ox = int(profile["sway_amp"] * math.sin(2 * math.pi * t / sway_period))
            oy = int(profile["bob_amp"] * math.sin(2 * math.pi * t / bob_period))

            paste_x = (cs - rotated.width) // 2 + ox
            paste_y = (cs - rotated.height) // 2 + oy

            canvas.paste(rotated, (paste_x, paste_y), rotated)
            canvas.convert("RGB").save(frames_dir / f"frame_{i:05d}.png")

        # Encode with libx264 (yuv420p for widest compat)
        subprocess.run([
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", str(frames_dir / "frame_%05d.png"),
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ], check=True, capture_output=True, timeout=120)
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)

    log.info("Flat-Peanut clip: %s (%s, %.1fs, flap=%.1fHz, speed=%.1fx)",
             output_path.name, emotion, duration, flap_hz, speed_mult)
    return output_path


def regenerate_all_peanut_clips(
    output_dir: Path,
    duration: float = 4.0,
    config: Optional[FlatPeanutConfig] = None,
) -> dict[str, Path]:
    """Render every clip in CLIP_RECIPES into output_dir.

    Returns a map of {clip_name: output_path}. Useful as a one-shot to
    refresh data/ai_peanut_clips_v2/.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    for clip_name in CLIP_RECIPES:
        out = output_dir / f"{clip_name}.mp4"
        render_flat_peanut_clip(clip_name, duration, out, config=config)
        results[clip_name] = out
    return results
