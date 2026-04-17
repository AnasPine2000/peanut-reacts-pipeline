"""
SadTalker integration for audio-driven lip-synced peanut animation.

Replaces the deprecated Wav2Lip path. Takes a (realistic) face image + TTS
audio and produces a talking video using the SadTalker model.

SadTalker runs in its own isolated conda env (old pinned deps fight modern
pipelines), so we always shell out. Two env vars override the defaults:

    SADTALKER_PYTHON   — path to the env's python.exe
    SADTALKER_HOME     — path to the cloned SadTalker repo

Defaults target the dev machine layout: conda env `sadtalker`, repo at
C:/Users/anasm/ai_models/SadTalker. On any other machine, set the env vars.

Face-landmark limitation: SadTalker needs a human-face-compatible source
image. The cartoon peanut PNGs in assets/peanut_face/cartoon_*.png do NOT
work (empirically confirmed 2026-04-17). Use realistic_neutral.png or a
similar human-face design.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _default_sadtalker_python() -> Path:
    # Dev-machine default — conda env `sadtalker`
    return Path(os.environ.get(
        "SADTALKER_PYTHON",
        r"C:\Users\anasm\anaconda3\envs\sadtalker\python.exe",
    ))


def _default_sadtalker_home() -> Path:
    return Path(os.environ.get(
        "SADTALKER_HOME",
        r"C:\Users\anasm\ai_models\SadTalker",
    ))


def sadtalker_available() -> bool:
    """Check if SadTalker env, repo, and weights are all present."""
    py = _default_sadtalker_python()
    home = _default_sadtalker_home()
    return (
        py.exists()
        and (home / "inference.py").exists()
        and (home / "checkpoints" / "SadTalker_V0.0.2_256.safetensors").exists()
    )


def _ensure_wav(audio_path: Path, cache_dir: Path) -> Path:
    """SadTalker wants WAV at 16kHz mono. Convert if needed, cache by hash."""
    if audio_path.suffix.lower() == ".wav":
        return audio_path

    cache_dir.mkdir(parents=True, exist_ok=True)
    wav_path = cache_dir / (audio_path.stem + "_16k.wav")
    if wav_path.exists() and wav_path.stat().st_mtime >= audio_path.stat().st_mtime:
        return wav_path

    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(audio_path.resolve()),
            "-ar", "16000", "-ac", "1", str(wav_path.resolve()),
        ],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return wav_path


def generate_sadtalker_video(
    face_image: Path,
    audio_path: Path,
    output_path: Path,
    *,
    size: int = 256,
    still: bool = True,
    preprocess: str = "full",
    enhancer: Optional[str] = None,
) -> Path:
    """Run SadTalker inference to produce a talking-head video.

    Args:
        face_image: Source image with a detectable human face.
        audio_path: Driving audio (WAV/MP3/etc — converted to 16kHz WAV if needed).
        output_path: Final MP4 destination.
        size: 256 or 512. 512 ~3x slower but sharper.
        still: If True, minimize head motion (better for stylized characters).
        preprocess: "crop" | "extcrop" | "resize" | "full" | "extfull".
                    "full" keeps the whole body/hoodie of the peanut.
        enhancer: None, "gfpgan", or "RestoreFormer". GFPGAN upscales to 1024.

    Returns:
        Path to the generated MP4.
    """
    if not sadtalker_available():
        raise RuntimeError(
            f"SadTalker not found. Checked python={_default_sadtalker_python()} "
            f"home={_default_sadtalker_home()}. "
            "Set SADTALKER_PYTHON and SADTALKER_HOME env vars or install per "
            "memory/reference_sadtalker_install.md."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert audio to 16kHz WAV if needed
    cache_dir = output_path.parent / "_sadtalker_audio_cache"
    wav_path = _ensure_wav(audio_path, cache_dir)

    # SadTalker writes into --result_dir/<timestamp>.mp4. Use a unique subdir
    # so concurrent calls don't collide, then relocate the single output.
    import tempfile
    result_dir = Path(tempfile.mkdtemp(prefix="sadtalker_", dir=output_path.parent))

    py = _default_sadtalker_python()
    home = _default_sadtalker_home()

    cmd = [
        str(py),
        str(home / "inference.py"),
        "--source_image", str(face_image.resolve()),
        "--driven_audio", str(wav_path.resolve()),
        "--result_dir", str(result_dir.resolve()),
        "--preprocess", preprocess,
        "--size", str(size),
    ]
    if still:
        cmd.append("--still")
    if enhancer:
        cmd += ["--enhancer", enhancer]

    logger.info(
        "SadTalker: %s + %s → %s (size=%d, still=%s, enhancer=%s)",
        face_image.name, audio_path.name, output_path.name,
        size, still, enhancer or "off",
    )

    result = subprocess.run(
        cmd,
        cwd=str(home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[-800:]
        # Cleanup before raising
        import shutil
        shutil.rmtree(result_dir, ignore_errors=True)
        # Common failure: stylized image has no detectable face landmarks
        if "landmark" in stderr.lower() or "detect" in stderr.lower():
            raise SadTalkerNoFaceError(
                f"SadTalker could not detect a face in {face_image.name}. "
                "Use realistic_neutral.png (or another human-face design) — "
                "cartoon PNGs lack detectable landmarks."
            )
        raise RuntimeError(f"SadTalker failed: {stderr[-400:]}")

    # SadTalker outputs <result_dir>/<timestamp>.mp4 — find it
    mp4s = sorted(result_dir.glob("*.mp4"))
    if not mp4s:
        import shutil
        shutil.rmtree(result_dir, ignore_errors=True)
        raise RuntimeError(f"SadTalker produced no MP4 in {result_dir}")

    # Move the final video and cleanup
    final_src = mp4s[-1]
    import shutil
    if output_path.exists():
        output_path.unlink()
    shutil.move(str(final_src), str(output_path))
    shutil.rmtree(result_dir, ignore_errors=True)

    logger.info("SadTalker output: %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path


def render_sadtalker_reaction(
    face_image: Path,
    audio_path: Path,
    output_path: Path,
    duration: float,
    *,
    canvas_size: int = 512,
    green_screen: bool = True,
    emotion: str = "neutral",
    size: int = 256,
    enhancer: Optional[str] = None,
    tail_padding: float = 0.3,
) -> Path:
    """Render a SadTalker-driven peanut reaction segment.

    Mirrors `wav2lip_sync.render_lipsync_reaction` so reaction_video.py can
    swap one backend for the other with minimal branching.

    Args:
        face_image: Peanut face image (must have detectable human face).
        audio_path: TTS audio for this reaction line.
        output_path: Final MP4 path.
        duration: Expected segment duration (used for the hold-frame tail).
        canvas_size: Output square canvas side in px.
        green_screen: Composite onto a green background for chroma-keying.
        emotion: Unused by SadTalker itself but kept for signature parity.
        size: SadTalker render size (256 fast, 512 sharper).
        enhancer: None | "gfpgan" | "RestoreFormer".
        tail_padding: Seconds to hold the last frame at the end.

    Returns:
        Path to the rendered segment.
    """
    # Step 1: Run SadTalker
    raw_output = output_path.with_name(output_path.stem + "_raw.mp4")
    generate_sadtalker_video(
        face_image, audio_path, raw_output,
        size=size, still=True, preprocess="full", enhancer=enhancer,
    )

    # Step 2: Scale to canvas, optional green-screen, tail-pad the final frame
    cs = canvas_size
    # SadTalker already adds subtle head motion; avoid the aggressive wav2lip
    # sway filter so we don't double-animate.
    if green_screen:
        # Scale + pad onto green square. chromakey removes the gray/black bg.
        vf = (
            f"scale={cs}:{cs}:force_original_aspect_ratio=decrease,"
            f"pad={cs}:{cs}:(ow-iw)/2:(oh-ih)/2:color=0x00FF00"
        )
    else:
        vf = f"scale={cs}:{cs}:force_original_aspect_ratio=decrease"

    # Tail-padding: hold last frame for tail_padding seconds
    if tail_padding > 0:
        vf += f",tpad=stop_mode=clone:stop_duration={tail_padding:.3f}"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_output),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-an",  # strip audio; the compositor will remux TTS later
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    raw_output.unlink(missing_ok=True)

    logger.info(
        "SadTalker reaction: %s (%.2fs, emotion=%s)",
        output_path.name, duration, emotion,
    )
    return output_path


class SadTalkerNoFaceError(RuntimeError):
    """Raised when SadTalker can't find a face in the source image.

    Catch this in the pipeline to fall back to the cartoon renderer for
    that segment instead of failing the whole job.
    """
    pass
