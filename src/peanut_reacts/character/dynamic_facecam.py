"""Dynamic character facecam — reactive clip selection based on emotions.

Unlike the static "loop one clip forever" approach, this module:
1. Maps each reaction's emotion to the best matching AI character clip
2. Builds a timeline that switches clips at each reaction moment
3. Fills gaps with an idle clip
4. Optionally crossfades between clips for smooth transitions
5. Renders a facecam track sized for corner overlay
6. Composites the facecam onto the main video with a chroma-keyed background

Uses the existing AI clips in data/ai_peanut_clips/ which have green backgrounds
(chroma key removable) and distinct emotion variants.
"""
from __future__ import annotations

import logging
import random
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CLIPS_DIR = PROJECT_ROOT / "data" / "ai_peanut_clips"


# Comprehensive emotion -> clip mapping
EMOTION_TO_CLIPS = {
    # Shock/surprise family
    "shocked": ["peanut_shocked.mp4"],
    "surprised": ["peanut_shocked.mp4"],
    "amazed": ["peanut_shocked.mp4", "peanut_excited.mp4"],
    "mind_blown": ["peanut_shocked.mp4"],
    "screaming": ["peanut_shocked.mp4"],

    # Joy/laughter family
    "laughing": ["peanut_laughing.mp4"],
    "amused": ["peanut_laughing.mp4"],
    "funny": ["peanut_laughing.mp4"],
    "happy": ["peanut_laughing.mp4", "peanut_excited.mp4"],

    # Hype/excited family
    "excited": ["peanut_excited.mp4", "peanut_celebrating.mp4"],
    "hype": ["peanut_excited.mp4", "peanut_celebrating.mp4"],
    "celebrating": ["peanut_celebrating.mp4"],
    "triumphant": ["peanut_celebrating.mp4"],

    # Confusion family
    "confused": ["peanut_confused.mp4"],
    "puzzled": ["peanut_confused.mp4"],
    "thinking": ["peanut_confused.mp4", "peanut_idle.mp4"],
    "analytical": ["peanut_confused.mp4", "peanut_idle.mp4"],

    # Fear/suspense family
    "scared": ["peanut_scared.mp4"],
    "suspense": ["peanut_scared.mp4"],
    "nervous": ["peanut_scared.mp4"],
    "worried": ["peanut_scared.mp4"],

    # Sarcasm/reaction family
    "sarcastic": ["peanut_sarcastic.mp4", "peanut_facepalm.mp4"],
    "disappointed": ["peanut_facepalm.mp4"],
    "facepalm": ["peanut_facepalm.mp4"],
    "knowing": ["peanut_sarcastic.mp4"],
    "smug": ["peanut_sarcastic.mp4"],

    # Idle / neutral
    "neutral": ["peanut_idle.mp4"],
    "idle": ["peanut_idle.mp4"],
    "calm": ["peanut_idle.mp4"],
    "": ["peanut_idle.mp4"],
}


@dataclass
class FacecamSegment:
    start: float
    end: float
    clip_path: Path
    emotion: str
    intensity: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start


def get_clip_for_emotion(emotion: str, clips_dir: Path = DEFAULT_CLIPS_DIR) -> Optional[Path]:
    """Pick the best available AI clip for an emotion."""
    emotion_norm = (emotion or "").lower().strip()
    candidates = EMOTION_TO_CLIPS.get(emotion_norm, EMOTION_TO_CLIPS["idle"])

    for name in candidates:
        path = clips_dir / name
        if path.exists():
            return path

    # Fallback: any available clip
    fallback = list(clips_dir.glob("peanut_*.mp4"))
    return random.choice(fallback) if fallback else None


def build_facecam_timeline(
    reactions: list[dict],
    video_duration: float,
    reaction_hold_seconds: float = 4.0,
    clips_dir: Path = DEFAULT_CLIPS_DIR,
) -> list[FacecamSegment]:
    """Build a timeline of FacecamSegments from reaction data.

    Each reaction produces a segment showing the matching emotion clip for
    ~4 seconds (or until the next reaction). Gaps between reactions are
    filled with the idle clip.
    """
    if not reactions:
        idle = get_clip_for_emotion("idle", clips_dir)
        if idle:
            return [FacecamSegment(0, video_duration, idle, "idle")]
        return []

    sorted_rx = sorted(reactions, key=lambda r: float(r.get("start", 0)))
    segments: list[FacecamSegment] = []
    prev_end = 0.0
    idle_clip = get_clip_for_emotion("idle", clips_dir)

    for i, rx in enumerate(sorted_rx):
        start = float(rx.get("start", 0))
        emotion = rx.get("emotion", "neutral")
        intensity = float(rx.get("intensity", 1.0))

        # Fill gap before this reaction with idle
        if start > prev_end + 0.5 and idle_clip:
            segments.append(FacecamSegment(
                start=prev_end,
                end=start,
                clip_path=idle_clip,
                emotion="idle",
            ))

        # Determine segment end: either next reaction start, or reaction_hold_seconds later
        if i + 1 < len(sorted_rx):
            next_start = float(sorted_rx[i + 1].get("start", 0))
            hold_end = min(start + reaction_hold_seconds, next_start)
        else:
            hold_end = min(start + reaction_hold_seconds, video_duration)

        clip = get_clip_for_emotion(emotion, clips_dir)
        if clip:
            segments.append(FacecamSegment(
                start=start,
                end=hold_end,
                clip_path=clip,
                emotion=emotion,
                intensity=intensity,
            ))
            prev_end = hold_end

    # Fill trailing time with idle
    if prev_end < video_duration and idle_clip:
        segments.append(FacecamSegment(
            start=prev_end,
            end=video_duration,
            clip_path=idle_clip,
            emotion="idle",
        ))

    log.info("Timeline: %d segments for %.1fs video", len(segments), video_duration)
    return segments


