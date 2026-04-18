"""9:16 vertical compositor for TikTok / YouTube Shorts Reddit stories.

Takes:
  - peanut_video:      SadTalker output, green-screen, 512x512 (or any square)
  - tts_audio:         the full narration as WAV/MP3
  - story_title:       shown as a title card for the first ~2.5s
  - subtitle_text:     full story text, chunked into ~5-word lines for subs
  - background_video:  optional satisfying clip; falls back to a generated
                       animated gradient if absent

Outputs an MP4 sized 1080x1920 (standard TikTok/Shorts canvas), layout:

    ┌─────────────────┐
    │   TITLE CARD    │  (first ~2.5s, fades out)
    │                 │
    │     PEANUT      │  top half, chroma-keyed onto background
    │                 │
    │                 │
    ├─────────────────┤
    │                 │
    │   BACKGROUND    │  bottom half — looped bg clip or gradient
    │                 │
    │                 │
    │   [SUBTITLE]    │  burned-in text, chunked by duration
    │                 │
    └─────────────────┘

Design goals:
  - One ffmpeg pass via filter_complex, no intermediate files
  - NVENC encoding when available (falls back to libx264)
  - Graceful degradation: missing background video → generated gradient;
    missing font → falls back to ffmpeg's default
  - Stateless: no DB access, just takes inputs and writes a file
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────────

@dataclass
class VerticalLayoutConfig:
    """Layout for a 9:16 Reddit-story Short."""
    canvas_w: int = 1080
    canvas_h: int = 1920
    # Peanut takes the top half; background fills the bottom
    split_y: int = 960               # pixel row where peanut ends / bg begins
    peanut_scale: float = 1.2        # scales peanut to comfortably fill the top
    title_duration: float = 2.5      # seconds — title card visible at start
    title_fontsize: int = 60
    subtitle_fontsize: int = 54
    subtitle_chunk_words: int = 5    # words per burned-in line
    bg_darken: float = 0.6           # 0..1; lower = darker, improves sub legibility
    fps: int = 30
    crf: int = 20                    # only used for libx264 fallback


# ── Helpers ──────────────────────────────────────────────────────────────

def _nvenc_available() -> bool:
    from peanut_reacts.core.ffmpeg import nvenc_available
    return nvenc_available()


def _probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return float(r.stdout.strip())


def _escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter.

    drawtext is very finicky — colons, backslashes, single quotes and
    percent signs all need escaping. Newlines must become literal \\n.
    """
    # Order matters: escape backslashes first
    t = text.replace("\\", "\\\\")
    t = t.replace("'", "\u2019")  # swap apostrophes for curly quote (avoids shell quoting hell)
    t = t.replace(":", "\\:")
    t = t.replace("%", "\\%")
    t = t.replace("\n", " ")
    return t


def _chunk_for_subtitles(text: str, duration: float, words_per_chunk: int
                        ) -> list[tuple[float, float, str]]:
    """Split text into (start, end, chunk_text) tuples for burned-in subs.

    Evenly distributes chunks across `duration`. Not word-level accurate
    but readable and matches the pacing most viral readers use. For
    word-level karaoke, swap in Whisper word-timing later.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[list[str]] = []
    for i in range(0, len(words), words_per_chunk):
        chunks.append(words[i:i + words_per_chunk])

    per_chunk = duration / len(chunks)
    out: list[tuple[float, float, str]] = []
    for idx, chunk in enumerate(chunks):
        start = idx * per_chunk
        end = min(start + per_chunk, duration)
        out.append((start, end, " ".join(chunk)))
    return out


_CACHE_DIR = Path.home() / ".cache" / "peanut_reacts" / "vertical_bg"


def _ensure_gradient_png(w: int, h: int) -> Path:
    """Generate (and cache) a purple→pink gradient PNG at the given size."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"gradient_{w}x{h}.png"
    if path.exists():
        return path

    from PIL import Image
    img = Image.new("RGB", (w, h))
    pixels = img.load()
    # Vertical gradient: deep purple at top → pink at bottom
    c_top = (26, 0, 51)        # #1A0033
    c_mid = (107, 31, 176)     # #6B1FB0
    c_bot = (255, 77, 143)     # #FF4D8F
    for y in range(h):
        t = y / max(h - 1, 1)
        if t < 0.5:
            u = t * 2
            r = int(c_top[0] + (c_mid[0] - c_top[0]) * u)
            g = int(c_top[1] + (c_mid[1] - c_top[1]) * u)
            b = int(c_top[2] + (c_mid[2] - c_top[2]) * u)
        else:
            u = (t - 0.5) * 2
            r = int(c_mid[0] + (c_bot[0] - c_mid[0]) * u)
            g = int(c_mid[1] + (c_bot[1] - c_mid[1]) * u)
            b = int(c_mid[2] + (c_bot[2] - c_mid[2]) * u)
        for x in range(w):
            pixels[x, y] = (r, g, b)
    img.save(path)
    logger.info("Generated gradient background: %s", path.name)
    return path


