"""
Loudness-based video clip extraction.

Analyses audio to find the loudest segments and extracts them as clips.
Supports caching, deduplication, and parallel processing.

Refactored from archive/shouting_playlist.py.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from pydub import AudioSegment

from peanut_reacts.core.ffmpeg import concatenate, cut_segment, extract_audio


@dataclass(frozen=True)
class LoudnessJob:
    input_dir: Path
    output_dir: Path
    window_size_ms: int = 1000
    step_size_ms: int = 1000
    min_clip_duration_ms: int = 5000
    max_clip_duration_ms: int = 15000
    selection_percentage: int = 15
    dedup_shift_seconds: int = 3
    max_workers: int = 8
    video_extensions: tuple[str, ...] = (".mp4", ".webm", ".mkv", ".avi")


class LoudSegmentDetector:
    """Detect and extract the loudest segments from video files."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def detect_loud_segments(
        self,
        audio_file: Path,
        *,
        window_size_ms: int = 1000,
        step_size_ms: int = 1000,
        min_clip_ms: int = 5000,
        max_clip_ms: int = 15000,
        selection_pct: int = 15,
    ) -> list[dict]:
        """Analyse audio and return candidate loud segments with avg dBFS."""
        self._log.info("Analysing loudness in %s ...", audio_file.name)
        audio = AudioSegment.from_file(str(audio_file))
        duration_ms = len(audio)

        # Build windows
        windows = []
        num_windows = int((duration_ms - window_size_ms) / step_size_ms) + 1
        for i in range(num_windows):
            start = i * step_size_ms
            end = start + window_size_ms
            segment = audio[start:end]
            windows.append({"start": start, "end": end, "dBFS": segment.dBFS})

        # Select top N%
        num_select = max(1, int(len(windows) * selection_pct / 100.0))
        selected = sorted(windows, key=lambda w: w["dBFS"], reverse=True)[:num_select]
        selected.sort(key=lambda w: w["start"])

        # Merge contiguous windows into peaks
        merged: list[dict] = []
        group = [selected[0]]
        for w in selected[1:]:
            if w["start"] <= group[-1]["end"] + step_size_ms:
                group.append(w)
            else:
                merged.append(max(group, key=lambda x: x["dBFS"]))
                group = [w]
        if group:
            merged.append(max(group, key=lambda x: x["dBFS"]))

        # Generate clip candidates
        segments = []
        for peak in merged:
            clip_dur = random.randint(min_clip_ms, max_clip_ms)
            start_ms = peak["start"]
            if start_ms + clip_dur > duration_ms:
                start_ms = max(0, duration_ms - clip_dur)
            end_ms = start_ms + clip_dur
            seg_audio = audio[start_ms:end_ms]
            segments.append({
                "start": start_ms / 1000.0,
                "end": end_ms / 1000.0,
                "avg_dBFS": seg_audio.dBFS,
            })

        self._log.info("Detected %d candidate segment(s).", len(segments))
        return segments

    def _cache_segments(self, audio_file: Path, cache_file: Path, **kwargs) -> list[dict]:
        """Load from cache or compute and save."""
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        segments = self.detect_loud_segments(audio_file, **kwargs)
        cache_file.write_text(json.dumps(segments), encoding="utf-8")
        return segments

    @staticmethod
    def deduplicate(segments: list[dict], shift: int = 3) -> list[dict]:
        """Remove near-overlapping segments, keeping the loudest."""
        if not segments:
            return segments
        segs = sorted(segments, key=lambda s: s["start"])
        deduped = [segs[0]]
        for seg in segs[1:]:
            last = deduped[-1]
            if seg["start"] < last["end"] + shift:
                if seg["avg_dBFS"] > last["avg_dBFS"]:
                    deduped[-1] = seg
            else:
                deduped.append(seg)
        return deduped

    def process_video(self, video_path: Path, job: LoudnessJob) -> list[Path]:
        """Process a single video: extract audio, detect loud segments, cut clips."""
        base = video_path.stem
        audio_path = job.output_dir / f"{base}.wav"
        cache_path = job.output_dir / f"{base}_segments.json"

        extract_audio(video_path, audio_path)

        segments = self._cache_segments(
            audio_path, cache_path,
            window_size_ms=job.window_size_ms,
            step_size_ms=job.step_size_ms,
            min_clip_ms=job.min_clip_duration_ms,
            max_clip_ms=job.max_clip_duration_ms,
            selection_pct=job.selection_percentage,
        )
        segments = self.deduplicate(segments, shift=job.dedup_shift_seconds)

        clips: list[Path] = []
        for idx, seg in enumerate(segments, 1):
            clip_path = job.output_dir / f"{base}_loud_clip_{idx:03d}.mp4"
            if not clip_path.exists():
                cut_segment(video_path, seg["start"], seg["end"], clip_path, codec="h264_nvenc")
            clips.append(clip_path)

        return clips

    def run(self, job: LoudnessJob) -> Optional[Path]:
        """Process all videos in *job.input_dir* in parallel and concatenate."""
        job.output_dir.mkdir(parents=True, exist_ok=True)

        video_files = [
            job.input_dir / f
            for f in sorted(os.listdir(job.input_dir))
            if f.lower().endswith(job.video_extensions)
        ]

        if not video_files:
            self._log.error("No video files found in %s", job.input_dir)
            return None

        all_clips: list[Path] = []
        with ThreadPoolExecutor(max_workers=job.max_workers) as executor:
            futures = {
                executor.submit(self.process_video, v, job): v
                for v in video_files
            }
            for future in as_completed(futures):
                video = futures[future]
                try:
                    clips = future.result()
                    all_clips.extend(clips)
                    self._log.info("%s: %d clip(s)", video.name, len(clips))
                except Exception as exc:
                    self._log.error("Error processing %s: %s", video.name, exc)

        if not all_clips:
            self._log.warning("No loud clips detected.")
            return None

        output = job.output_dir / "final_loud_compilation.mp4"
        concatenate(all_clips, output, shuffle=True)
        return output
