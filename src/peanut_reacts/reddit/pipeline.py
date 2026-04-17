"""End-to-end Reddit story pipeline orchestrator.

One function renders one finished Short from one DB row:

    story (new)  →  TTS  →  SadTalker  →  vertical composite  →  MP4 (ready)

Designed for cron-driven batch processing: call `render_next_story()` on
a schedule, it picks the top unrendered post and returns its output path
(or None if the queue is empty).

Concerns isolated here:
    * DB state transitions (new → processing → ready | failed)
    * Retries on transient failures (TTS rate-limit, SadTalker OOM)
    * Background clip selection from assets/backgrounds/

Everything else (scraping, processing, TTS engine, renderer, compositor)
lives in its own module. This file only stitches them together.
"""
from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from peanut_reacts.character.elevenlabs_tts import (
    ElevenLabsConfig,
    ElevenLabsTTSEngine,
)
from peanut_reacts.character.tts import EdgeTTSEngine, TTSConfig
from peanut_reacts.character.sadtalker_sync import (
    SadTalkerNoFaceError,
    render_sadtalker_reaction,
    sadtalker_available,
)
from peanut_reacts.compositing.vertical_story import (
    VerticalLayoutConfig,
    compose_vertical_short,
)

from .db import Story, StoryDB
from .story_processor import ProcessedStory, process_story

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FACE_IMAGE = REPO_ROOT / "assets" / "peanut_face" / "realistic_neutral.png"
DEFAULT_BACKGROUND_DIR = REPO_ROOT / "assets" / "backgrounds"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output" / "reddit_shorts"


@dataclass
class PipelineConfig:
    """Tunables for the Reddit-story pipeline."""
    # Input assets
    face_image: Path = field(default_factory=lambda: DEFAULT_FACE_IMAGE)
    background_dir: Path = field(default_factory=lambda: DEFAULT_BACKGROUND_DIR)

    # Output
    output_dir: Path = field(default_factory=lambda: DEFAULT_OUTPUT_DIR)
    target_format: str = "shorts"         # "shorts" | "longform"

    # TTS
    tts_engine: str = "edge"              # "edge" (free) | "elevenlabs" (paid, expressive)
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "TX3LPaxmHKxFdv7VOQHJ"  # Liam — energetic creator
    edge_voice: str = "en-US-GuyNeural"   # energetic male voice, free
    edge_rate: str = "+15%"               # slightly faster pace for TikTok
    tts_emotion: str = "amused"           # only used by ElevenLabs

    # SadTalker
    sadtalker_size: int = 256             # 256 fast, 512 sharper but ~3x slower

    # Compositing
    layout: VerticalLayoutConfig = field(default_factory=VerticalLayoutConfig)

    # Pipeline behavior
    random_background: bool = True        # if False, uses no background (gradient)
    keep_intermediate: bool = False       # keep peanut raw + tts mp3 after success


def _pick_background(cfg: PipelineConfig) -> Optional[Path]:
    """Pick a random bg clip from the library, or None to trigger gradient."""
    if not cfg.random_background:
        return None
    if not cfg.background_dir.exists():
        return None
    clips = [p for p in cfg.background_dir.glob("bg_*.mp4") if p.is_file()]
    return random.choice(clips) if clips else None


def _synthesize_tts(
    processed: ProcessedStory,
    output_path: Path,
    cfg: PipelineConfig,
) -> Path:
    """Run TTS on the processed script. Returns the audio path.

    Engine selection via `cfg.tts_engine`:
      - "edge": free, good quality, no API key needed
      - "elevenlabs": paid, more expressive, needs ELEVENLABS_API_KEY
    """
    if cfg.tts_engine == "edge":
        tts_cfg = TTSConfig(voice=cfg.edge_voice, rate=cfg.edge_rate)
        engine = EdgeTTSEngine(tts_cfg)
        result = engine.synthesize_sync(processed.tts_text, output_path)
        log.info("Edge TTS: %.2fs audio (%d chars)",
                 result.duration, len(processed.tts_text))
        return result.audio_path

    if cfg.tts_engine == "elevenlabs":
        api_key = cfg.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY not set. Export it, pass it via "
                "PipelineConfig(elevenlabs_api_key=...), or switch to "
                "tts_engine='edge' (free)."
            )
        el_cfg = ElevenLabsConfig(api_key=api_key, voice_id=cfg.elevenlabs_voice_id)
        engine = ElevenLabsTTSEngine(el_cfg)
        result = engine.synthesize(
            processed.tts_text, output_path,
            emotion=cfg.tts_emotion,
        )
        log.info("ElevenLabs TTS: %.2fs audio (%d chars)",
                 result.duration, result.chars_used)
        return result.audio_path

    raise ValueError(f"Unknown tts_engine: {cfg.tts_engine!r} "
                     f"(valid: 'edge', 'elevenlabs')")