def _build_background_filter(
    bg_video: Optional[Path],
    duration: float,
    cfg: VerticalLayoutConfig,
) -> tuple[list[str], str]:
    """Return ([extra -i inputs], filter_chain_producing_bg_labeled_[bg]).

    If no background video is provided, we generate a static gradient PNG
    via PIL and loop it — works on any ffmpeg version. Otherwise loop the
    provided clip for the full story duration.
    """
    if bg_video is None or not bg_video.exists():
        gradient_png = _ensure_gradient_png(cfg.canvas_w, cfg.canvas_h)
        inputs: list[str] = [
            "-loop", "1",
            "-t", f"{duration:.2f}",
            "-i", str(gradient_png),
        ]
        chain = (
            f"[{{bg_idx}}:v]fps={cfg.fps},"
            f"scale={cfg.canvas_w}:{cfg.canvas_h}[bg]"
        )
        return inputs, chain

    # Real background video: loop until our duration is covered, crop to canvas
    inputs = [
        "-stream_loop", "-1",
        "-t", f"{duration:.2f}",
        "-i", str(bg_video),
    ]
    chain = (
        f"[{{bg_idx}}:v]fps={cfg.fps},"
        f"scale={cfg.canvas_w}:{cfg.canvas_h}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={cfg.canvas_w}:{cfg.canvas_h},"
        f"eq=brightness=-{0.5 - cfg.bg_darken/2:.2f}:saturation=1.1[bg]"
    )
    return inputs, chain


def _build_title_drawtext(title: str, cfg: VerticalLayoutConfig) -> str:
    """Drawtext filter snippet for the title card at the top."""
    escaped = _escape_drawtext(title)
    return (
        f"drawtext=text='{escaped}':"
        f"fontcolor=white:fontsize={cfg.title_fontsize}:"
        f"box=1:boxcolor=black@0.7:boxborderw=20:"
        f"x=(w-text_w)/2:y=80:"
        f"enable='between(t,0,{cfg.title_duration})':"
        # Fade out in the last 0.3s of visibility
        f"alpha='if(lt(t,{cfg.title_duration - 0.3}),1,"
        f"max(0,{cfg.title_duration}-t)/0.3)'"
    )


