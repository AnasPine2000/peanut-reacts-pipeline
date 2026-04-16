"""
Emotion-mapped AI clip selector for reaction videos.

Maps each reaction line's emotion to the closest AI-generated clip,
then builds a continuous facecam track that switches between emotion
clips based on what's happening in the video.
"""

from __future__ import annotations

import logging
import random
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Map reaction emotions to AI clip filenames
EMOTION_TO_CLIP = {
    "shocked": ["peanut_shocked.mp4"],
    "excited": ["peanut_excited.mp4", "peanut_celebrating.mp4"],
    "amused": ["peanut_laughing.mp4"],
    "funny": ["peanut_laughing.mp4"],
    "hype": ["peanut_excited.mp4", "peanut_celebrating.mp4"],
    "confused": ["peanut_confused.mp4", "peanut_suspicious.mp4"],
    "suspense": ["peanut_suspicious.mp4", "peanut_scared.mp4"],
    "scared": ["peanut_scared.mp4"],
    "sarcastic": ["peanut_sarcastic.mp4", "peanut_facepalm.mp4"],
    "analysis": ["peanut_suspicious.mp4", "peanut_idle.mp4"],
    "analytical": ["peanut_suspicious.mp4"],
    "knowing": ["peanut_sarcastic.mp4"],
    "surprised": ["peanut_shocked.mp4"],
    "neutral": ["peanut_idle.mp4"],
    "idle": ["peanut_idle.mp4"],
}


def get_clip_for_emotion(emotion: str, clips_dir: Path) -> Path | None:
    """Get the best AI clip for a given emotion."""
    candidates = EMOTION_TO_CLIP.get(emotion.lower(), EMOTION_TO_CLIP["idle"])

    for name in candidates:
        path = clips_dir / name
        if path.exists():
            return path

    # Fallback: any available clip
    available = list(clips_dir.glob("peanut_*.mp4"))
    return random.choice(available) if available else None


def build_emotion_timeline(
    reactions: list[dict],
    video_duration: float,
    clips_dir: Path,
    default_emotion: str = "idle",
) -> list[dict]:
    """Build a timeline of which AI clip to show at each moment.

    Returns list of: {"start": float, "end": float, "clip": Path, "emotion": str}
    """
    if not reactions:
        clip = get_clip_for_emotion("idle", clips_dir)
        return [{"start": 0, "end": video_duration, "clip": clip, "emotion": "idle"}] if clip else []

    timeline: list[dict] = []
    sorted_reactions = sorted(reactions, key=lambda r: r["start"])

    prev_end = 0.0

    for r in sorted_reactions:
        start = r["start"]
        emotion = r.get("emotion", "neutral")

        # Fill gap before this reaction with idle
        if start > prev_end + 1:
            idle_clip = get_clip_for_emotion("idle", clips_dir)
            if idle_clip:
                timeline.append({
                    "start": prev_end,
                    "end": start,
                    "clip": idle_clip,
                    "emotion": "idle",
                })

        # Reaction emotion clip (show for ~5-8 seconds)
        reaction_duration = min(8.0, r.get("duration", 5.0))
        clip = get_clip_for_emotion(emotion, clips_dir)
        if clip:
            timeline.append({
                "start": start,
                "end": start + reaction_duration,
                "clip": clip,
                "emotion": emotion,
            })
            prev_end = start + reaction_duration

    # Fill remaining time with idle
    if prev_end < video_duration:
        idle_clip = get_clip_for_emotion("idle", clips_dir)
        if idle_clip:
            timeline.append({
                "start": prev_end,
                "end": video_duration,
                "clip": idle_clip,
                "emotion": "idle",
            })

    return timeline


def render_facecam_track(
    timeline: list[dict],
    output_path: Path,
    video_duration: float,
    size: int = 300,
    fps: int = 25,
) -> Path:
    """Render the complete facecam video track from the emotion timeline.

    Concatenates looped AI clips according to the timeline, each segment
    scaled to the facecam size with green screen background.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not timeline:
        logger.warning("Empty timeline — creating blank facecam")
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x00FF00:s={size}x{size}:d={video_duration}:r={fps}",
            "-c:v", "libx264", "-crf", "22", "-pix_fmt", "yuv420p",
            str(output_path),
        ], check=True, capture_output=True)
        return output_path

    # Render each timeline segment
    temp_segments: list[Path] = []
    temp_dir = output_path.parent / "facecam_temp"
    temp_dir.mkdir(exist_ok=True)

    for i, entry in enumerate(timeline):
        seg_dur = entry["end"] - entry["start"]
        if seg_dur <= 0:
            continue

        clip = Path(entry["clip"])
        if not clip.exists():
            continue

        seg_path = temp_dir / f"fc_{i:04d}.mp4"

        # Loop the AI clip to fill the segment duration, scale to facecam size
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(clip),
            "-t", str(seg_dur),
            "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=0x00FF00",
            "-c:v", "libx264", "-crf", "22", "-preset", "fast",
            "-pix_fmt", "yuv420p", "-an",
            str(seg_path),
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0 and seg_path.exists():
            temp_segments.append(seg_path)

    if not temp_segments:
        logger.error("No facecam segments rendered")
        return output_path

    # Concatenate all segments
    from peanut_reacts.core.ffmpeg import concatenate
    concatenate(temp_segments, output_path)

    # Cleanup
    for s in temp_segments:
        s.unlink(missing_ok=True)
    try:
        temp_dir.rmdir()
    except:
        pass

    logger.info("Facecam track: %s (%.1fs, %d segments)", output_path.name, video_duration, len(temp_segments))
    return output_path
