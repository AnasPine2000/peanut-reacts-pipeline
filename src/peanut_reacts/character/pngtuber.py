"""PNGtuber narrator avatar — dynamic, expression-reactive.

A character that does two things at once:

  1. Lip-flap — mouth opens/closes with the narration audio
     (silencedetect-driven, the always-on baseline).
  2. React — holds an expression sprite (shocked / laughing / angry
     / sad) for a few seconds on each emotional story beat.

So the avatar isn't a static head flapping its mouth — it reacts to
what it's narrating, beat by beat.

Sprite set (a directory):
  narrator_closed.png    REQUIRED  neutral, mouth closed
  narrator_open.png      REQUIRED  neutral, mouth open
  narrator_shocked.png   optional  held on 'shocked' beats
  narrator_laughing.png  optional  held on 'laughing' beats
  narrator_angry.png     optional  held on 'angry' beats
  narrator_sad.png       optional  held on 'sad' beats

Missing optional sprites simply fall back to neutral — the minimum
viable set is still just the two neutral images. Fully backward
compatible with the original 2-state avatar.

Compositing (single ffmpeg pass):
  layer 0  main video
  layer 1  narrator_closed         always on  (base mouth state)
  layer 2  narrator_open           enabled during speech windows
  layer 3+ each expression sprite  enabled during its beat windows,
                                    on top — covers the neutral layers

Emotion timeline: a list of EmotionBeat(start, end, emotion). The
reddit pipeline derives it from a DeepSeek emotion pass over the
script; see reddit_pipeline.generate_emotion_timeline.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


_POSITIONS = {
    "bottom-left":  ("{M}", "H-h-{M}"),
    "bottom-right": ("W-w-{M}", "H-h-{M}"),
    "top-left":     ("{M}", "{M}"),
    "top-right":    ("W-w-{M}", "{M}"),
}

# emotion name -> sprite filename within the sprite directory
EMOTION_SPRITES = {
    "shocked":  "narrator_shocked.png",
    "laughing": "narrator_laughing.png",
    "angry":    "narrator_angry.png",
    "sad":      "narrator_sad.png",
}

# How long an expression is held when a beat fires (seconds).
DEFAULT_BEAT_HOLD_S = 2.8


@dataclass
class EmotionBeat:
    """One reaction beat: hold `emotion` from start_s to end_s."""
    start_s: float
    end_s: float
    emotion: str


@dataclass
class PngTuberStyle:
    """Placement + behaviour spec for the narrator avatar."""
    sprite_dir: Path                     # dir holding narrator_*.png
    position: str = "bottom-left"
    size_frac: float = 0.26              # avatar width / frame width
    margin_px: int = 40
    # Solid-colour key-out for sprites that lack real alpha. Empty =
    # sprites have a proper alpha channel (the normal case).
    chroma_color: str = ""
    chroma_similarity: float = 0.16
    chroma_blend: float = 0.10
    # silencedetect tuning for the mouth-flap
    silence_noise_db: float = -32.0
    min_silence_s: float = 0.18


def _probe_duration(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip()) if r.stdout.strip() else 0.0
    except Exception:
        return 0.0


def analyze_speech_windows(
    audio_path: Path,
    noise_db: float = -32.0,
    min_silence_s: float = 0.18,
) -> list[tuple[float, float]]:
    """Return (start, end) windows where the narrator is SPEAKING.

    ffmpeg silencedetect emits the silence intervals; speech is the
    gaps between them. Empty list = no speech detected (treat as
    "mouth stays closed", caller should skip the avatar)."""
    duration = _probe_duration(audio_path)
    if duration <= 0:
        log.warning("[PNGTUBER] could not probe audio: %s", audio_path)
        return []
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(audio_path),
             "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        log.warning("[PNGTUBER] silencedetect failed: %s", e)
        return []

    stderr = proc.stderr or ""
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", stderr)]

    silences: list[tuple[float, float]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        if e > s:
            silences.append((s, e))
    silences.sort()

    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in silences:
        if s > cursor + 0.02:
            speech.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration - 0.02:
        speech.append((cursor, duration))

    log.info("[PNGTUBER] %d speech windows over %.1fs (%.0f%% talking)",
             len(speech), duration,
             100 * sum(e - s for s, e in speech) / duration if duration else 0)
    return speech


def _enable_expr(windows: list[tuple[float, float]]) -> str:
    """ffmpeg `enable` expression true during any listed window."""
    if not windows:
        return "0"
    return "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in windows)


def _resolve_sprites(sprite_dir: Path) -> dict:
    """Find the available sprites. Returns {'closed':path,'open':path,
    'shocked':path,...}. closed+open are required; the rest optional."""
    sprite_dir = Path(sprite_dir).expanduser()
    found: dict[str, Path] = {}
    closed = sprite_dir / "narrator_closed.png"
    opened = sprite_dir / "narrator_open.png"
    if closed.exists():
        found["closed"] = closed
    if opened.exists():
        found["open"] = opened
    for emotion, fname in EMOTION_SPRITES.items():
        p = sprite_dir / fname
        if p.exists():
            found[emotion] = p
    return found


def add_narrator_avatar(
    main_video: Path,
    narration_audio: Path,
    style: PngTuberStyle,
    output: Path,
    emotion_timeline: Optional[list[EmotionBeat]] = None,
) -> Optional[Path]:
    """Composite the dynamic narrator avatar onto the main video.

    Single ffmpeg pass. Layers, bottom to top:
      - main video
      - neutral closed sprite (always)
      - neutral open sprite (speech windows)
      - each expression sprite (its beat windows) — on top, so a
        held expression covers the flapping neutral mouth

    emotion_timeline is optional; without it the avatar is the plain
    2-state lip-flap. Returns None on any failure so the caller ships
    the video without the avatar."""
    main_video = Path(main_video)
    narration_audio = Path(narration_audio)
    if not main_video.exists() or not narration_audio.exists():
        log.warning("[PNGTUBER] missing main video or audio")
        return None

    sprites = _resolve_sprites(style.sprite_dir)
    if "closed" not in sprites or "open" not in sprites:
        log.warning("[PNGTUBER] need narrator_closed.png + narrator_open.png "
                    "in %s — skipping avatar", style.sprite_dir)
        return None

    speech = analyze_speech_windows(
        narration_audio,
        noise_db=style.silence_noise_db,
        min_silence_s=style.min_silence_s,
    )
    if not speech:
        log.warning("[PNGTUBER] no speech detected — skipping avatar")
        return None

    # Group emotion beats by emotion, keeping only emotions we have a
    # sprite for. Unknown / spriteless emotions just fall back to
    # neutral (no layer added).
    beats_by_emotion: dict[str, list[tuple[float, float]]] = {}
    if emotion_timeline:
        for b in emotion_timeline:
            if b.emotion in sprites and b.end_s > b.start_s:
                beats_by_emotion.setdefault(b.emotion, []).append(
                    (b.start_s, b.end_s)
                )

    output.parent.mkdir(parents=True, exist_ok=True)

    # Probe main width so size_frac resolves to pixels
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width", "-of", "csv=p=0", str(main_video)],
            capture_output=True, text=True, timeout=15,
        )
        main_w = int(r.stdout.strip()) if r.stdout.strip() else 1920
    except Exception:
        main_w = 1920
    avatar_w = max(120, int(main_w * style.size_frac))

    pos_key = style.position if style.position in _POSITIONS else "bottom-left"
    x_expr, y_expr = (e.format(M=style.margin_px) for e in _POSITIONS[pos_key])

    # ── Assemble the ffmpeg inputs + filter graph ─────────────────
    # Input order: main video, then every sprite we'll use. We build
    # the sprite list deterministically so input indices are known.
    sprite_order = ["closed", "open"] + [
        e for e in EMOTION_SPRITES if e in beats_by_emotion
    ]
    inputs = ["-i", str(main_video)]
    for key in sprite_order:
        inputs.extend(["-loop", "1", "-i", str(sprites[key])])

    def _prep(idx: int, label: str) -> str:
        """Scale (+ optional chromakey) sprite at input idx -> [label]."""
        chain = f"[{idx}:v]scale={avatar_w}:-1"
        if style.chroma_color:
            chain += (f",chromakey={style.chroma_color}:"
                      f"{style.chroma_similarity}:{style.chroma_blend}")
        return chain + f"[{label}]"

    parts = []
    # Sprite prep (input index = position in sprite_order + 1)
    for i, key in enumerate(sprite_order):
        parts.append(_prep(i + 1, f"s_{key}"))

    # Base: main + closed (always on)
    parts.append(f"[0:v][s_closed]overlay={x_expr}:{y_expr}[L0]")
    # Neutral open during speech
    parts.append(
        f"[L0][s_open]overlay={x_expr}:{y_expr}:"
        f"enable='{_enable_expr(speech)}'[L1]"
    )
    # Expression layers on top, each enabled during its beats
    prev = "L1"
    emotion_keys = [e for e in EMOTION_SPRITES if e in beats_by_emotion]
    for i, emotion in enumerate(emotion_keys):
        is_last = (i == len(emotion_keys) - 1)
        out_label = "vout" if is_last else f"L{2 + i}"
        parts.append(
            f"[{prev}][s_{emotion}]overlay={x_expr}:{y_expr}:"
            f"enable='{_enable_expr(beats_by_emotion[emotion])}'[{out_label}]"
        )
        prev = out_label
    if not emotion_keys:
        # No expression layers — rename L1 to vout via a null filter
        parts.append(f"[L1]null[vout]")

    filter_complex = ";".join(parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-shortest",
        str(output),
    ]

    log.info("[PNGTUBER] compositing — %d expression beats across %s",
             sum(len(v) for v in beats_by_emotion.values()),
             list(beats_by_emotion.keys()) or "none")
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=2400)
    except subprocess.CalledProcessError as e:
        log.error("[PNGTUBER] composite failed: %s",
                  (e.stderr or b"").decode("utf-8", errors="replace")[-600:])
        return None
    except Exception as e:
        log.error("[PNGTUBER] composite crashed: %s", e)
        return None

    if not output.exists():
        return None
    log.info("[PNGTUBER] avatar composited: %s (%d MB)",
             output.name, output.stat().st_size // (1024 * 1024))
    return output
