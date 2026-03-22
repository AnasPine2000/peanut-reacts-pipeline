"""
Audio-amplitude-based avatar overlay.

Creates an animated avatar that reacts to audio volume by switching
between normal and open-mouth states, with subtitle text overlay.

Refactored from archive/avatar.py.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from peanut_reacts.core.srt import parse_srt

logger = logging.getLogger(__name__)


def get_subtitle_text(t: float, subtitle_entries: list[tuple[float, float, str]]) -> str:
    """Return the subtitle text active at time *t*, or empty string."""
    for start, end, text in subtitle_entries:
        if start <= t < end:
            return text
    return ""


def compute_audio_amplitudes(audio_clip, fps: int = 10) -> list[float]:
    """Compute RMS amplitudes of *audio_clip*, sampling *fps* times/second.

    *audio_clip* should be a moviepy AudioClip.
    """
    duration = audio_clip.duration
    n_samples = int(duration * fps)
    amplitudes: list[float] = []

    for i in range(n_samples):
        start_t = i / fps
        end_t = min((i + 1) / fps, duration)
        audio_array = audio_clip.subclip(start_t, end_t).to_soundarray(nbytes=4, fps=44100)
        rms = float(np.sqrt(np.mean(audio_array ** 2)))
        amplitudes.append(rms)

    return amplitudes


def create_avatar_video(
    video_path: Path,
    srt_path: Path,
    avatar_normal_path: Path,
    avatar_open_path: Path,
    output_path: Path,
    *,
    amplitude_threshold: float = 0.02,
    avatar_scale: float = 0.3,
    position: tuple[str, str] = ("right", "bottom"),
) -> Path:
    """Overlay an animated avatar onto *video_path*.

    The avatar switches between normal and open-mouth images based on
    audio amplitude, with subtitles from *srt_path* displayed on top.
    """
    from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip, VideoClip

    # Parse subtitles
    srt_entries = parse_srt(srt_path)
    subtitle_tuples = [(s["start"], s["end"], s["text"]) for s in srt_entries]

    # Load video
    video_clip = VideoFileClip(str(video_path))
    audio_clip = video_clip.audio

    # Audio analysis
    fps_audio = 10
    volume_array = compute_audio_amplitudes(audio_clip, fps_audio)

    # Load avatar images
    avatar_normal = ImageClip(str(avatar_normal_path)).set_duration(video_clip.duration)
    avatar_open = ImageClip(str(avatar_open_path)).set_duration(video_clip.duration)

    def make_frame(t: float):
        idx = min(int(math.floor(t * fps_audio)), len(volume_array) - 1)
        amplitude = volume_array[idx]

        if amplitude > amplitude_threshold:
            return avatar_open.get_frame(t)
        return avatar_normal.get_frame(t)

    avatar_animated = VideoClip(make_frame, duration=video_clip.duration)
    avatar_window = avatar_animated.resize(width=int(video_clip.w * avatar_scale))
    avatar_window = avatar_window.set_position(position)

    composite = CompositeVideoClip([video_clip, avatar_window])
    composite = composite.set_audio(audio_clip)
    composite.write_videofile(str(output_path), fps=video_clip.fps)

    logger.info("Avatar overlay complete: %s", output_path)
    return output_path
