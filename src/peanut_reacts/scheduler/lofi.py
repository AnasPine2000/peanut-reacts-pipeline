"""Lofi music pipeline.

Generates AI lofi tracks, compiles mixes, uploads to YouTube,
and manages a 24/7 livestream.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def generate_background_image(output_path: Path, style: str = "lofi") -> Path:
    """Generate a simple background for lofi videos/streams."""
    if output_path.exists():
        return output_path

    # Generate a dark gradient background with text
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=#1a1a2e:s=1920x1080:d=1",
        "-vf", (
            "drawtext=text='Lofi Vibes':fontcolor=white:fontsize=72:"
            "x=(w-text_w)/2:y=(h-text_h)/2-60:font=Arial,"
            "drawtext=text='chill beats to relax/study to':fontcolor=#888888:fontsize=36:"
            "x=(w-text_w)/2:y=(h-text_h)/2+40:font=Arial"
        ),
        "-frames:v", "1",
        str(output_path),
    ], capture_output=True, timeout=30)

    log.info("Generated background: %s", output_path)
    return output_path


def compile_lofi_mix(
    tracks_dir: Path,
    output_path: Path,
    background: Path,
    max_tracks: int = 15,
    crossfade_seconds: int = 3,
) -> bool:
    """Compile lofi tracks into a long mix video with background image."""
    tracks = sorted(tracks_dir.glob("*.mp3")) + sorted(tracks_dir.glob("*.wav")) + sorted(tracks_dir.glob("*.flac"))

    if len(tracks) < 2:
        log.error("Not enough tracks in %s (%d found, need 2+)", tracks_dir, len(tracks))
        return False

    tracks = tracks[:max_tracks]
    log.info("Compiling mix from %d tracks", len(tracks))

    # Create concat file
    concat_file = output_path.parent / "mix_concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{t}'" for t in tracks),
        encoding="utf-8",
    )

    # Concat audio
    concat_audio = output_path.parent / "mix_audio.mp3"
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(concat_audio),
        ], check=True, capture_output=True, timeout=300)
    except Exception as e:
        log.error("Audio concatenation failed: %s", e)
        return False

    # Combine with background image
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(background),
            "-i", str(concat_audio),
            "-c:v", "libx264", "-preset", "veryfast",
            "-tune", "stillimage",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path),
        ], check=True, capture_output=True, timeout=600)
        log.info("Mix compiled: %s", output_path.name)
        return True
    except Exception as e:
        log.error("Video compilation failed: %s", e)
        return False


def start_lofi_livestream(
    tracks_dir: Path,
    background: Path,
    rtmp_url: str,
    stream_key: str,
) -> subprocess.Popen:
    """Start a 24/7 lofi livestream via FFmpeg RTMP."""
    tracks = sorted(tracks_dir.glob("*.mp3")) + sorted(tracks_dir.glob("*.wav"))
    if not tracks:
        log.error("No tracks found in %s", tracks_dir)
        return None

    concat_file = tracks_dir / "stream_concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{t}'" for t in tracks),
        encoding="utf-8",
    )

    cmd = [
        "ffmpeg",
        "-re",
        "-stream_loop", "-1",
        "-loop", "1", "-i", str(background),
        "-stream_loop", "-1",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c:v", "libx264", "-preset", "ultrafast",
        "-maxrate", "2500k", "-bufsize", "5000k",
        "-pix_fmt", "yuv420p",
        "-g", "50",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-shortest",
        "-f", "flv",
        f"{rtmp_url}/{stream_key}",
    ]

    log.info("Starting lofi livestream with %d tracks", len(tracks))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log.info("Lofi stream PID: %d", proc.pid)
    return proc


def run_lofi_pipeline(
    output_dir: Path,
    tracks_dir: Path = None,
    upload_service=None,
    upload_privacy: str = "public",
    tags: list[str] = None,
) -> tuple[int, int]:
    """Full lofi pipeline: compile mix -> upload.

    Returns (mixes_uploaded, errors).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if tracks_dir is None:
        tracks_dir = output_dir / "tracks"

    tracks_dir.mkdir(parents=True, exist_ok=True)

    # Check for tracks
    tracks = list(tracks_dir.glob("*.mp3")) + list(tracks_dir.glob("*.wav"))
    if len(tracks) < 2:
        log.warning("Need at least 2 tracks in %s. Found %d.", tracks_dir, len(tracks))
        log.info("Add .mp3 or .wav files to %s to get started.", tracks_dir)
        return 0, 0

    # Generate background
    bg = output_dir / "lofi_background.png"
    generate_background_image(bg)

    # Compile mix
    date_str = time.strftime("%Y%m%d")
    mix_path = output_dir / f"lofi_mix_{date_str}.mp4"

    if not mix_path.exists():
        success = compile_lofi_mix(tracks_dir, mix_path, bg)
        if not success:
            return 0, 1

    # Upload
    if upload_service and mix_path.exists():
        try:
            from peanut_reacts.upload.youtube_upload import UploadMetadata, YouTubeUploader
            uploader = YouTubeUploader(upload_service)
            meta = UploadMetadata(
                title=f"Lofi Hip Hop Radio - Chill Beats to Relax/Study to [{date_str}]",
                description="Chill lofi beats for relaxing, studying, and working. New mix every week.\n\n#lofi #chillbeats #study #relax #ambient",
                tags=tags or ["lofi", "chill", "study", "relax", "beats"],
                category_id="10",  # Music
                privacy_status=upload_privacy,
            )
            result = uploader.upload_video(mix_path, meta)
            log.info("Uploaded lofi mix: %s", result.url)
            return 1, 0
        except Exception as e:
            log.error("Upload failed: %s", e)
            return 0, 1

    log.info("Mix saved locally: %s", mix_path)
    return 1, 0
