"""
Eye animation post-processor for Wav2Lip output.

Adds blinking, eye movement, and emotion-based expressions to
the Wav2Lip lip-synced video, which only animates the mouth.
Uses face_alignment to locate eye positions, then overlays
animated eyes via ffmpeg drawbox/overlay filters.
"""

from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


@dataclass
class EyeRegion:
    """Detected eye region from the face image."""
    left_eye_center: tuple[int, int]
    right_eye_center: tuple[int, int]
    eye_width: int
    eye_height: int


def detect_eye_regions(face_image: Path) -> Optional[EyeRegion]:
    """Detect eye positions in the peanut face image using face_alignment."""
    try:
        import face_alignment
        import torch

        fa = face_alignment.FaceAlignment(
            face_alignment.LandmarksType.TWO_D,
            device="cuda" if torch.cuda.is_available() else "cpu",
        )

        img = np.array(Image.open(face_image).convert("RGB"))
        landmarks = fa.get_landmarks(img)

        if not landmarks or len(landmarks) == 0:
            logger.warning("No face landmarks detected in %s", face_image)
            return None

        lm = landmarks[0]  # first face

        # Eye landmarks (68-point model):
        # Left eye: points 36-41, Right eye: points 42-47
        left_eye = lm[36:42]
        right_eye = lm[42:48]

        left_center = (int(left_eye[:, 0].mean()), int(left_eye[:, 1].mean()))
        right_center = (int(right_eye[:, 0].mean()), int(right_eye[:, 1].mean()))

        # Estimate eye size from landmark spread
        eye_w = int(np.linalg.norm(left_eye[0] - left_eye[3]) * 1.4)
        eye_h = int(eye_w * 0.6)

        return EyeRegion(
            left_eye_center=left_center,
            right_eye_center=right_center,
            eye_width=eye_w,
            eye_height=eye_h,
        )
    except Exception as e:
        logger.warning("Eye detection failed: %s", e)
        return None


def generate_eye_overlay_frames(
    eye_region: EyeRegion,
    duration: float,
    fps: int = 25,
    canvas_size: int = 512,
    source_image_size: int = 1024,
    emotion: str = "neutral",
) -> list[Image.Image]:
    """Generate transparent PNG frames with animated eyes.

    The eyes blink periodically, look around, and change expression
    based on the emotion parameter.
    """
    n_frames = int(duration * fps) + 1
    frames = []

    # Scale eye region from source image coords to canvas coords
    scale = canvas_size / source_image_size
    lx = int(eye_region.left_eye_center[0] * scale)
    ly = int(eye_region.left_eye_center[1] * scale)
    rx = int(eye_region.right_eye_center[0] * scale)
    ry = int(eye_region.right_eye_center[1] * scale)
    ew = int(eye_region.eye_width * scale)
    eh = int(eye_region.eye_height * scale)

    for i in range(n_frames):
        t = i / fps

        # Create transparent frame
        frame = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        d = ImageDraw.Draw(frame)

        # Blink cycle: blink every 3-4 seconds, blink duration ~0.15s
        blink_period = 3.5
        blink_phase = t % blink_period
        is_blinking = blink_phase > (blink_period - 0.15)

        # Eye movement (subtle look-around)
        look_x = int(3 * math.sin(2 * math.pi * 0.25 * t))
        look_y = int(2 * math.sin(2 * math.pi * 0.18 * t))

        # Emotion modifiers
        eye_openness = 1.0  # 1.0 = normal, 0.5 = squint, 1.3 = wide
        brow_offset = 0

        if emotion == "shocked":
            eye_openness = 1.4
            brow_offset = -4
        elif emotion == "sarcastic":
            eye_openness = 0.6
            brow_offset = 2
        elif emotion == "excited":
            eye_openness = 1.2
            brow_offset = -2
        elif emotion == "confused":
            eye_openness = 1.1
            brow_offset = -3
        elif emotion == "amused":
            eye_openness = 0.85
            brow_offset = 0

        actual_eh = int(eh * eye_openness)

        for (cx, cy) in [(lx, ly), (rx, ry)]:
            if is_blinking:
                # Semi-transparent eyelid overlay (skin-colored bar over eye area)
                lid_h = actual_eh + 4
                d.rounded_rectangle(
                    [cx - ew // 2 - 2, cy - lid_h // 2,
                     cx + ew // 2 + 2, cy + lid_h // 2],
                    radius=ew // 4,
                    fill=(180, 150, 110, 200),  # skin-tone eyelid
                )
                # Eyelash line
                d.line(
                    [(cx - ew // 2, cy), (cx + ew // 2, cy)],
                    fill=(60, 40, 25, 220),
                    width=2,
                )
            else:
                # Subtle pupil movement overlay (don't replace the whole eye)
                # Draw a slightly transparent dark pupil that shifts with look direction
                pupil_r = max(5, ew // 5)
                px = cx + look_x
                py = cy + look_y

                # Dark pupil with slight transparency (blends with existing eye)
                d.ellipse(
                    [px - pupil_r, py - pupil_r,
                     px + pupil_r, py + pupil_r],
                    fill=(25, 18, 10, 140),
                )

                # Bright highlight (reflection)
                hl = max(2, pupil_r // 3)
                d.ellipse(
                    [px - pupil_r + 3, py - pupil_r + 2,
                     px - pupil_r + 3 + hl, py - pupil_r + 2 + hl],
                    fill=(255, 255, 255, 180),
                )

                # Upper eyelid shadow for expression (wider = higher shadow)
                lid_top = cy - actual_eh // 2
                shadow_alpha = 80 if eye_openness >= 1.0 else 140
                d.arc(
                    [cx - ew // 2, lid_top - 4, cx + ew // 2, cy + 4],
                    180, 360,
                    fill=(60, 40, 25, shadow_alpha),
                    width=2,
                )

        frames.append(frame)

    return frames


def add_eye_animation_to_video(
    input_video: Path,
    output_video: Path,
    eye_region: EyeRegion,
    duration: float,
    fps: int = 25,
    canvas_size: int = 512,
    source_image_size: int = 1024,
    emotion: str = "neutral",
) -> Path:
    """Overlay animated eyes onto a Wav2Lip output video.

    Generates eye animation frames, encodes as a transparent video,
    and composites on top of the Wav2Lip output.
    """
    work_dir = output_video.parent
    eye_frames_dir = work_dir / "eye_frames"
    eye_frames_dir.mkdir(exist_ok=True)

    # Generate eye overlay frames
    frames = generate_eye_overlay_frames(
        eye_region, duration, fps, canvas_size, source_image_size, emotion,
    )

    # Save frames as PNGs
    for i, frame in enumerate(frames):
        frame.save(eye_frames_dir / f"eye_{i:05d}.png")

    # Encode eye frames as transparent video
    eye_video = work_dir / "eye_overlay.mov"
    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(eye_frames_dir / "eye_%05d.png"),
        "-c:v", "png",  # lossless with alpha
        "-pix_fmt", "rgba",
        str(eye_video),
    ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Composite: overlay eye video on top of Wav2Lip output
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-i", str(eye_video),
        "-filter_complex",
        "[0:v][1:v]overlay=0:0:shortest=1[v]",
        "-map", "[v]",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-an",
        str(output_video),
    ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Cleanup
    import shutil
    shutil.rmtree(eye_frames_dir, ignore_errors=True)
    eye_video.unlink(missing_ok=True)

    logger.info("Added eye animation to %s", output_video.name)
    return output_video
