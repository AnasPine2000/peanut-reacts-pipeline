#!/usr/bin/env python3
"""
End-to-end integration test for the SadTalker renderer branch.

Reuses the cached reactions.json + TTS MP3s from production/ai_reaction/ so
this doesn't re-run the LLM or TTS APIs. Drives a short clip (default 30s)
from the source video through the full compositing path with
renderer="sadtalker":

    1. SadTalker segment renders (per reaction)
    2. Reaction audio track built (_build_reaction_audio)
    3. Final composite with facecam layout (_final_composite)

Success criteria:
    - Pipeline completes without exceptions
    - Output MP4 exists and plays
    - Peanut visible only during reaction segments, idle between
    - Lip-sync matches the TTS audio

Usage:
    python scripts/test_sadtalker_e2e.py
    python scripts/test_sadtalker_e2e.py --duration 60 --max-reactions 5
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

# Ensure src/ is importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from peanut_reacts.character.cartoon_renderer import (
    CartoonRendererConfig,
    render_idle_loop,
)
from peanut_reacts.character.reaction_generator import ReactionLine
from peanut_reacts.character.sadtalker_sync import (
    SadTalkerNoFaceError,
    render_sadtalker_reaction,
    sadtalker_available,
)
from peanut_reacts.character.tts import TTSResult
from peanut_reacts.compositing.layout import LayoutConfig
from peanut_reacts.compositing.reaction_video import (
    _build_reaction_audio,
    _final_composite,
)
from peanut_reacts.core.ffmpeg import get_video_dimensions, get_video_duration

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sadtalker_e2e")

PROD_DIR = Path(r"C:\Users\anasm\Videos\4K Video Downloader+\production\ai_reaction")
SRC_CLIP = PROD_DIR / "source_clip.mp4"
REACTIONS_JSON = PROD_DIR / "reactions.json"
TTS_DIR = PROD_DIR / "tts"
FACE_IMAGE = ROOT / "assets" / "peanut_face" / "realistic_neutral.png"


def _probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return float(r.stdout.strip())


def _trim_video(src: Path, dst: Path, duration: float) -> Path:
    """Trim the source video to the first `duration` seconds."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-i", str(src), "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        str(dst),
    ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return dst


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--duration", type=float, default=30.0,
                   help="Seconds of source video to use (default: 30)")
    p.add_argument("--max-reactions", type=int, default=3,
                   help="Cap on reactions to render (default: 3)")
    p.add_argument("--size", type=int, default=256, choices=[256, 512],
                   help="SadTalker render size (default: 256)")
    p.add_argument("--out", default=None, help="Output MP4 path")
    args = p.parse_args()

    # ── Sanity checks ──────────────────────────────────────────────────
    if not sadtalker_available():
        log.error("SadTalker not available. Install per memory/reference_sadtalker_install.md")
        return 2

    for need in [SRC_CLIP, REACTIONS_JSON, TTS_DIR, FACE_IMAGE]:
        if not need.exists():
            log.error("Missing required asset: %s", need)
            return 2

    # ── Set up paths ───────────────────────────────────────────────────
    work_dir = Path(r"C:\Users\anasm\ai_models\test_renders\e2e_pipeline")
    work_dir.mkdir(parents=True, exist_ok=True)

    out_path = Path(args.out) if args.out else work_dir / "sadtalker_e2e_output.mp4"

    # ── Trim source video ──────────────────────────────────────────────
    clip_path = work_dir / f"source_trim_{int(args.duration)}s.mp4"
    if not clip_path.exists() or _probe_duration(clip_path) < args.duration - 0.5:
        log.info("Trimming source to %.1fs ...", args.duration)
        _trim_video(SRC_CLIP, clip_path, args.duration)
    video_duration = _probe_duration(clip_path)
    video_w, video_h = get_video_dimensions(clip_path)
    log.info("Source clip: %.2fs %dx%d", video_duration, video_w, video_h)

    # ── Load reactions + cap to clip window ────────────────────────────
    all_reactions = json.loads(REACTIONS_JSON.read_text(encoding="utf-8"))
    # Keep only reactions whose start is inside the clip window and whose
    # cached TTS MP3 exists.
    filtered: list[tuple[int, dict]] = []
    for i, r in enumerate(all_reactions):
        if r["start"] >= video_duration - 3:
            continue
        mp3 = TTS_DIR / f"r_{i:04d}.mp3"
        if not mp3.exists():
            continue
        filtered.append((i, r))
        if len(filtered) >= args.max_reactions:
            break

    if not filtered:
        log.error("No cached reactions found in the first %.1fs", args.duration)
        return 3

    log.info("Selected %d cached reaction(s):", len(filtered))
    for i, r in filtered:
        log.info("  [%d] t=%.1fs emotion=%s text=%r", i, r["start"], r["emotion"], r["text"])

    # ── Build ReactionLine + TTSResult pairs ──────────────────────────
    tts_pairs: list[tuple[ReactionLine, TTSResult]] = []
    for i, r in filtered:
        mp3 = TTS_DIR / f"r_{i:04d}.mp3"
        dur = _probe_duration(mp3)
        start = float(r["start"])
        end = min(start + dur, video_duration)
        line = ReactionLine(
            start=start, end=end,
            text=r["text"], emotion=r["emotion"],
        )
        tts_pairs.append((line, TTSResult(
            audio_path=mp3, duration=dur, word_timings=[],
        )))

    # ── Render idle loop from the SAME realistic peanut ────────────────
    # Don't fall back to the cartoon renderer — that would swap the character
    # between reactions, breaking visual continuity. Instead, loop the
    # realistic peanut PNG with subtle ken-burns on a green screen so the
    # downstream compositor can chroma-key it identically to the speaking
    # segments. Result: the same character is visible the entire time.
    idle_path = work_dir / "peanut_idle_loop.mp4"
    if not idle_path.exists():
        log.info("Rendering realistic-peanut idle loop (4s, static with gentle zoom) ...")
        subprocess.run([
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", "25", "-t", "4",
            "-i", str(FACE_IMAGE.resolve()),
            # Scale to 512, pad to square with green bg, slow zoom for liveliness
            "-vf",
            "scale=512:512:force_original_aspect_ratio=decrease,"
            "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00FF00,"
            "zoompan=z='min(zoom+0.0008,1.08)':x='iw/2-(iw/zoom/2)'"
            ":y='ih/2-(ih/zoom/2)':d=100:s=512x512:fps=25",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            str(idle_path.resolve()),
        ], check=True, capture_output=True, timeout=60)

    # ── Render SadTalker segments ──────────────────────────────────────
    peanut_segments: list[tuple[ReactionLine, Path]] = []
    seg_dir = work_dir / "peanut_segments"
    seg_dir.mkdir(exist_ok=True)

    t_total = 0.0
    for idx, (line, tts_result) in enumerate(tts_pairs):
        seg_path = seg_dir / f"peanut_{idx + 1:03d}.mp4"
        if seg_path.exists():
            log.info("[%d/%d] cached segment: %s", idx + 1, len(tts_pairs), seg_path.name)
            peanut_segments.append((line, seg_path))
            continue

        t0 = time.time()
        log.info("[%d/%d] rendering SadTalker (%.2fs, %s) ...",
                 idx + 1, len(tts_pairs), tts_result.duration, line.emotion)
        try:
            render_sadtalker_reaction(
                FACE_IMAGE, tts_result.audio_path, seg_path,
                duration=tts_result.duration,
                canvas_size=512, green_screen=True,
                emotion=line.emotion, size=args.size, tail_padding=0.3,
            )
        except SadTalkerNoFaceError as e:
            log.error("[%d/%d] face detect failed: %s", idx + 1, len(tts_pairs), e)
            return 4
        dt = time.time() - t0
        t_total += dt
        log.info("[%d/%d] done in %.1fs (%.2fx realtime)",
                 idx + 1, len(tts_pairs), dt, dt / max(tts_result.duration, 0.1))
        peanut_segments.append((line, seg_path))

    log.info("Total SadTalker time: %.1fs for %.1fs of audio (%.2fx realtime)",
             t_total, sum(t.duration for _, t in tts_pairs),
             t_total / max(sum(t.duration for _, t in tts_pairs), 0.1))

    # ── Build reaction audio track ─────────────────────────────────────
    reaction_audio = work_dir / "reaction_audio.aac"
    log.info("Building reaction audio track ...")
    _build_reaction_audio(tts_pairs, video_duration, reaction_audio)

    # ── Final composite ────────────────────────────────────────────────
    log.info("Final composite → %s", out_path)
    # Bigger peanut in TOP_RIGHT so lip-sync is clearly visible at a glance
    from peanut_reacts.compositing.layout import FacecamPosition
    layout = LayoutConfig(
        facecam_position=FacecamPosition.TOP_RIGHT,
        facecam_scale=0.35,                 # 35% of video width (~672px on 1920)
        facecam_margin=24,
        facecam_border_width=4,
        facecam_corner_radius=12,
    )
    _final_composite(
        clip_path, peanut_segments, reaction_audio, out_path,
        idle_loop_path=idle_path,
        layout=layout,
        speaker_subtitle_filters=None,
        video_width=video_w,
        video_height=video_h,
        original_duck=0.15,
        reaction_volume=2.5,
    )

    if not out_path.exists():
        log.error("Final composite produced no file!")
        return 5

    log.info("SUCCESS: %s (%.1f MB)", out_path, out_path.stat().st_size / 1e6)
    log.info("Open it: start %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