def render_dynamic_facecam(
    timeline: list[FacecamSegment],
    output_path: Path,
    size: int = 360,
    fps: int = 25,
    chroma_key: bool = False,
) -> Optional[Path]:
    """Render the facecam track by stitching clips per the timeline.

    Each segment loops its AI clip for the segment duration. The output is
    a single video at the specified size. If chroma_key=True, keeps the green
    (caller applies chromakey filter later when overlaying).
    """
    if not timeline:
        log.warning("Empty timeline")
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = output_path.parent / f"_facecam_{output_path.stem}"
    temp_dir.mkdir(exist_ok=True)

    segment_files: list[Path] = []
    for i, seg in enumerate(timeline):
        if seg.duration <= 0.1:
            continue
        seg_file = temp_dir / f"seg_{i:05d}.mp4"
        if seg_file.exists():
            segment_files.append(seg_file)
            continue

        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(seg.clip_path),
            "-t", f"{seg.duration:.3f}",
            "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=0x00FF00,setsar=1:1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-an",
            str(seg_file),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
            segment_files.append(seg_file)
        except subprocess.CalledProcessError as e:
            log.warning("Segment %d failed: %s", i, e.stderr.decode("utf-8", errors="replace")[-200:])

    if not segment_files:
        return None

    # Concat all segments via concat demuxer
    # Use absolute paths in the list file (safer than cwd tricks on Windows)
    list_file = temp_dir / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in segment_files),
        encoding="utf-8",
    )

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(output_path),
        ], check=True, capture_output=True, timeout=600)
        log.info("Rendered dynamic facecam: %s (%d segments)", output_path.name, len(segment_files))

        # Cleanup
        for f in segment_files:
            f.unlink(missing_ok=True)
        list_file.unlink(missing_ok=True)
        try:
            temp_dir.rmdir()
        except Exception:
            pass
        return output_path
    except subprocess.CalledProcessError as e:
        log.error("Facecam concat failed: %s", e.stderr.decode("utf-8", errors="replace")[-300:])
        return None


def composite_with_facecam(
    source_video: Path,
    facecam_track: Path,
    audio_track: Path,
    output_path: Path,
    position: str = "bottom-right",
    facecam_size: int = 360,
    margin: int = 40,
    use_chromakey: bool = True,
    encoder: Optional[str] = None,
) -> Optional[Path]:
    """Composite the final video: source + facecam overlay + custom audio.

    Position: bottom-right | bottom-left | top-right | top-left
    If use_chromakey=True, removes the green background from the facecam.
    """
    if not source_video.exists() or not facecam_track.exists() or not audio_track.exists():
        log.error("Missing inputs for final composite")
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Position coordinates
    pos_map = {
        "bottom-right": f"W-w-{margin}:H-h-{margin}",
        "bottom-left":  f"{margin}:H-h-{margin}",
        "top-right":    f"W-w-{margin}:{margin}",
        "top-left":     f"{margin}:{margin}",
    }
    overlay_xy = pos_map.get(position, pos_map["bottom-right"])

    if encoder is None:
        encoder = "h264_nvenc" if _nvenc_available() else "libx264"
    preset = "fast" if encoder == "h264_nvenc" else "veryfast"

    if use_chromakey:
        # Chroma key + scale + overlay
        filter_complex = (
            f"[1:v]chromakey=0x00FF00:0.15:0.08,"
            f"scale={facecam_size}:{facecam_size}[fc];"
            f"[0:v][fc]overlay={overlay_xy}[out]"
        )
    else:
        filter_complex = (
            f"[1:v]scale={facecam_size}:{facecam_size}[fc];"
            f"[0:v][fc]overlay={overlay_xy}[out]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(source_video),
        "-i", str(facecam_track),
        "-i", str(audio_track),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "2:a",
        "-c:v", encoder, "-preset", preset,
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=7200)
        log.info("Final composite with dynamic facecam: %s", output_path.name)
        return output_path
    except subprocess.CalledProcessError as e:
        log.error("Composite failed: %s", e.stderr.decode("utf-8", errors="replace")[-500:])
        return None


def _nvenc_available() -> bool:
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        return "h264_nvenc" in r.stdout
    except Exception:
        return False


def build_and_composite(
    source_video: Path,
    audio_track: Path,
    reactions: list[dict],
    output_path: Path,
    video_duration: float,
    facecam_size: int = 360,
    position: str = "bottom-right",
    clips_dir: Path = DEFAULT_CLIPS_DIR,
) -> Optional[Path]:
    """One-shot: build dynamic facecam from reactions and composite to final video.

    This is the function the scheduler pipeline should call instead of the
    old static composite.
    """
    if not reactions:
        log.warning("No reactions provided, skipping dynamic facecam")
        return None

    work_dir = output_path.parent / f"_work_{output_path.stem}"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Build timeline from reactions
    timeline = build_facecam_timeline(reactions, video_duration, clips_dir=clips_dir)
    if not timeline:
        log.error("Empty facecam timeline")
        return None

    # Step 2: Render the facecam video track
    facecam_path = work_dir / "facecam.mp4"
    fc = render_dynamic_facecam(timeline, facecam_path, size=facecam_size)
    if not fc:
        log.error("Facecam render failed")
        return None

    # Step 3: Composite onto source video with overlay
    final = composite_with_facecam(
        source_video=source_video,
        facecam_track=fc,
        audio_track=audio_track,
        output_path=output_path,
        facecam_size=facecam_size,
        position=position,
    )

    # Cleanup work files
    try:
        facecam_path.unlink(missing_ok=True)
        work_dir.rmdir()
    except Exception:
        pass

    return final
