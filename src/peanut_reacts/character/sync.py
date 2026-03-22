"""
Speech-synced peanut animation.

Drives the peanut character's mouth from TTS word timing data,
producing animation segments where the mouth opens when speaking.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from peanut_reacts.character.renderer import (
    draw_peanut_character,
    export_alpha_webm,
    _clamp,
)
from peanut_reacts.character.tts import TTSWordTiming

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpeechEvent:
    """A continuous speaking interval (possibly merged from adjacent words)."""
    start: float   # seconds from animation start
    end: float     # seconds from animation start


# ---------------------------------------------------------------------------
# Timing conversion
# ---------------------------------------------------------------------------

def word_timings_to_speech_events(
    timings: list[TTSWordTiming] | tuple[TTSWordTiming, ...],
    line_start: float = 0.0,
    *,
    merge_gap_ms: int = 80,
) -> list[SpeechEvent]:
    """Convert TTS word timings into speech events.

    *line_start* is the absolute video timestamp where this audio begins.
    Adjacent words closer than *merge_gap_ms* are merged into continuous speech.
    """
    if not timings:
        return []

    # Convert to absolute time events
    raw: list[tuple[float, float]] = []
    for w in timings:
        start = line_start + w.offset_ms / 1000.0
        end = start + w.duration_ms / 1000.0
        raw.append((start, end))

    raw.sort(key=lambda e: e[0])

    # Merge close events
    merge_gap = merge_gap_ms / 1000.0
    merged: list[SpeechEvent] = []
    cur_start, cur_end = raw[0]

    for start, end in raw[1:]:
        if start - cur_end <= merge_gap:
            cur_end = max(cur_end, end)
        else:
            merged.append(SpeechEvent(start=cur_start, end=cur_end))
            cur_start, cur_end = start, end
    merged.append(SpeechEvent(start=cur_start, end=cur_end))

    return merged


def compute_mouth_open(t: float, speech_events: list[SpeechEvent], ease_ms: int = 50) -> float:
    """Return mouth openness [0.0, 1.0] at time *t*.

    Returns 1.0 during speech, 0.0 during silence, with *ease_ms*
    ease-in/ease-out ramps for smooth animation.
    """
    ease = ease_ms / 1000.0

    for event in speech_events:
        if event.start - ease <= t <= event.end + ease:
            # Inside or near a speech event
            if t < event.start:
                # Ease in
                return _clamp((t - (event.start - ease)) / ease, 0.0, 1.0)
            elif t > event.end:
                # Ease out
                return _clamp(1.0 - (t - event.end) / ease, 0.0, 1.0)
            else:
                return 1.0

    return 0.0


# ---------------------------------------------------------------------------
# Frame rendering
# ---------------------------------------------------------------------------

def render_synced_frames(
    out_dir: Path,
    duration: float,
    fps: int,
    speech_events: list[SpeechEvent],
    *,
    canvas: int = 512,
    char_size: int = 420,
    seed: int = 0,
) -> None:
    """Render speech-synced animation frames as PNGs.

    Uses the existing :func:`draw_peanut_character` with ``mouth_open_override``
    driven by TTS word timings.
    """
    import math
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    total_frames = int(round(duration * fps))

    for i in range(total_frames):
        t = i / fps
        mouth = compute_mouth_open(t, speech_events)

        # Green-screen background for reliable chroma-keying in ffmpeg
        frame = Image.new("RGBA", (canvas, canvas), (0, 255, 0, 255))
        char = draw_peanut_character(t, size=char_size, seed=seed, mouth_open_override=mouth)

        # Bounce and wobble (same as original renderer)
        bounce = int(8 * math.sin(2 * math.pi * 0.7 * t))
        wobble_deg = 4.0 * math.sin(2 * math.pi * 0.35 * t)
        char_rot = char.rotate(wobble_deg, resample=Image.BICUBIC, expand=True)

        x = (canvas - char_rot.width) // 2
        y = (canvas - char_rot.height) // 2 + bounce
        # Composite character onto green background
        frame.paste(char_rot, (x, y), char_rot)
        frame.save(out_dir / f"{i:04d}.png", "PNG")

    logger.info("Rendered %d synced frames to %s", total_frames, out_dir)


def render_reaction_video(
    speech_events: list[SpeechEvent],
    duration: float,
    output_path: Path,
    *,
    fps: int = 24,
    canvas: int = 512,
    char_size: int = 420,
    seed: int = 0,
) -> Path:
    """Render speech-synced frames with green-screen background as MP4.

    Uses a temporary directory for intermediate PNG frames.
    The green background is removed via colorkey during compositing.
    """
    import subprocess

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="peanut_sync_") as tmp:
        frames_dir = Path(tmp) / "frames"
        render_synced_frames(
            frames_dir, duration, fps, speech_events,
            canvas=canvas, char_size=char_size, seed=seed,
        )
        # Encode as MP4 (green-screen background)
        subprocess.run([
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", str(frames_dir / "%04d.png"),
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    logger.info("Exported synced video: %s (%.2fs)", output_path.name, duration)
    return output_path


# Keep backward compat alias
render_reaction_webm = render_reaction_video
