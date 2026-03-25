"""
Cartoon peanut expression renderer with dynamic animation.

Creates lively, animated character segments by combining:
- Expression image swapping (6 emotions)
- Breathing zoom pulse (scale oscillation)
- Head sway + bobbing + tilt
- Mouth flap simulation (rapid zoom on face during speech)
- Emotion-specific motion profiles (shake when excited, slow when sarcastic)
- Squash & stretch for bouncy cartoon feel
"""

from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).resolve().parents[2].parent / "assets" / "peanut_face"

_EMOTION_MAP = {
    "neutral": "cartoon_v1.png",
    "amused": "cartoon_amused.png",
    "sarcastic": "cartoon_sarcastic.png",
    "excited": "cartoon_excited.png",
    "confused": "cartoon_confused.png",
    "shocked": "cartoon_shocked.png",
}

# Emotion-specific animation profiles
_EMOTION_PROFILES = {
    "neutral": {
        "sway_speed": 1.0, "bob_speed": 1.0, "sway_amp": 1.0, "bob_amp": 1.0,
        "tilt_amp": 1.0, "breathe_amp": 1.0, "squash": 0.0,
    },
    "excited": {
        "sway_speed": 2.0, "bob_speed": 2.5, "sway_amp": 1.8, "bob_amp": 2.0,
        "tilt_amp": 1.5, "breathe_amp": 1.5, "squash": 0.03,
    },
    "shocked": {
        "sway_speed": 0.5, "bob_speed": 0.8, "sway_amp": 0.5, "bob_amp": 0.3,
        "tilt_amp": 0.3, "breathe_amp": 2.0, "squash": 0.02,  # big breathe = gasp
    },
    "sarcastic": {
        "sway_speed": 0.6, "bob_speed": 0.5, "sway_amp": 0.7, "bob_amp": 0.4,
        "tilt_amp": 2.0, "breathe_amp": 0.5, "squash": 0.0,  # slow, tilted
    },
    "amused": {
        "sway_speed": 1.5, "bob_speed": 2.0, "sway_amp": 1.3, "bob_amp": 1.8,
        "tilt_amp": 1.2, "breathe_amp": 1.5, "squash": 0.02,  # bouncy laugh
    },
    "confused": {
        "sway_speed": 0.8, "bob_speed": 0.6, "sway_amp": 0.5, "bob_amp": 0.5,
        "tilt_amp": 2.5, "breathe_amp": 0.8, "squash": 0.0,  # head tilt
    },
}


@dataclass
class CartoonRendererConfig:
    assets_dir: Path = field(default_factory=lambda: _ASSETS_DIR)
    canvas_size: int = 512
    fps: int = 25
    # Base animation parameters (modified by emotion profiles)
    base_sway_amp: int = 12       # pixels left-right
    base_bob_amp: int = 8         # pixels up-down
    base_sway_period: float = 2.5
    base_bob_period: float = 1.8
    base_tilt_rad: float = 0.035  # max rotation
    base_tilt_period: float = 3.0
    breathe_period: float = 2.2   # breathing cycle
    breathe_scale: float = 0.025  # max scale change (2.5%)
    mouth_flap_hz: float = 8.0   # mouth open/close frequency during speech


def get_expression_image(emotion: str, assets_dir: Path = _ASSETS_DIR) -> Path:
    filename = _EMOTION_MAP.get(emotion, _EMOTION_MAP["neutral"])
    path = assets_dir / filename
    if not path.exists():
        path = assets_dir / _EMOTION_MAP["neutral"]
    if not path.exists():
        raise FileNotFoundError(f"No expression images in {assets_dir}")
    return path


