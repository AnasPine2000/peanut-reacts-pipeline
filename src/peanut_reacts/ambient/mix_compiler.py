"""Long-form ambient mix compiler.

Takes a list of Track objects and produces a single 8+ hour crossfaded mix.
Uses FFmpeg's acrossfade filter with batched concatenation to avoid the
Windows command line length limit.
"""
from __future__ import annotations

import logging
import random
import subprocess
import time
from pathlib import Path
from typing import Optional

from .music_sources import Track

log = logging.getLogger(__name__)


def normalize_audio(input_path: Path, output_path: Path, target_lufs: float = -16) -> bool:
    """Normalize a track to target loudness using FFmpeg loudnorm."""
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(input_path),
            "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
            "-ar", "44100", "-ac", "2", "-b:a", "192k",
            str(output_path),
        ], check=True, capture_output=True, timeout=120)
        return True
    except Exception as e:
        log.warning("Normalization failed for %s: %s", input_path.name, e)
        return False


def crossfade_two(track_a: Path, track_b: Path, output: Path, fade_seconds: float = 5) -> bool:
    """Crossfade two tracks via FFmpeg acrossfade filter."""
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(track_a), "-i", str(track_b),
            "-filter_complex",
            f"[0:a][1:a]acrossfade=d={fade_seconds}:c1=tri:c2=tri[out]",
            "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k",
            str(output),
        ], check=True, capture_output=True, timeout=300)
        return True
    except Exception as e:
        log.error("Crossfade failed: %s", e)
        return False


def compile_mix_batched(
    tracks: list[Track],
    output_path: Path,
    target_duration_hours: float = 8,
    crossfade_seconds: float = 5,
    shuffle: bool = True,
    batch_size: int = 8,
) -> Optional[Path]:
    """Compile a long crossfaded mix from a list of tracks.

    Strategy: progressively crossfade in batches to handle hundreds of files
    without exceeding command line limits.
    """
    if len(tracks) < 2:
        log.error("Need at least 2 tracks for a mix (got %d)", len(tracks))
        return None

    target_seconds = target_duration_hours * 3600
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Shuffle and repeat tracks to fill target duration
    pool = list(tracks)
    if shuffle:
        random.shuffle(pool)

    sequence: list[Track] = []
    accumulated = 0
    while accumulated < target_seconds:
        for t in pool:
            if accumulated >= target_seconds:
                break
            sequence.append(t)
            accumulated += max(t.duration_seconds - crossfade_seconds, 60)
        random.shuffle(pool) if shuffle else None

    log.info("Mix sequence: %d tracks, ~%.1f hours target", len(sequence), accumulated / 3600)

    # Use simple concat + crossfade approach
    return _build_mix_with_concat(sequence, output_path, crossfade_seconds)


def _build_mix_with_concat(
    sequence: list[Track],
    output_path: Path,
    crossfade_seconds: float = 5,
) -> Optional[Path]:
    """Build the mix by progressively concatenating with crossfades.

    For long mixes (50+ tracks), this works in batches because FFmpeg's
    filter_complex graph has practical limits.
    """
    work_dir = output_path.parent / "_mix_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: crossfade chunks of 8 tracks each into intermediate files
    chunk_size = 8
    intermediate_files = []

    for chunk_idx in range(0, len(sequence), chunk_size):
        chunk = sequence[chunk_idx:chunk_idx + chunk_size]
        chunk_out = work_dir / f"chunk_{chunk_idx:04d}.mp3"

        if chunk_out.exists():
            intermediate_files.append(chunk_out)
            continue

        if not _crossfade_chunk(chunk, chunk_out, crossfade_seconds):
            log.warning("Failed chunk %d, skipping", chunk_idx)
            continue
        intermediate_files.append(chunk_out)

    if not intermediate_files:
        log.error("No chunks built")
        return None

    log.info("Built %d intermediate chunks", len(intermediate_files))

    # Step 2: simple concat all intermediate chunks (no crossfade between chunks
    # because they're long enough to not need it)
    concat_list = work_dir / "final_concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in intermediate_files),
        encoding="utf-8",
    )

    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(output_path),
        ], check=True, capture_output=True, timeout=1800)
        log.info("Mix compiled: %s", output_path)

        # Cleanup intermediate files
        for f in intermediate_files:
            f.unlink(missing_ok=True)
        concat_list.unlink(missing_ok=True)
        try:
            work_dir.rmdir()
        except Exception:
            pass

        return output_path
    except Exception as e:
        log.error("Final concat failed: %s", e)
        return None


def _crossfade_chunk(tracks: list[Track], output: Path, fade: float = 5) -> bool:
    """Crossfade a small chunk of tracks (3-10) into one file."""
    if len(tracks) == 1:
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(tracks[0].path),
                 "-c:a", "libmp3lame", "-b:a", "192k", str(output)],
                check=True, capture_output=True, timeout=120,
            )
            return True
        except Exception:
            return False

    # Build filter_complex chain for acrossfade
    inputs = []
    for t in tracks:
        inputs.extend(["-i", str(t.path)])

    # Chain: [0][1]acrossfade=d=N[a01];[a01][2]acrossfade=d=N[a02];...
    filter_parts = []
    last_label = "0:a"
    for i in range(1, len(tracks)):
        out_label = f"a{i:02d}"
        filter_parts.append(
            f"[{last_label}][{i}:a]acrossfade=d={fade}:c1=tri:c2=tri[{out_label}]"
        )
        last_label = out_label

    filter_str = ";".join(filter_parts)

    try:
        subprocess.run(
            ["ffmpeg", "-y"] + inputs + [
                "-filter_complex", filter_str,
                "-map", f"[{last_label}]",
                "-c:a", "libmp3lame", "-b:a", "192k",
                str(output),
            ],
            check=True, capture_output=True, timeout=900,
        )
        return True
    except subprocess.CalledProcessError as e:
        log.error("Chunk crossfade failed: %s", e.stderr.decode("utf-8", errors="replace")[-300:])
        return False
    except Exception as e:
        log.error("Chunk crossfade error: %s", e)
        return False
