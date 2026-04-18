"""Shorts composer — creates polished vertical Shorts with character overlay.

Takes a peak moment from a source video and produces a single upload-ready
vertical clip with:
1. Vertical conversion (9:16 with blur background)
2. Dynamic character facecam overlay (emotion-driven)
3. Hook text burned in (first 2 seconds)
4. CTA text at end ("Full video on YouTube")
5. Captions generated per platform

This is the "quality" layer that sits between raw clip extraction and posting.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class ComposedShort:
    """A fully composed Short ready for upload."""
    clip_path: Path
    source_video_id: str = ""
    source_video_title: str = ""
    peak_start: float = 0
    peak_score: float = 0
    peak_emotion: str = ""
    hook_text: str = ""
    caption_yt: str = ""
    caption_tt: str = ""
    hashtags: list[str] = field(default_factory=list)
    duration_seconds: float = 0


def compose_short(
    source_video: Path,
    peak,
    reactions: list[dict],
    output_dir: Path,
    channel_id: str = "",
    character_name: str = "Peanut",
    source_video_id: str = "",
    source_video_title: str = "",
    niche: str = "sidemen",
    facecam_enabled: bool = True,
    facecam_size: int = 300,
    facecam_position: str = "bottom-left",
    custom_hashtags: list[str] = None,
    llm_provider=None,
) -> Optional[ComposedShort]:
    """Full composition pipeline: peak → polished vertical Short.

    Steps:
    1. Extract horizontal clip from source
    2. Convert to vertical (9:16 blur_bg)
    3. If facecam_enabled: render dynamic facecam for this clip's time range
    4. Overlay facecam on vertical clip (chroma-keyed)
    5. Add hook text + CTA
    6. Generate captions for YT + TT

    Returns ComposedShort or None on failure.
    """
    from .clip_extractor import extract_clip, convert_to_vertical
    from .caption_generator import generate_hook_text, generate_caption

    output_dir.mkdir(parents=True, exist_ok=True)
    start_int = int(peak.start_seconds)
    prefix = f"{source_video_id or 'clip'}_{start_int:05d}"

    # ── Step 1: Extract horizontal clip ───────────────────────────
    h_clip = output_dir / f"{prefix}_h.mp4"
    h = extract_clip(source_video, peak.start_seconds, peak.end_seconds, h_clip,
                     padding_before=1.5, padding_after=0.5)
    if not h:
        log.error("Failed to extract horizontal clip for peak at %ds", start_int)
        return None

    # ── Step 2: Convert to vertical ───────────────────────────────
    v_clip = output_dir / f"{prefix}_v.mp4"
    v = convert_to_vertical(h, v_clip, method="blur_bg")
    if not v:
        log.error("Failed to convert to vertical")
        return None

    # ── Step 3+4: Dynamic facecam overlay ─────────────────────────
    if facecam_enabled:
        fc_clip = _add_facecam_to_vertical(
            v_clip, reactions, peak, output_dir, prefix,
            facecam_size, facecam_position,
        )
        if fc_clip:
            v_clip = fc_clip  # Use the facecam version

    # ── Step 5: Hook + CTA overlay ────────────────────────────────
    hook = generate_hook_text(
        peak_emotion=peak.peak_emotion,
        peak_score=peak.score,
        comment_count=getattr(peak, "comment_count", 0),
        replay_intensity=getattr(peak, "replay_intensity", 0),
    )

    final_clip = output_dir / f"{prefix}_final.mp4"
    final = _add_text_overlays(v_clip, final_clip, hook_text=hook,
                                cta_text=f"{character_name} Reacts on YouTube")
    if not final:
        final_clip = v_clip  # Use without text if overlay fails

    # ── Step 6: Generate captions ─────────────────────────────────
    caption_yt = generate_caption(
        "youtube_shorts", source_video_title, character_name,
        peak.peak_emotion, hook, niche, source_video_id,
        custom_hashtags, llm_provider,
    )
    caption_tt = generate_caption(
        "tiktok", source_video_title, character_name,
        peak.peak_emotion, hook, niche, source_video_id,
        custom_hashtags, llm_provider,
    )

    # Get duration
    duration = _get_duration(final_clip)

    tags = list(custom_hashtags or [])
    tags.extend(["shorts", "fyp", "viral", niche])

    log.info("Composed Short: %s (%.1fs, %s)", final_clip.name, duration, hook)
    return ComposedShort(
        clip_path=final_clip,
        source_video_id=source_video_id,
        source_video_title=source_video_title,
        peak_start=peak.start_seconds,
        peak_score=peak.score,
        peak_emotion=peak.peak_emotion,
        hook_text=hook,
        caption_yt=caption_yt,
        caption_tt=caption_tt,
        hashtags=tags,
        duration_seconds=duration,
    )


def _add_facecam_to_vertical(
    vertical_clip: Path,
    reactions: list[dict],
    peak,
    output_dir: Path,
    prefix: str,
    facecam_size: int = 300,
    position: str = "bottom-left",
) -> Optional[Path]:
    """Render dynamic facecam for the clip's time range and overlay on vertical video."""
    try:
        from peanut_reacts.character.dynamic_facecam import (
            build_facecam_timeline, render_dynamic_facecam,
        )

        # Filter reactions to just this clip's time range
        clip_start = peak.start_seconds - 2
        clip_end = peak.end_seconds + 1
        clip_reactions = [
            {**r, "start": float(r.get("start", 0)) - clip_start}
            for r in reactions
            if clip_start <= float(r.get("start", 0)) <= clip_end
        ]

        if not clip_reactions:
            # Use the peak's own emotion as a single reaction
            clip_reactions = [{
                "start": 0.5,
                "text": "!",
                "emotion": peak.peak_emotion or "excited",
                "intensity": 2.5,
            }]

        clip_duration = _get_duration(vertical_clip)
        timeline = build_facecam_timeline(clip_reactions, clip_duration)
        if not timeline:
            return None

        fc_path = output_dir / f"{prefix}_fc.mp4"
        fc = render_dynamic_facecam(timeline, fc_path, size=facecam_size)
        if not fc:
            return None

        # Overlay on vertical clip with chroma key
        output = output_dir / f"{prefix}_vfc.mp4"
        margin = 30
        pos_map = {
            "bottom-left": f"{margin}:H-h-{margin}",
            "bottom-right": f"W-w-{margin}:H-h-{margin}",
            "top-left": f"{margin}:{margin}",
            "top-right": f"W-w-{margin}:{margin}",
        }
        overlay_xy = pos_map.get(position, pos_map["bottom-left"])

        encoder = "h264_nvenc" if _nvenc_available() else "libx264"
        preset = "fast" if encoder == "h264_nvenc" else "veryfast"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(vertical_clip),
            "-i", str(fc),
            "-filter_complex",
            f"[1:v]chromakey=0x00FF00:0.15:0.08,scale={facecam_size}:{facecam_size}[fc];"
            f"[0:v][fc]overlay={overlay_xy}:shortest=1[out]",
            "-map", "[out]", "-map", "0:a?",
            "-c:v", encoder, "-preset", preset,
            "-c:a", "copy",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)

        # Cleanup intermediate
        fc_path.unlink(missing_ok=True)

        log.info("Added dynamic facecam to vertical: %s", output.name)
        return output
    except Exception as e:
        log.warning("Facecam overlay failed: %s", e)
        return None