def _render_frames(
    face_image: Path,
    duration: float,
    config: CartoonRendererConfig,
    emotion: str = "neutral",
    speaking: bool = False,
) -> list[Image.Image]:
    """Render animated frames with dynamic motion.

    Each frame applies:
    - Position offset (sway + bob)
    - Rotation (tilt)
    - Scale pulse (breathing + squash/stretch)
    - Mouth flap (zoom on lower face during speech)
    """
    src = Image.open(face_image).convert("RGBA")
    cs = config.canvas_size
    fps = config.fps
    n_frames = int(duration * fps) + 1

    profile = _EMOTION_PROFILES.get(emotion, _EMOTION_PROFILES["neutral"])

    # Speech makes everything more energetic
    energy = 1.5 if speaking else 1.0

    sway_amp = config.base_sway_amp * profile["sway_amp"] * energy
    bob_amp = config.base_bob_amp * profile["bob_amp"] * energy
    sway_period = config.base_sway_period / profile["sway_speed"]
    bob_period = config.base_bob_period / profile["bob_speed"]
    tilt_rad = config.base_tilt_rad * profile["tilt_amp"] * energy
    tilt_period = config.base_tilt_period / max(0.3, profile["sway_speed"])
    breathe_amp = config.breathe_scale * profile["breathe_amp"]
    squash_amp = profile["squash"] * energy

    frames = []

    for i in range(n_frames):
        t = i / fps

        # Create green background canvas
        frame = Image.new("RGBA", (cs, cs), (0, 255, 0, 255))

        # ── Scale: breathing + squash/stretch ──
        breathe = 1.0 + breathe_amp * math.sin(2 * math.pi * t / config.breathe_period)
        squash_x = 1.0 + squash_amp * math.sin(2 * math.pi * t / (bob_period * 0.5))
        squash_y = 1.0 - squash_amp * math.sin(2 * math.pi * t / (bob_period * 0.5))

        # Mouth flap: during speech, add a rapid scale pulse on the vertical axis
        if speaking:
            flap = 0.015 * abs(math.sin(2 * math.pi * config.mouth_flap_hz * t))
            squash_y += flap
            squash_x -= flap * 0.5

        scale_x = breathe * squash_x
        scale_y = breathe * squash_y

        new_w = int(cs * scale_x)
        new_h = int(cs * scale_y)
        # Ensure even dimensions
        new_w = new_w if new_w % 2 == 0 else new_w + 1
        new_h = new_h if new_h % 2 == 0 else new_h + 1

        scaled = src.resize((new_w, new_h), Image.LANCZOS)

        # ── Rotation (tilt) ──
        angle_deg = math.degrees(tilt_rad * math.sin(2 * math.pi * t / tilt_period))

        # For confused: add a persistent tilt bias
        if emotion == "confused":
            angle_deg += 5.0  # always slightly tilted

        rotated = scaled.rotate(angle_deg, resample=Image.BICUBIC, expand=False,
                                fillcolor=(0, 255, 0, 255))

        # ── Position offset (sway + bob) ──
        ox = int(sway_amp * math.sin(2 * math.pi * t / sway_period))
        oy = int(bob_amp * math.sin(2 * math.pi * t / bob_period))

        # Center the character on the canvas with offset
        paste_x = (cs - rotated.width) // 2 + ox
        paste_y = (cs - rotated.height) // 2 + oy

        frame.paste(rotated, (paste_x, paste_y), rotated)
        frames.append(frame.convert("RGB"))

    return frames


def render_cartoon_segment(
    emotion: str,
    duration: float,
    output_path: Path,
    *,
    config: Optional[CartoonRendererConfig] = None,
    speaking: bool = True,
) -> Path:
    """Render an animated cartoon peanut segment with dynamic motion.

    The character breathes, sways, bobs, tilts, and mouth-flaps based
    on the emotion profile and whether it's speaking.
    """
    cfg = config or CartoonRendererConfig()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    face_image = get_expression_image(emotion, cfg.assets_dir)

    # Render frames
    frames = _render_frames(face_image, duration, cfg, emotion, speaking)

    # Save frames to temp dir
    frames_dir = output_path.parent / f"_frames_{output_path.stem}"
    frames_dir.mkdir(exist_ok=True)

    for i, frame in enumerate(frames):
        frame.save(frames_dir / f"frame_{i:05d}.png")

    # Encode to video
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(cfg.fps),
        "-i", str(frames_dir / "frame_%05d.png"),
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Cleanup frames
    import shutil
    shutil.rmtree(frames_dir, ignore_errors=True)

    logger.info("Cartoon segment: %s (%s, %.2fs, speaking=%s)",
                output_path.name, emotion, duration, speaking)
    return output_path


def render_idle_loop(
    output_path: Path,
    duration: float = 4.0,
    *,
    config: Optional[CartoonRendererConfig] = None,
) -> Path:
    """Render an idle loop with gentle neutral animation."""
    return render_cartoon_segment(
        "neutral", duration, output_path,
        config=config, speaking=False,
    )
