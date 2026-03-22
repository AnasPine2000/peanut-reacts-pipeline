"""
Keyword-based video clip extraction.

Searches video transcripts (SRT or Whisper-generated) for keyword matches
and cuts matching clips. Supports exact, fuzzy, and combined search modes.

Refactored from archive/new_opt.py with all duplicated logic moved to core/.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from peanut_reacts.core.ffmpeg import concatenate, cut_segment, get_video_duration
from peanut_reacts.core.path_sanitizer import DefaultPathSanitizer
from peanut_reacts.core.srt import parse_srt
from peanut_reacts.core.text_match import find_keyword_matches
from peanut_reacts.core.transcription import transcribe_video


@dataclass(frozen=True)
class KeywordSearchJob:
    keyword: str
    input_dir: Path
    output_dir: Path
    search_mode: str = "both"           # exact | fuzzy | both
    fuzzy_threshold: float = 0.6
    clip_min_seconds: int = 20
    clip_max_seconds: int = 30
    overlap_skip_probability: float = 0.6
    model_name: str = "base"
    device: str = "cuda"
    video_extensions: tuple[str, ...] = (".mp4", ".webm", ".mkv", ".avi")


class KeywordClipExtractor:
    """Extract clips from videos based on keyword matches in transcripts."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger
        self._sanitizer = DefaultPathSanitizer()

    def _load_transcript(self, video_path: Path, job: KeywordSearchJob) -> list[dict]:
        """Load transcript from SRT, cached JSON, or generate via Whisper."""
        base = video_path.with_suffix("")

        # Try SRT first
        srt_path = base.with_suffix(".en.srt")
        if srt_path.exists():
            self._log.info("Loading transcript from SRT: %s", srt_path.name)
            return parse_srt(srt_path)

        # Try cached JSON
        json_path = base.parent / (base.name + "_full_transcript.json")
        if json_path.exists():
            import json
            self._log.info("Loading cached transcript: %s", json_path.name)
            return json.loads(json_path.read_text(encoding="utf-8"))

        # Fall back to Whisper
        return transcribe_video(
            video_path, model_name=job.model_name, device=job.device,
        )

    def _compute_clips(
        self,
        matches: list[dict],
        video_duration: float,
        job: KeywordSearchJob,
    ) -> list[tuple[float, float]]:
        """Compute non-overlapping clip intervals centred on match midpoints."""
        candidates: list[tuple[float, float]] = []

        for match in matches:
            mid = (match["start"] + match["end"]) / 2
            length = random.randint(job.clip_min_seconds, job.clip_max_seconds)
            half = length / 2
            start = max(0, mid - half)
            end = min(video_duration, start + length)
            if end - start < length:
                start = max(0, end - length)
            candidates.append((start, end))

        candidates.sort(key=lambda c: c[0])

        # Filter overlapping clips
        accepted: list[tuple[float, float]] = []
        last_end = -1.0
        for start, end in candidates:
            if start >= last_end:
                accepted.append((start, end))
                last_end = end
            elif random.random() >= job.overlap_skip_probability:
                accepted.append((start, end))
                last_end = end

        return accepted

    def process_video(
        self,
        video_path: Path,
        job: KeywordSearchJob,
    ) -> list[Path]:
        """Process a single video: load transcript, find matches, cut clips."""
        self._log.info("Processing: %s", video_path.name)
        duration = get_video_duration(video_path)
        transcript = self._load_transcript(video_path, job)

        if not transcript:
            self._log.warning("No transcript available for %s", video_path.name)
            return []

        matches = find_keyword_matches(
            transcript, job.keyword,
            mode=job.search_mode, threshold=job.fuzzy_threshold,
        )
        if not matches:
            return []

        clips = self._compute_clips(matches, duration, job)
        safe_kw = "".join(c for c in job.keyword if c.isalnum() or c.isspace()).strip().replace(" ", "_")
        video_base = self._sanitizer.sanitize(video_path.stem)

        clip_files: list[Path] = []
        for idx, (start, end) in enumerate(clips, 1):
            clip_path = job.output_dir / f"{video_base}_{safe_kw}_clip_{idx}{video_path.suffix}"
            try:
                cut_segment(video_path, start, end, clip_path)
                clip_files.append(clip_path)
            except Exception as exc:
                self._log.error("Failed to cut clip %d: %s", idx, exc)

        return clip_files

    def run(self, job: KeywordSearchJob) -> Optional[Path]:
        """Process all videos in *job.input_dir* and concatenate results."""
        job.output_dir.mkdir(parents=True, exist_ok=True)

        video_files = [
            job.input_dir / f
            for f in sorted(os.listdir(job.input_dir))
            if f.lower().endswith(job.video_extensions)
        ]

        if not video_files:
            self._log.error("No video files found in %s", job.input_dir)
            return None

        self._log.info("Found %d video(s) in %s", len(video_files), job.input_dir)

        all_clips: list[Path] = []
        for video in video_files:
            clips = self.process_video(video, job)
            all_clips.extend(clips)
            self._log.info("%s: %d clip(s)", video.name, len(clips))

        self._log.info("Total clips: %d", len(all_clips))

        if not all_clips:
            self._log.warning("No clips generated.")
            return None

        safe_kw = "".join(c for c in job.keyword if c.isalnum() or c.isspace()).strip().replace(" ", "_")
        combined = job.output_dir / f"combined_{safe_kw}_clips{all_clips[0].suffix}"
        concatenate(all_clips, combined, shuffle=True)
        return combined
