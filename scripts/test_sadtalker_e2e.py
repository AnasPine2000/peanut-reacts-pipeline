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
import os
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


def _build_speaker_subtitle_filters(
    clip_path: Path, work_dir: Path, hf_token: str,
    video_w: int, video_h: int,
) -> list[str] | None:
    """Transcribe + diarize + build ffmpeg drawtext filters for speakers.

    Caches transcript.json + diarization.json in work_dir so subsequent
    runs skip the slow Whisper (~1min) + pyannote (~3-5min) passes.
    """
    from peanut_reacts.analysis.diarization import (
        DiarizationResult,
        DiarizedSegment,
        align_transcript_with_speakers,
        build_subtitle_filters,
        diarize_video,
    )
    from peanut_reacts.core.transcription import transcribe_video

    transcript_json = work_dir / "transcript.json"
    diarization_json = work_dir / "diarization.json"

    # ── 1. Whisper transcript ──────────────────────────────────────────
    if transcript_json.exists():
        log.info("Using cached transcript: %s", transcript_json.name)
        transcript = json.loads(transcript_json.read_text(encoding="utf-8"))
    else:
        log.info("Transcribing %s with Whisper (base, cuda) ...", clip_path.name)
        t0 = time.time()
        transcript = transcribe_video(clip_path, model_name="base", device="cuda")
        transcript_json.write_text(
            json.dumps(transcript, indent=2), encoding="utf-8",
        )
        log.info("Whisper: %d segments in %.1fs", len(transcript), time.time() - t0)

    # ── 2. Speaker diarization ─────────────────────────────────────────
    if diarization_json.exists():
        log.info("Using cached diarization: %s", diarization_json.name)
        data = json.loads(diarization_json.read_text(encoding="utf-8"))
        segments = [
            DiarizedSegment(start=s["start"], end=s["end"], speaker=s["speaker"],
                           speaker_index=s["speaker_index"])
            for s in data["segments"]
        ]
        diarization = DiarizationResult(
            segments=segments,
            num_speakers=data["num_speakers"],
            speaker_map=data["speaker_map"],
        )
    else:
        log.info("Diarizing %s with pyannote (first run takes 3-5min) ...", clip_path.name)
        t0 = time.time()
        try:
            diarization = diarize_video(clip_path, hf_token=hf_token, work_dir=work_dir)
        except Exception as e:
            log.warning("Diarization failed (continuing without): %s", e)
            return None
        log.info("Diarization: %d speakers in %.1fs",
                 diarization.num_speakers, time.time() - t0)
        # Cache
        diarization_json.write_text(json.dumps({
            "num_speakers": diarization.num_speakers,
            "speaker_map": diarization.speaker_map,
            "segments": [
                {"start": s.start, "end": s.end, "speaker": s.speaker,
                 "speaker_index": s.speaker_index}
                for s in diarization.segments
            ],
        }, indent=2), encoding="utf-8")

    # ── 3. Align transcript with speakers → colored segments ───────────
    colored = align_transcript_with_speakers(transcript, diarization)
    log.info("Aligned: %d colored subtitle segments across %d speakers",
             len(colored), diarization.num_speakers)

    # ── 4. Build ffmpeg drawtext filters ───────────────────────────────
    filters = build_subtitle_filters(
        colored,
        video_width=video_w, video_height=video_h,
    )
    log.info("Built %d ffmpeg subtitle filters", len(filters))
    return filters


def _load_dotenv_if_present() -> None:
    """Load ROOT/.env into os.environ (minimal parser, no external dep)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_dotenv_if_present()
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

    # Define seg_dir early so the idle-loop builder can reference cached segments
    seg_dir = work_dir / "peanut_segments"
    seg_dir.mkdir(exist_ok=True)

    # ── Build idle loop from talking-peanut segments (TikTok VTuber style) ─
    # The peanut is continuously "talking" even between reaction windows —
    # no audible voice (main video's audio dominates during idle), but the
    # mouth keeps moving so the character feels alive rather than frozen.
    # During a real reaction window, the matching SadTalker segment overlays
    # on top via `enable=between(t,start,end)`, so timing still matches.
    #
    # We stitch the first 5 cached SadTalker segments into a ~12s loop —
    # enough variety that the loop doesn't feel canned.
    idle_path = work_dir / "peanut_idle_loop.mp4"
    if not idle_path.exists():
        seed_segments = [seg_dir / f"peanut_{i:03d}.mp4" for i in range(1, 6)]
        seed_segments = [s for s in seed_segments if s.exists()]
        if not seed_segments:
            log.error("No cached peanut segments to build idle loop from")
            return 5
        log.info("Building talking-peanut idle loop from %d seeds ...",
                 len(seed_segments))
        concat_list = work_dir / "idle_concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{s.resolve().as_posix()}'" for s in seed_segments),
            encoding="utf-8",
        )
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list.resolve()),
            "-an",                        # strip audio — idle is silent
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            str(idle_path.resolve()),
        ], check=True, capture_output=True, timeout=120)
        concat_list.unlink(missing_ok=True)

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

    # ── Color-coded speaker subtitles for the Sidemen source audio ──────
    # (Transcribe → diarize → align → build ffmpeg drawtext filters.)
    # Cached to disk so reruns skip the slow Whisper+pyannote passes.
    speaker_filters = None
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    if hf_token:
        speaker_filters = _build_speaker_subtitle_filters(
            clip_path, work_dir, hf_token, video_w, video_h,
        )
    else:
        log.warning("HF_TOKEN not set — skipping diarized subtitles. "
                    "Add HF_TOKEN to .env for color-coded speakers.")

    _final_composite(
        clip_path, peanut_segments, reaction_audio, out_path,
        idle_loop_path=idle_path,
        layout=layout,
        speaker_subtitle_filters=speaker_filters,
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