def render_story(
    story: Story,
    db: StoryDB,
    cfg: Optional[PipelineConfig] = None,
) -> Path:
    """Render one story end-to-end. Commits status transitions to the DB.

    Raises on fatal errors (caller decides retry policy). On success,
    the story row has status='ready' and video_path set.
    """
    cfg = cfg or PipelineConfig()

    if not sadtalker_available():
        raise RuntimeError(
            "SadTalker not available. See memory/reference_sadtalker_install.md"
        )
    if not cfg.face_image.exists():
        raise FileNotFoundError(f"Face image missing: {cfg.face_image}")

    # Per-story workdir keeps artifacts inspectable and restartable
    work_dir = cfg.output_dir / story.id
    work_dir.mkdir(parents=True, exist_ok=True)
    final_path = cfg.output_dir / f"{story.id}.mp4"

    db.set_status(story.id, "processing")

    try:
        # ── Step 1: process story text ──────────────────────────────────
        processed = process_story(story, target_format=cfg.target_format)
        log.info(
            "[%s] %s | %d chars | ~%.0fs TTS | %s",
            story.id, story.subreddit,
            processed.final_chars, processed.estimated_duration,
            "truncated" if processed.truncated else "full",
        )

        # ── Step 2: TTS ─────────────────────────────────────────────────
        tts_path = work_dir / "narration.mp3"
        if not tts_path.exists():
            _synthesize_tts(processed, tts_path, cfg)

        # ── Step 3: SadTalker lip-sync ──────────────────────────────────
        peanut_path = work_dir / "peanut_greenscreen.mp4"
        if not peanut_path.exists():
            try:
                render_sadtalker_reaction(
                    cfg.face_image, tts_path, peanut_path,
                    duration=0,              # unused in sadtalker path
                    canvas_size=512,
                    green_screen=True,
                    emotion=cfg.tts_emotion,
                    size=cfg.sadtalker_size,
                    tail_padding=0.0,        # handled at composite
                )
            except SadTalkerNoFaceError as e:
                db.set_status(story.id, "failed",
                              error_msg=f"sadtalker-no-face: {e}")
                raise

        # ── Step 4: Final vertical composite ────────────────────────────
        bg = _pick_background(cfg)
        compose_vertical_short(
            peanut_video=peanut_path,
            tts_audio=tts_path,
            story_title=story.title,
            subtitle_text=processed.body,
            output_path=final_path,
            background_video=bg,
            config=cfg.layout,
        )

        # ── Step 5: Mark ready, optionally clean up ─────────────────────
        db.set_status(
            story.id, "ready",
            video_path=str(final_path),
        )

        if not cfg.keep_intermediate:
            for p in [tts_path, peanut_path]:
                p.unlink(missing_ok=True)
            try:
                work_dir.rmdir()  # only if empty
            except OSError:
                pass

        log.info("[%s] Done → %s", story.id, final_path)
        return final_path

    except Exception as e:
        # Mark failed if we haven't already set a more specific failure above
        if db.get(story.id) and db.get(story.id).status == "processing":
            db.set_status(story.id, "failed", error_msg=str(e)[:500])
        raise


def render_next_story(
    db: StoryDB,
    cfg: Optional[PipelineConfig] = None,
) -> Optional[Path]:
    """Render the highest-scoring 'new' story. Returns None if queue empty."""
    stories = list(db.iter_by_status("new", limit=1))
    if not stories:
        log.info("No 'new' stories in queue")
        return None
    return render_story(stories[0], db, cfg)


def render_batch(
    db: StoryDB,
    count: int = 5,
    cfg: Optional[PipelineConfig] = None,
) -> list[Path]:
    """Render up to `count` highest-scoring 'new' stories. Continues past
    individual failures (logs + marks failed in DB, moves to next)."""
    cfg = cfg or PipelineConfig()
    outputs: list[Path] = []
    stories = list(db.iter_by_status("new", limit=count))
    log.info("Rendering batch of %d stories ...", len(stories))
    for story in stories:
        try:
            outputs.append(render_story(story, db, cfg))
        except Exception as e:
            log.error("[%s] Failed: %s", story.id, e)
    return outputs