def _build_subtitle_drawtext(
    subtitle_chunks: list[tuple[float, float, str]],
    cfg: VerticalLayoutConfig,
) -> str:
    """Chain multiple drawtext filters, each with its own enable window."""
    snippets: list[str] = []
    for start, end, text in subtitle_chunks:
        escaped = _escape_drawtext(text)
        snippets.append(
            f"drawtext=text='{escaped}':"
            f"fontcolor=white:fontsize={cfg.subtitle_fontsize}:"
            f"box=1:boxcolor=black@0.6:boxborderw=15:"
            f"x=(w-text_w)/2:y=h-300:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
    return ",".join(snippets) if snippets else "null"


# ── Main entry ───────────────────────────────────────────────────────────

def compose_vertical_short(
    peanut_video: Path,
    tts_audio: Path,
    story_title: str,
    subtitle_text: str,
    output_path: Path,
    *,
    background_video: Optional[Path] = None,
    config: Optional[VerticalLayoutConfig] = None,
) -> Path:
    """Composite a peanut reading a Reddit story into a 9:16 vertical Short.

    Single ffmpeg pass, no intermediate files. Encodes with NVENC where
    available, libx264 otherwise.
    """
    cfg = config or VerticalLayoutConfig()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    duration = _probe_duration(tts_audio)
    logger.info("Composing %s: tts=%.2fs peanut=%s bg=%s",
                output_path.name, duration,
                peanut_video.name if peanut_video else "<none>",
                background_video.name if background_video else "<gradient>")

    # Build dynamic inputs — order matters because ffmpeg maps by index.
    # Index 0: peanut video (always)
    # Index 1: tts audio (always)
    # Index 2: background video OR lavfi gradient (always)
    inputs = [
        "-i", str(peanut_video),
        "-i", str(tts_audio),
    ]
    bg_inputs, bg_chain_tmpl = _build_background_filter(background_video, duration, cfg)
    inputs.extend(bg_inputs)
    bg_idx = 2

    # Peanut: chroma-key out the green, scale to fit the top half.
    # Key ordering: ensure rgba upfront so chromakey's alpha output survives
    # the scale downstream. Similarity bumped to 0.25 to catch anti-aliased
    # edges that SadTalker leaves around the character.
    peanut_target_w = int(cfg.canvas_w * cfg.peanut_scale)
    peanut_chain = (
        f"[0:v]fps={cfg.fps},"
        f"format=rgba,"
        f"chromakey=0x00FF00:0.25:0.10,"
        f"scale={peanut_target_w}:-1:flags=lanczos[peanut]"
    )

    # Build the rest of the chain.
    bg_chain = bg_chain_tmpl.format(bg_idx=bg_idx)

    # Final composite: overlay peanut onto background, then draw title + subs.
    subtitle_chunks = _chunk_for_subtitles(
        subtitle_text, duration, cfg.subtitle_chunk_words,
    )
    title_dt = _build_title_drawtext(story_title, cfg)
    subs_dt = _build_subtitle_drawtext(subtitle_chunks, cfg)

    # Position peanut so its center sits at y = split_y/2 (top half middle).
    # `format=rgb` on overlay lets the alpha channel from chromakey take effect.
    overlay_chain = (
        f"[bg][peanut]overlay="
        f"x=(main_w-overlay_w)/2:"
        f"y=({cfg.split_y}-overlay_h)/2:"
        f"format=auto:eof_action=pass,{title_dt},{subs_dt}[v]"
    )

    filter_complex = ";".join([peanut_chain, bg_chain, overlay_chain])

    # Choose encoder. Old ffmpeg (<5.x) NVENC uses named presets; new uses p1-p7.
    # `fast` is accepted by both for broad compatibility.
    if _nvenc_available():
        enc_args = [
            "-c:v", "h264_nvenc", "-preset", "fast",
            "-b:v", "6M", "-maxrate", "8M", "-bufsize", "12M",
        ]
        logger.info("NVENC available — using GPU encode")
    else:
        enc_args = [
            "-c:v", "libx264", "-crf", str(cfg.crf), "-preset", "veryfast",
        ]

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "1:a",
        *enc_args,
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-t", f"{duration:.2f}",
        str(output_path),
    ]

    logger.info("Running ffmpeg composite ...")
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        stderr = r.stderr.decode(errors="replace")[-1500:]
        raise RuntimeError(
            f"Vertical composite failed (exit {r.returncode}):\n{stderr}"
        )

    if not output_path.exists():
        raise RuntimeError(f"ffmpeg reported success but {output_path} missing")

    logger.info("Composite: %s (%.1f MB)",
                output_path, output_path.stat().st_size / 1e6)
    return output_path