def _add_text_overlays(
    source: Path,
    output: Path,
    hook_text: str = "",
    cta_text: str = "",
    hook_duration: float = 2.5,
) -> Optional[Path]:
    """Burn hook text and CTA onto the clip."""
    def escape(t: str) -> str:
        return t.replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")

    duration = _get_duration(source)
    filters = []

    if hook_text:
        h = escape(hook_text)
        filters.append(
            f"drawtext=text='{h}':fontcolor=white:fontsize=64:"
            f"box=1:boxcolor=black@0.7:boxborderw=18:"
            f"x=(w-text_w)/2:y=h*0.12:"
            f"enable='between(t,0,{hook_duration})'"
        )

    if cta_text:
        c = escape(cta_text)
        cta_start = max(0, duration - 3)
        filters.append(
            f"drawtext=text='{c}':fontcolor=yellow:fontsize=48:"
            f"box=1:boxcolor=black@0.8:boxborderw=14:"
            f"x=(w-text_w)/2:y=h*0.88:"
            f"enable='between(t,{cta_start},{duration})'"
        )

    if not filters:
        import shutil
        shutil.copy2(source, output)
        return output

    try:
        encoder = "h264_nvenc" if _nvenc_available() else "libx264"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(source),
            "-vf", ",".join(filters),
            "-c:v", encoder, "-preset", "fast" if "nvenc" in encoder else "veryfast",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output),
        ], check=True, capture_output=True, timeout=300)
        return output
    except Exception as e:
        log.warning("Text overlay failed: %s — using source", e)
        import shutil
        shutil.copy2(source, output)
        return output


def _get_duration(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 30.0


def _nvenc_available() -> bool:
    """Delegate to canonical check that actually tests nvenc at runtime."""
    from peanut_reacts.core.ffmpeg import nvenc_available
    return nvenc_available()
