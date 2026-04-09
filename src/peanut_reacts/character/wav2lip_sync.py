"""
Wav2Lip integration for realistic lip-synced peanut animation.

Takes a static face image + TTS audio and produces a video with
realistic mouth movements using the Wav2Lip GAN model.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Path to the Wav2Lip repo (cloned as lib/wav2lip)
_WAV2LIP_DIR = Path(__file__).resolve().parents[2].parent / "lib" / "wav2lip"
_CHECKPOINT = _WAV2LIP_DIR / "checkpoints" / "wav2lip_gan.pth"


def wav2lip_available() -> bool:
    """Check if Wav2Lip model and inference script are available."""
    return (
        (_WAV2LIP_DIR / "inference.py").exists()
        and _CHECKPOINT.exists()
    )


def generate_lipsync_video(
    face_image: Path,
    audio_path: Path,
    output_path: Path,
    *,
    resize_factor: int = 1,
    pad_top: int = 0,
    pad_bottom: int = 10,
    pad_left: int = 0,
    pad_right: int = 0,
    nosmooth: bool = True,
) -> Path:
    """Run Wav2Lip inference to generate a lip-synced video.

    Args:
        face_image: Path to the face image (or short video) to animate.
        audio_path: Path to the audio file driving the mouth.
        output_path: Where to save the result video.
        resize_factor: Downscale factor for faster processing (1 = full res).
        pad_*: Padding around detected face region.
        nosmooth: Disable temporal smoothing (better for static images).

    Returns:
        Path to the generated video.
    """
    if not wav2lip_available():
        raise RuntimeError(
            f"Wav2Lip not found at {_WAV2LIP_DIR}. "
            "Clone https://github.com/Rudrabha/Wav2Lip into lib/wav2lip "
            "and download wav2lip_gan.pth to checkpoints/."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Wav2Lip runs from its own directory — use absolute paths
    cmd = [
        sys.executable,
        str(_WAV2LIP_DIR / "inference.py"),
        "--checkpoint_path", str(_CHECKPOINT.resolve()),
        "--face", str(face_image.resolve()),
        "--audio", str(audio_path.resolve()),
        "--outfile", str(output_path.resolve()),
        "--resize_factor", str(resize_factor),
        "--pads", str(pad_top), str(pad_bottom), str(pad_left), str(pad_right),
    ]

    if nosmooth:
        cmd.append("--nosmooth")

    logger.info("Running Wav2Lip: %s + %s → %s", face_image.name, audio_path.name, output_path.name)

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(_WAV2LIP_DIR),  # run from wav2lip dir for imports
    )

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[-500:]
        logger.error("Wav2Lip failed:\n%s", stderr)
        raise RuntimeError(f"Wav2Lip failed: {stderr[-200:]}")

    if not output_path.exists():
        # Wav2Lip may save to temp/result.avi or results/ — find it
        import shutil
        candidates = [
            _WAV2LIP_DIR / "temp" / "result.avi",
            _WAV2LIP_DIR / "results" / "result_voice.mp4",
        ]
        found = None
        for c in candidates:
            if c.exists():
                found = c
                break

        if found:
            # Convert AVI to MP4 if needed
            if found.suffix == ".avi":
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(found),
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                    str(output_path),
                ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                found.unlink(missing_ok=True)
            else:
                shutil.move(str(found), str(output_path))
        else:
            raise RuntimeError("Wav2Lip produced no output file.")

    logger.info("Wav2Lip output: %s", output_path)
    return output_path


def render_lipsync_reaction(
    face_image: Path,
    audio_path: Path,
    output_path: Path,
    duration: float,
    *,
    canvas_size: int = 512,
    green_screen: bool = True,
    emotion: str = "neutral",
    eye_region=None,
    source_image_size: int = 1024,
) -> Path:
    """Render a lip-synced peanut reaction segment.

    Runs Wav2Lip on the face image with TTS audio, then optionally
    composites onto green screen for chroma-keying.

    Args:
        face_image: Peanut face image (ideally on green background).
        audio_path: TTS audio for this reaction line.
        output_path: Output video path.
        duration: Expected duration in seconds.
        canvas_size: Output canvas size (square).
        green_screen: Whether to add green screen background.

    Returns:
        Path to the rendered video segment.
    """
    # Step 1: Run Wav2Lip
    raw_output = output_path.with_name(output_path.stem + "_raw.mp4")
    generate_lipsync_video(face_image, audio_path, raw_output)

    if not green_screen:
        if raw_output != output_path:
            import shutil
            shutil.move(str(raw_output), str(output_path))
        return output_path

    # Step 2: Scale to canvas, add green screen, animate with head sway + tilt
    cs = canvas_size
    pad = cs + 68
    vf = (
        f"scale={pad}:{pad}:force_original_aspect_ratio=decrease,"
        f"pad={pad}:{pad}:(ow-iw)/2:(oh-ih)/2:color=0x00FF00,"
        # Head sway (more energetic during speech)
        f"crop=w={cs}:h={cs}"
        f":x='{(pad-cs)//2}+20*sin(2*PI*t/1.5)'"
        f":y='{(pad-cs)//2}+14*sin(2*PI*t/1.2)',"
        # Head tilt (slightly faster during speech)
        f"rotate='0.04*sin(2*PI*t/2)':fillcolor=0x00FF00"
        f":ow={cs}:oh={cs}"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_output),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-an",  # no audio in the video segment
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Cleanup raw
    raw_output.unlink(missing_ok=True)

    # Step 3: Add eye animation overlay (blinking + expression)
    if eye_region is not None:
        from peanut_reacts.character.eye_animator import add_eye_animation_to_video

        eye_output = output_path.with_name(output_path.stem + "_eyes.mp4")
        add_eye_animation_to_video(
            output_path, eye_output, eye_region,
            duration=duration, fps=25, canvas_size=canvas_size,
            source_image_size=source_image_size,
            emotion=emotion,
        )
        # Replace original with eye-animated version
        output_path.unlink(missing_ok=True)
        eye_output.rename(output_path)
        logger.info("Added eye animation (%s) to %s", emotion, output_path.name)

    logger.info("Lip-synced segment: %s (%.2fs)", output_path.name, duration)
    return output_path
