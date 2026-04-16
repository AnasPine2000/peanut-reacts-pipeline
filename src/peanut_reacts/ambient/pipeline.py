"""Ambient pipeline orchestrator.

Full flow: source tracks -> compile mix -> generate background -> composite -> upload.

Reuses existing pipeline patterns from scheduler/lofi.py but with the full
ambient-specific feature set: themed visuals, AI image generation, long-form
mixes, optional Spotify distribution.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = PROJECT_ROOT / "production" / "ambient"


@dataclass
class AmbientJob:
    theme: str
    target_hours: float = 8
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    use_ai_visuals: bool = True


def _nvenc_available() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                          capture_output=True, text=True, timeout=10)
        return "h264_nvenc" in r.stdout
    except Exception:
        return False


def composite_video(
    background_loop: Path,
    audio_mix: Path,
    output_path: Path,
    target_duration_seconds: float,
) -> Optional[Path]:
    """Combine background loop + audio into final video."""
    if not background_loop.exists() or not audio_mix.exists():
        log.error("Missing inputs: bg=%s, audio=%s", background_loop, audio_mix)
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoder = "h264_nvenc" if _nvenc_available() else "libx264"
    preset = "fast" if encoder == "h264_nvenc" else "veryfast"

    try:
        # Loop the background video to match the audio duration
        subprocess.run([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(background_loop),
            "-i", str(audio_mix),
            "-c:v", encoder, "-preset", preset,
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-shortest",
            "-movflags", "+faststart",
            "-t", str(target_duration_seconds),
            str(output_path),
        ], check=True, capture_output=True, timeout=3600)
        log.info("Composited final video: %s", output_path)
        return output_path
    except subprocess.CalledProcessError as e:
        log.error("Composite failed: %s", e.stderr.decode("utf-8", errors="replace")[-400:])
        return None


def run_ambient_pipeline(
    job: AmbientJob,
    output_dir: Path = DEFAULT_OUTPUT,
    upload: bool = False,
    youtube_service=None,
    privacy: str = "private",
    fal_api_key: str = None,
) -> dict:
    """Full ambient pipeline:
    1. Scan track library for the theme
    2. Compile long-form crossfaded mix
    3. Generate background visual
    4. Composite final video
    5. Optionally upload to YouTube

    Returns: dict with paths and upload result.
    """
    from .music_sources import scan_track_library, filter_by_theme
    from .mix_compiler import compile_mix_batched
    from .background_gen import generate_background

    result = {
        "theme": job.theme,
        "started_at": datetime.utcnow().isoformat(),
        "tracks_dir": None,
        "mix_path": None,
        "background_path": None,
        "final_path": None,
        "youtube_url": None,
        "errors": [],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    tracks_root = output_dir / "tracks"
    backgrounds_dir = output_dir / "backgrounds"
    mixes_dir = output_dir / "mixes"
    final_dir = output_dir / "final"

    log.info("=" * 60)
    log.info("AMBIENT PIPELINE: %s (%s hours)", job.theme, job.target_hours)
    log.info("=" * 60)

    # ── Step 1: Source tracks ─────────────────────────────────────
    all_tracks = scan_track_library(tracks_root)
    if not all_tracks:
        result["errors"].append(
            f"No tracks found in {tracks_root}. Add CC0/Suno/PD tracks first."
        )
        return result

    themed_tracks = filter_by_theme(all_tracks, job.theme)
    if len(themed_tracks) < 2:
        log.warning("Only %d tracks for theme '%s', using full library", len(themed_tracks), job.theme)
        themed_tracks = all_tracks

    log.info("Step 1: Found %d tracks", len(themed_tracks))
    result["tracks_count"] = len(themed_tracks)

    # ── Step 2: Compile mix ───────────────────────────────────────
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    mix_path = mixes_dir / f"{job.theme}_{timestamp}.mp3"
    log.info("Step 2: Compiling %.1f-hour mix...", job.target_hours)
    compiled = compile_mix_batched(
        tracks=themed_tracks,
        output_path=mix_path,
        target_duration_hours=job.target_hours,
        crossfade_seconds=5,
    )
    if not compiled:
        result["errors"].append("Mix compilation failed")
        return result
    result["mix_path"] = str(compiled)

    # ── Step 3: Generate background ───────────────────────────────
    log.info("Step 3: Generating background visual...")
    bg_path = generate_background(
        theme=job.theme,
        output_dir=backgrounds_dir,
        duration_seconds=600,  # 10-minute base loop, will be looped infinitely
        use_ai=job.use_ai_visuals,
        fal_api_key=fal_api_key,
    )
    if not bg_path:
        result["errors"].append("Background generation failed")
        return result
    result["background_path"] = str(bg_path)

    # ── Step 4: Composite final video ─────────────────────────────
    log.info("Step 4: Compositing final video...")
    target_seconds = job.target_hours * 3600
    final_path = final_dir / f"{job.theme}_{timestamp}_final.mp4"
    final = composite_video(
        background_loop=bg_path,
        audio_mix=mix_path,
        output_path=final_path,
        target_duration_seconds=target_seconds,
    )
    if not final:
        result["errors"].append("Composite failed")
        return result
    result["final_path"] = str(final)

    log.info("Step 5: Final video ready: %s", final)

    # ── Step 5: Upload (optional) ─────────────────────────────────
    if upload and youtube_service:
        try:
            from peanut_reacts.upload.youtube_upload import YouTubeUploader, UploadMetadata
            uploader = YouTubeUploader(youtube_service)

            title = job.title or _generate_title(job)
            description = job.description or _generate_description(job)
            tags = job.tags or _generate_tags(job)

            meta = UploadMetadata(
                title=title[:100],
                description=description,
                tags=tags[:15],
                category_id="10",  # Music
                privacy_status=privacy,
            )
            up_result = uploader.upload_video(final, meta)
            result["youtube_url"] = up_result.url
            result["video_id"] = up_result.video_id
            log.info("Uploaded: %s", up_result.url)
        except Exception as e:
            log.error("Upload failed: %s", e)
            result["errors"].append(f"Upload: {e}")

    result["finished_at"] = datetime.utcnow().isoformat()
    return result


def _generate_title(job: AmbientJob) -> str:
    """Create a title following ambient channel conventions."""
    theme_label = job.theme.replace("_", " ").title()
    hours = int(job.target_hours)
    return f"{hours} HOURS of {theme_label} | Cozy Ambient for Sleep & Study"


def _generate_description(job: AmbientJob) -> str:
    theme_label = job.theme.replace("_", " ")
    return (
        f"Relax, study, or sleep to {int(job.target_hours)} hours of {theme_label} ambience. "
        f"Perfect for focus, meditation, or peaceful sleep.\n\n"
        f"All music is original / royalty-free. No copyright claims.\n\n"
        f"#ambient #sleep #study #relaxing #cozy #lofi #{job.theme}"
    )


def _generate_tags(job: AmbientJob) -> list[str]:
    base = ["ambient", "sleep music", "study music", "relaxing", "cozy",
            "8 hours", "no ads", "lofi", "background music"]
    return base + [job.theme.replace("_", " ")]
