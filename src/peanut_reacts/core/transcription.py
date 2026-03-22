"""
Whisper-based audio/video transcription.

Consolidates transcription logic previously duplicated across 5+ scripts.
Supports GPU acceleration, chunked transcription for long videos, and caching.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from peanut_reacts.core.ffmpeg import get_video_duration, split_video

logger = logging.getLogger(__name__)


def _load_model(model_name: str = "base", device: str = "cuda") -> Any:
    """Load a Whisper model (lazy import to avoid import-time GPU init)."""
    import whisper

    logger.info("Loading Whisper model '%s' on %s ...", model_name, device)
    return whisper.load_model(model_name, device=device)


def extract_transcript(
    video_path: Path,
    *,
    model: Any = None,
    model_name: str = "base",
    device: str = "cuda",
) -> list[dict]:
    """Transcribe a single video/audio file using Whisper.

    If *model* is provided it is reused (avoids reloading per chunk).
    Returns a list of segment dicts with keys ``"start"``, ``"end"``, ``"text"``.
    """
    if model is None:
        model = _load_model(model_name, device)

    logger.info("Transcribing %s ...", video_path.name)
    result = model.transcribe(str(video_path))
    segments = result.get("segments", [])
    if not segments:
        logger.warning("No transcript segments found for %s", video_path.name)
    return segments


def transcribe_chunk(
    chunk_path: Path,
    offset: float,
    chunk_index: int,
    *,
    model: Any = None,
    model_name: str = "base",
    device: str = "cuda",
) -> tuple[list[dict], Path]:
    """Transcribe one chunk and adjust time-codes by *offset* seconds.

    Saves the per-chunk transcript to a JSON sidecar file.
    Returns ``(adjusted_segments, transcript_path)``.
    """
    segments = extract_transcript(
        chunk_path, model=model, model_name=model_name, device=device,
    )

    for seg in segments:
        seg["start"] += offset
        seg["end"] += offset

    transcript_path = chunk_path.with_suffix(".transcript.json")
    transcript_path.write_text(json.dumps(segments, indent=2), encoding="utf-8")
    logger.info("Saved transcript for chunk %d to %s", chunk_index + 1, transcript_path.name)

    return segments, transcript_path


def transcribe_video(
    video_path: Path,
    *,
    chunk_duration: int = 900,
    model_name: str = "base",
    device: str = "cuda",
    cache: bool = True,
) -> list[dict]:
    """High-level: transcribe a video, splitting into chunks if needed.

    If *cache* is True and a full transcript JSON already exists, it is loaded
    instead of re-transcribing.

    Returns the full list of transcript segment dicts.
    """
    full_transcript_path = video_path.with_name(video_path.stem + "_full_transcript.json")

    # Cache hit
    if cache and full_transcript_path.exists():
        logger.info("Loading cached transcript from %s", full_transcript_path.name)
        return json.loads(full_transcript_path.read_text(encoding="utf-8"))

    duration = get_video_duration(video_path)
    logger.info("Video duration: %.2f hours", duration / 3600)

    # Split if longer than chunk_duration threshold (default: > 1 chunk)
    if duration > chunk_duration:
        chunk_files = split_video(video_path, chunk_duration=chunk_duration)
    else:
        chunk_files = [video_path]

    # Load model once and reuse for all chunks
    model = _load_model(model_name, device)

    full_transcript: list[dict] = []
    intermediate_files: list[Path] = []

    for idx, chunk in enumerate(chunk_files):
        offset = idx * chunk_duration
        segments, transcript_file = transcribe_chunk(
            chunk, offset, idx, model=model,
        )
        full_transcript.extend(segments)
        if chunk != video_path:
            intermediate_files.append(chunk)
        intermediate_files.append(transcript_file)

    # Save full transcript
    full_transcript_path.write_text(
        json.dumps(full_transcript, indent=2), encoding="utf-8",
    )
    logger.info("Saved full transcript to %s", full_transcript_path.name)

    # Clean up chunk files (not chunk transcripts — they're small)
    for f in intermediate_files:
        if f.suffix != ".json":
            try:
                f.unlink()
                logger.debug("Deleted chunk file: %s", f.name)
            except OSError:
                pass

    return full_transcript
