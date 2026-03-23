"""
Speaker diarization — identify who speaks when.

Uses pyannote.audio for speaker segmentation, then aligns results
with transcript segments to produce color-coded speaker labels.

Requires a HuggingFace token for pyannote pretrained models.
Set HF_TOKEN env var or pass directly.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Color palette for speakers (max 8 speakers)
SPEAKER_COLORS = [
    {"name": "Speaker 1", "hex": "#FF4444", "ffmpeg": "0xFF4444"},  # Red
    {"name": "Speaker 2", "hex": "#44AAFF", "ffmpeg": "0x44AAFF"},  # Blue
    {"name": "Speaker 3", "hex": "#44FF44", "ffmpeg": "0x44FF44"},  # Green
    {"name": "Speaker 4", "hex": "#FFAA44", "ffmpeg": "0xFFAA44"},  # Orange
    {"name": "Speaker 5", "hex": "#FF44FF", "ffmpeg": "0xFF44FF"},  # Pink
    {"name": "Speaker 6", "hex": "#44FFFF", "ffmpeg": "0x44FFFF"},  # Cyan
    {"name": "Speaker 7", "hex": "#FFFF44", "ffmpeg": "0xFFFF44"},  # Yellow
    {"name": "Speaker 8", "hex": "#AAAAFF", "ffmpeg": "0xAAAAFF"},  # Lavender
]


@dataclass
class SpeakerSegment:
    """A time range where a specific speaker is talking."""
    start: float
    end: float
    speaker: str          # e.g. "SPEAKER_00"
    speaker_index: int    # 0-based index for color mapping
    text: str = ""
    color_hex: str = ""
    color_ffmpeg: str = ""


@dataclass
class DiarizationResult:
    """Full diarization output for a video."""
    segments: list[SpeakerSegment] = field(default_factory=list)
    num_speakers: int = 0
    speaker_map: dict[str, int] = field(default_factory=dict)  # speaker_id -> index


def _extract_audio_wav(video_path: Path, output_path: Path) -> Path:
    """Extract audio as 16kHz mono WAV for diarization."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ar", "16000", "-ac", "1", "-f", "wav",
        str(output_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return output_path


def diarize_video(
    video_path: Path,
    *,
    hf_token: str = "",
    num_speakers: Optional[int] = None,
    min_speakers: int = 2,
    max_speakers: int = 8,
    work_dir: Optional[Path] = None,
) -> DiarizationResult:
    """Run speaker diarization on a video file.

    Args:
        video_path: Path to video file
        hf_token: HuggingFace token (or set HF_TOKEN env var)
        num_speakers: Exact number of speakers (if known)
        min_speakers: Minimum speakers to detect
        max_speakers: Maximum speakers to detect
        work_dir: Directory for temp audio file

    Returns:
        DiarizationResult with speaker-labeled time segments
    """
    token = hf_token or os.environ.get("HF_TOKEN", "")
    if not token:
        logger.warning(
            "No HuggingFace token. Set HF_TOKEN env var for pyannote diarization. "
            "Get a free token at https://huggingface.co/settings/tokens"
        )
        raise ValueError("HF_TOKEN required for speaker diarization")

    from pyannote.audio import Pipeline

    # Extract audio
    work = work_dir or video_path.parent
    wav_path = work / f"{video_path.stem}_diarize.wav"
    logger.info("Extracting audio for diarization ...")
    _extract_audio_wav(video_path, wav_path)

    # Load diarization pipeline
    logger.info("Loading pyannote diarization model ...")
    try:
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=token,
        )
    except TypeError:
        # Older pyannote versions use use_auth_token
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=token,
        )

    # Run diarization
    logger.info("Running speaker diarization (this may take a few minutes) ...")
    kwargs = {}
    if num_speakers:
        kwargs["num_speakers"] = num_speakers
    else:
        kwargs["min_speakers"] = min_speakers
        kwargs["max_speakers"] = max_speakers

    raw_output = pipeline(str(wav_path), **kwargs)

    # pyannote 4.x returns DiarizeOutput; extract the Annotation object
    if hasattr(raw_output, "speaker_diarization"):
        diarization = raw_output.speaker_diarization
    else:
        diarization = raw_output  # older versions return Annotation directly

    # Parse results
    speaker_map: dict[str, int] = {}
    segments: list[SpeakerSegment] = []

    for turn, _, speaker in diarization.itertracks(yield_label=True):
        if speaker not in speaker_map:
            idx = len(speaker_map)
            speaker_map[speaker] = idx

        idx = speaker_map[speaker]
        color = SPEAKER_COLORS[idx % len(SPEAKER_COLORS)]

        segments.append(SpeakerSegment(
            start=turn.start,
            end=turn.end,
            speaker=speaker,
            speaker_index=idx,
            color_hex=color["hex"],
            color_ffmpeg=color["ffmpeg"],
        ))

    # Cleanup
    wav_path.unlink(missing_ok=True)

    logger.info("Diarization complete: %d speakers, %d segments", len(speaker_map), len(segments))
    return DiarizationResult(
        segments=segments,
        num_speakers=len(speaker_map),
        speaker_map=speaker_map,
    )


def align_transcript_with_speakers(
    transcript: list[dict],
    diarization: DiarizationResult,
) -> list[SpeakerSegment]:
    """Align transcript segments with diarization speaker labels.

    For each transcript segment, find which speaker is dominant
    (most overlap) and assign that speaker's color.
    """
    colored_segments: list[SpeakerSegment] = []

    for seg in transcript:
        seg_start = float(seg.get("start", 0))
        seg_end = float(seg.get("end", 0))
        seg_text = seg.get("text", "").strip()

        if not seg_text:
            continue

        # Find the diarization segment with most overlap
        best_speaker = ""
        best_overlap = 0.0
        best_idx = 0

        for d_seg in diarization.segments:
            overlap_start = max(seg_start, d_seg.start)
            overlap_end = min(seg_end, d_seg.end)
            overlap = max(0, overlap_end - overlap_start)

            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = d_seg.speaker
                best_idx = d_seg.speaker_index

        color = SPEAKER_COLORS[best_idx % len(SPEAKER_COLORS)]

        colored_segments.append(SpeakerSegment(
            start=seg_start,
            end=seg_end,
            speaker=best_speaker or "UNKNOWN",
            speaker_index=best_idx,
            text=seg_text,
            color_hex=color["hex"],
            color_ffmpeg=color["ffmpeg"],
        ))

    return colored_segments


def build_subtitle_filters(
    colored_segments: list[SpeakerSegment],
    *,
    font_size: int = 22,
    y_position: str = "h-50",
    max_segments: int = 200,
) -> list[str]:
    """Build ffmpeg drawtext filters for color-coded subtitles.

    Returns a list of filter strings to be joined into a filter_complex.
    """
    filters: list[str] = []

    for i, seg in enumerate(colored_segments[:max_segments]):
        safe_text = (
            seg.text
            .replace("\\", "\\\\")
            .replace("'", "\u2019")
            .replace(":", "\\:")
            .replace("%", "%%")
        )
        enable = f"between(t,{seg.start:.2f},{seg.end:.2f})"

        filters.append(
            f"drawtext="
            f"text='{safe_text}':"
            f"fontsize={font_size}:"
            f"fontcolor={seg.color_ffmpeg}:"
            f"borderw=2:bordercolor=black:"
            f"box=1:boxcolor=black@0.5:boxborderw=6:"
            f"x=(w-tw)/2:y={y_position}:"
            f"enable='{enable}'"
        )

    return filters
