"""PNGtuber-style narrator avatar — amplitude-driven 2-state character.

The cheapest character format that still reads as "alive": a character
with exactly two images (mouth-closed, mouth-open) that swap based on
whether the narrator is speaking. Tens of thousands of streamers/
creators use exactly this. Zero GPU, zero per-render cost, zero
external API. Builds in one ffmpeg pass.

How the mouth is driven:
  ffmpeg's `silencedetect` filter emits silence_start / silence_end
  events for an audio track. The gaps BETWEEN silences are speech —
  that's when the mouth is open. We parse those events, invert them
  into speech windows, and feed them to an ffmpeg `overlay ... enable`
  expression. The open-mouth sprite is overlaid only during speech;
  the closed-mouth sprite is the always-on base layer.

Why not full lip-sync (Rhubarb / Hedra / Wan):
  v1 is a deliberate, bounded experiment. If the 2-state avatar
  improves channel retention, THEN we invest in 9-viseme Rhubarb
  sync. If it doesn't, we've spent one week, not a month. Don't
  build v2 before v1's data is in.

Transparent PNG vs chroma key:
  If the sprite PNGs have a real alpha channel, ffmpeg's overlay
  respects it directly — cleanest. If they have a solid-color
  background (magenta recommended for human characters), set
  chroma_color and we key it out first.

Usage:
    from peanut_reacts.character.pngtuber import PngTuberStyle, add_narrator_avatar
    style = PngTuberStyle(
        closed_sprite=Path("assets/narrator/narrator_closed.png"),
        open_sprite=Path("assets/narrator/narrator_open.png"),
        position="bottom-left",
        size_frac=0.28,
    )
    out = add_narrator_avatar(main_video, narration_audio, style, output_path)
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# Corner placement presets. Values are ffmpeg overlay x:y expressions
# where W/H = main video dims, w/h = scaled avatar dims, M = margin.
_POSITIONS = {
    "bottom-left":  ("{M}", "H-h-{M}"),
    "bottom-right": ("W-w-{M}", "H-h-{M}"),
    "top-left":     ("{M}", "{M}"),
    "top-right":    ("W-w-{M}", "{M}"),
}


@dataclass
class PngTuberStyle:
    """Branding + placement spec for the narrator avatar."""
    closed_sprite: Path                 # mouth-closed PNG
    open_sprite: Path                   # mouth-open PNG
    position: str = "bottom-left"       # corner — see _POSITIONS
    size_frac: float = 0.28             # avatar width as fraction of frame width
    margin_px: int = 40                 # gap from the frame edge
    # If the sprites have a solid-color background instead of real
    # alpha, set this to the hex (e.g. "0xFF00FF" magenta) to key it.
    # Empty string = sprites have a real alpha channel, key nothing.
    chroma_color: str = ""
    chroma_similarity: float = 0.16     # chromakey tolerance
    chroma_blend: float = 0.10
    # silencedetect tuning. noise: dB threshold below which audio
    # counts as silence. min_silence: a quiet patch shorter than this
    # is NOT treated as silence (debounce — keeps the mouth from
    # snapping shut on every micro-pause between words).
    silence_noise_db: float = -32.0
    min_silence_s: float = 0.18


def _probe_duration(path: Path) -> float:
    """Return media duration in seconds, or 0 on failure."""
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
    """Return (start, end) windows during which the narrator is SPEAKING.

    Implementation: run ffmpeg's silencedetect, parse the silence
    intervals from stderr, then invert — the gaps between silences are
    speech. The mouth is open during these windows.

    A window list of [] (no speech detected) means either the audio is
    entirely silent OR detection failed; the caller should treat that
    as "mouth stays closed the whole time" and probably skip the
    avatar overlay rather than show a never-talking character.
    """
    duration = _probe_duration(audio_path)
    if duration <= 0:
        log.warning("[PNGTUBER] could not probe audio duration: %s", audio_path)
        return []

    try:
        # silencedetect writes to stderr. We don't need the (null) output.
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

    # Pair silences into intervals. silencedetect emits start then end
    # in order; a trailing start with no end means silence runs to EOF.
    silences: list[tuple[float, float]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        if e > s:
            silences.append((s, e))
    silences.sort()

    # Invert: speech windows are the gaps between silences.
    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in silences:
        if s > cursor + 0.02:        # ignore sub-frame slivers
            speech.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration - 0.02:
        speech.append((cursor, duration))

    log.info("[PNGTUBER] %d speech windows over %.1fs (%.0f%% talking)",
             len(speech), duration,
             100 * sum(e - s for s, e in speech) / duration if duration else 0)
    return speech


def _build_enable_expr(windows: list[tuple[float, float]]) -> str:
    """Build an ffmpeg `enable` expression that is true during any
    speech window. Form: between(t,a,b)+between(t,c,d)+...

    ffmpeg accepts very large expressions (the string can be tens of KB
    for a long video) — verified working with ~1500 terms. If a video
    is so long this becomes a problem, the fix is to pre-render the
    avatar as its own track; not needed at our 6-10 minute lengths.
    """
    if not windows:
        return "0"   # never enabled
    terms = [f"between(t,{s:.3f},{e:.3f})" for s, e in windows]
    return "+".join(terms)


def add_narrator_avatar(
    main_video: Path,
    narration_audio: Path,
    style: PngTuberStyle,
    output: Path,
) -> Optional[Path]:
    """Composite a 2-state PNGtuber avatar onto the main video.

    One ffmpeg pass:
      input 0  main video
      input 1  closed-mouth sprite (looped still)
      input 2  open-mouth sprite (looped still)

    filter graph:
      - scale both sprites to size_frac * main-width
      - optional chromakey if the sprites have a solid bg
      - overlay closed sprite at the corner (always on) -> base
      - overlay open sprite at the same corner, enable=speech windows

    Returns the output path, or None on failure. The caller (pipeline)
    treats None as "ship the video without the avatar" — the avatar is
    a bonus layer, never load-bearing.
    """
    for p in (main_video, narration_audio, style.closed_sprite, style.open_sprite):
        if not Path(p).exists():
            log.warning("[PNGTUBER] missing input: %s", p)
            return None

    if style.position not in _POSITIONS:
        log.warning("[PNGTUBER] unknown position %r, using bottom-left", style.position)
        pos_key = "bottom-left"
    else:
        pos_key = style.position

    windows = analyze_speech_windows(
        narration_audio,
        noise_db=style.silence_noise_db,
        min_silence_s=style.min_silence_s,
    )
    if not windows:
        log.warning("[PNGTUBER] no speech detected — skipping avatar overlay")
        return None

    output.parent.mkdir(parents=True, exist_ok=True)

    # Probe main video width so size_frac resolves to real pixels.
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

    x_expr, y_expr = _POSITIONS[pos_key]
    x_expr = x_expr.format(M=style.margin_px)
    y_expr = y_expr.format(M=style.margin_px)

    enable_expr = _build_enable_expr(windows)

    # ── Build the filter graph ────────────────────────────────────
    # Sprite prep: scale to avatar_w, preserve aspect. Optional chroma.
    def _sprite_chain(label_in: str, label_out: str) -> str:
        chain = f"[{label_in}]scale={avatar_w}:-1"
        if style.chroma_color:
            chain += (
                f",chromakey={style.chroma_color}:"
                f"{style.chroma_similarity}:{style.chroma_blend}"
            )
        chain += f"[{label_out}]"
        return chain

    filter_parts = [
        _sprite_chain("1:v", "closed"),
        _sprite_chain("2:v", "open"),
        # Always-on closed sprite as the base mouth state
        f"[0:v][closed]overlay={x_expr}:{y_expr}[base]",
        # Open sprite layered on top, visible only during speech windows
        f"[base][open]overlay={x_expr}:{y_expr}:"
        f"enable='{enable_expr}'[vout]",
    ]
    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(main_video),
        "-loop", "1", "-i", str(style.closed_sprite),
        "-loop", "1", "-i", str(style.open_sprite),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a?",                 # carry the main video's audio as-is
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-shortest",
        str(output),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=2400)
    except subprocess.CalledProcessError as e:
        log.error(
            "[PNGTUBER] composite failed: %s",
            (e.stderr or b"").decode("utf-8", errors="replace")[-600:],
        )
        return None
    except Exception as e:
        log.error("[PNGTUBER] composite crashed: %s", e)
        return None

    if not output.exists():
        return None
    log.info("[PNGTUBER] avatar composited: %s (%d MB)",
             output.name, output.stat().st_size // (1024 * 1024))
    return output
