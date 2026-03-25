"""
Speaker diarization for color-coded subtitles.

Uses pyannote.audio to identify who is speaking when, then aligns
diarization output with transcript segments to produce color-coded
subtitle overlays for the reaction video.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# 10 distinct colors for up to 10 speakers
SPEAKER_COLORS = [
    {"hex": "#44FF44", "ffmpeg": "0x44FF44", "name": "green"},
    {"hex": "#FF4444", "ffmpeg": "0xFF4444", "name": "red"},
    {"hex": "#FFFF44", "ffmpeg": "0xFFFF44", "name": "yellow"},
    {"hex": "#FFAA44", "ffmpeg": "0xFFAA44", "name": "orange"},
    {"hex": "#AAAAFF", "ffmpeg": "0xAAAAFF", "name": "lavender"},
    {"hex": "#44AAFF", "ffmpeg": "0x44AAFF", "name": "blue"},
    {"hex": "#44FFFF", "ffmpeg": "0x44FFFF", "name": "cyan"},
    {"hex": "#FF44FF", "ffmpeg": "0xFF44FF", "name": "pink"},
    {"hex": "#FF8888", "ffmpeg": "0xFF8888", "name": "salmon"},
    {"hex": "#88FF88", "ffmpeg": "0x88FF88", "name": "lime"},
]


@dataclass
class DiarizedSegment:
    start: float
    end: float
    speaker: str
    speaker_index: int


@dataclass
class DiarizationResult:
    segments: list[DiarizedSegment]
    num_speakers: int
    speaker_map: dict[str, int]


@dataclass
class SpeakerSegment:
    start: float
    end: float
    speaker: str
    speaker_index: int
    text: str
    color_hex: str
    color_ffmpeg: str


def diarize_video(
    video_path: Path,
    *,
    hf_token: str = "",
    num_speakers: Optional[int] = None,
    work_dir: Optional[Path] = None,
) -> DiarizationResult:
    """Run speaker diarization on a video file.

    Uses pyannote.audio 3.x pipeline to identify speakers.
    """
    from pyannote.audio import Pipeline

    work_dir = work_dir or video_path.parent
    wav_path = work_dir / f"{video_path.stem}_diarize.wav"

    # Extract audio as WAV
    if not wav_path.exists():
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-ar", "16000", "-ac", "1", "-vn",
            str(wav_path),
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        token=hf_token or None,
    )

    kwargs = {}
    if num_speakers and num_speakers > 0:
        kwargs["num_speakers"] = num_speakers

    output = pipeline(str(wav_path), **kwargs)

    # Parse output — pyannote 4.x returns DiarizeOutput
    annotation = output.speaker_diarization if hasattr(output, "speaker_diarization") else output

    segments: list[DiarizedSegment] = []
    speaker_set: set[str] = set()

    for turn, _, speaker in annotation.itertracks(yield_label=True):
        speaker_set.add(speaker)
        segments.append(DiarizedSegment(
            start=turn.start,
            end=turn.end,
            speaker=speaker,
            speaker_index=0,
        ))

    # Assign consistent indices
    speaker_list = sorted(speaker_set)
    speaker_map = {s: i for i, s in enumerate(speaker_list)}

    for seg in segments:
        seg.speaker_index = speaker_map[seg.speaker]

    return DiarizationResult(
        segments=segments,
        num_speakers=len(speaker_list),
        speaker_map=speaker_map,
    )


def align_transcript_with_speakers(
    transcript: list[dict],
    diarization: DiarizationResult,
) -> list[SpeakerSegment]:
    """Align transcript segments with diarized speakers.

    For each transcript segment, find which speaker was talking
    at that time and assign the corresponding color.
    """
    colored_segments: list[SpeakerSegment] = []

    for seg in transcript:
        seg_start = float(seg.get("start", 0))
        seg_end = float(seg.get("end", seg_start + 1))
        seg_text = seg.get("text", "").strip()

        if not seg_text:
            continue

        seg_mid = (seg_start + seg_end) / 2

        best_speaker = None
        best_idx = 0
        best_overlap = 0.0

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


def _merge_consecutive_segments(
    segments: list[SpeakerSegment],
    max_gap: float = 0.5,
) -> list[SpeakerSegment]:
    """Merge consecutive segments from the same speaker to reduce filter count."""
    if not segments:
        return []

    merged: list[SpeakerSegment] = []
    current = segments[0]

    for seg in segments[1:]:
        if (seg.speaker == current.speaker
                and seg.start - current.end < max_gap
                and len(current.text) + len(seg.text) < 80):
            current = SpeakerSegment(
                start=current.start,
                end=seg.end,
                speaker=current.speaker,
                speaker_index=current.speaker_index,
                text=(current.text + " " + seg.text).strip(),
                color_hex=current.color_hex,
                color_ffmpeg=current.color_ffmpeg,
            )
        else:
            merged.append(current)
            current = seg

    merged.append(current)
    return merged


def _remove_overlapping_segments(
    segments: list[SpeakerSegment],
) -> list[SpeakerSegment]:
    """Remove overlapping subtitle segments — trim earlier one to prevent stacking."""
    if not segments:
        return []

    sorted_segs = sorted(segments, key=lambda s: s.start)
    result: list[SpeakerSegment] = [sorted_segs[0]]

    for seg in sorted_segs[1:]:
        prev = result[-1]
        if seg.start < prev.end:
            # Trim previous segment to end when this one starts
            trimmed = SpeakerSegment(
                start=prev.start,
                end=seg.start,
                speaker=prev.speaker,
                speaker_index=prev.speaker_index,
                text=prev.text,
                color_hex=prev.color_hex,
                color_ffmpeg=prev.color_ffmpeg,
            )
            if trimmed.end - trimmed.start > 0.2:
                result[-1] = trimmed
            else:
                result.pop()
        result.append(seg)

    return result


def build_subtitle_filters(
    colored_segments: list[SpeakerSegment],
    *,
    font_size: int = 0,
    video_width: int = 0,
    video_height: int = 0,
    y_position: str = "",
    x_position: str = "20",
    max_segments: int = 200,
    max_text_len: int = 0,
) -> list[str]:
    """Build ffmpeg drawtext filters for color-coded subtitles.

    Auto-scales font size to video resolution, merges same-speaker
    segments, and removes time overlaps to prevent text stacking.
    """
    # Auto-scale font size to resolution
    if font_size <= 0:
        if video_height >= 1080:
            font_size = 28
        elif video_height >= 720:
            font_size = 22
        elif video_height >= 480:
            font_size = 18
        else:
            font_size = 14

    # Auto-scale max text length to resolution
    if max_text_len <= 0:
        if video_width >= 1920:
            max_text_len = 70
        elif video_width >= 1280:
            max_text_len = 55
        elif video_width >= 640:
            max_text_len = 40
        else:
            max_text_len = 30

    if not y_position:
        y_position = f"h-{font_size + 25}"

    # Merge same-speaker consecutive segments
    merged = _merge_consecutive_segments(colored_segments)

    # Remove time overlaps (prevents text stacking)
    deoverlapped = _remove_overlapping_segments(merged)

    logger.info(
        "Subtitles: %d raw -> %d merged -> %d deoverlapped (font=%d, maxlen=%d)",
        len(colored_segments), len(merged), len(deoverlapped), font_size, max_text_len,
    )

    border_w = max(1, font_size // 10)
    box_pad = max(4, font_size // 5)

    filters: list[str] = []
    for seg in deoverlapped[:max_segments]:
        safe_text = (
            seg.text
            .replace("\\", "\\\\")
            .replace("'", "\u2019")
            .replace(":", "\\:")
            .replace("%", "%%")
        )
        if len(safe_text) > max_text_len:
            safe_text = safe_text[:max_text_len - 3] + "..."

        enable = f"between(t,{seg.start:.2f},{seg.end:.2f})"

        filters.append(
            f"drawtext="
            f"text='{safe_text}':"
            f"fontsize={font_size}:"
            f"fontcolor={seg.color_ffmpeg}:"
            f"borderw={border_w}:bordercolor=black:"
            f"box=1:boxcolor=black@0.5:boxborderw={box_pad}:"
            f"x={x_position}:y={y_position}:"
            f"enable='{enable}'"
        )

    return filters
