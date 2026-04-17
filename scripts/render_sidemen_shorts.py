#!/usr/bin/env python3
"""Render per-reaction Sidemen Shorts in 9:16 vertical TikTok format.

For each of the cached peanut segments (from the earlier full-Sidemen run),
composite into a 1080x1920 Short:
  - Peanut (top half) — the SadTalker-rendered, green-screen-keyed output
  - Sidemen gameplay (bottom half) — trimmed from source_clip.mp4 at the
    reaction's original timestamp, muted (peanut TTS is the only audio)
  - TikTok-style karaoke-ish burned subtitles of the reaction text

Inputs (all pre-existing, no re-rendering of SadTalker needed):
  - peanut_segments/peanut_NNN.mp4   — green-screen peanut lip-sync videos
  - production/ai_reaction/tts/r_NNNN.mp3  — matching TTS audio
  - production/ai_reaction/reactions.json — reaction metadata
  - production/ai_reaction/source_clip.mp4 — the Sidemen source

Usage:
    python scripts/render_sidemen_shorts.py
    python scripts/render_sidemen_shorts.py --from 1 --to 5   # just the first 5
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from peanut_reacts.compositing.vertical_story import (
    VerticalLayoutConfig,
    compose_vertical_short,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sidemen_shorts")

# ── Paths ──────────────────────────────────────────────────────────────
PROD = Path(r"C:\Users\anasm\Videos\4K Video Downloader+\production\ai_reaction")
REACTIONS_JSON = PROD / "reactions.json"
TTS_DIR = PROD / "tts"
SOURCE_CLIP = PROD / "source_clip.mp4"
PEANUT_SEGS_DIR = Path(r"C:\Users\anasm\ai_models\test_renders\e2e_pipeline\peanut_segments")
OUT_DIR = Path(r"C:\Users\anasm\ai_models\test_renders\sidemen_shorts")


def _probe_duration(p: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def _trim_source(source: Path, start: float, duration: float, dest: Path) -> Path:
    """Extract a duration-second clip of the Sidemen source starting at `start`."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(source),
        "-t", f"{duration + 1:.2f}",           # +1s buffer so loops never underrun
        "-an",                                   # strip audio
        "-c:v", "libx264", "-crf", "22", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        str(dest),
    ], check=True, capture_output=True, timeout=120)
    return dest


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="from_idx", type=int, default=1,
                   help="1-indexed first reaction to render (default 1)")
    p.add_argument("--to", dest="to_idx", type=int, default=0,
                   help="1-indexed last reaction; 0 = all (default 0)")
    p.add_argument("--bg-dir", default=None,
                   help="Directory of cached Sidemen bg clips (default: <out>/_sidemen_bg)")
    args = p.parse_args()

    reactions = json.loads(REACTIONS_JSON.read_text(encoding="utf-8"))
    log.info("Loaded %d reactions from %s", len(reactions), REACTIONS_JSON.name)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bg_dir = Path(args.bg_dir) if args.bg_dir else OUT_DIR / "_sidemen_bg"
    bg_dir.mkdir(parents=True, exist_ok=True)

    to_idx = args.to_idx if args.to_idx else len(reactions)

    # Tune the vertical layout for a tighter peanut framing (bigger than default)
    layout = VerticalLayoutConfig(
        peanut_scale=1.1,         # modest scale — avoid peanut clipping edges
        title_duration=2.0,
        title_fontsize=56,
        subtitle_fontsize=60,
        subtitle_chunk_words=4,
        bg_darken=0.5,            # moderate darken so subs pop
    )

    n_rendered, n_skipped = 0, 0
    t_total_start = time.time()

    for i, reaction in enumerate(reactions):
        idx_1 = i + 1
        if idx_1 < args.from_idx or idx_1 > to_idx:
            continue

        peanut_seg = PEANUT_SEGS_DIR / f"peanut_{idx_1:03d}.mp4"
        tts_mp3 = TTS_DIR / f"r_{i:04d}.mp3"
        out_path = OUT_DIR / f"short_{idx_1:03d}.mp4"

        if not peanut_seg.exists() or not tts_mp3.exists():
            log.warning("[%d] skipping: peanut=%s tts=%s",
                        idx_1, peanut_seg.exists(), tts_mp3.exists())
            n_skipped += 1
            continue
        if out_path.exists():
            log.info("[%d] already rendered (%s)", idx_1, out_path.name)
            n_skipped += 1
            continue

        dur = _probe_duration(tts_mp3)
        log.info("[%d/%d] %s  \"%s\"  (%.2fs)", idx_1, to_idx,
                 reaction["emotion"], reaction["text"][:60], dur)

        # Extract Sidemen background slice at the reaction's timestamp
        bg_clip = bg_dir / f"bg_{idx_1:03d}.mp4"
        _trim_source(SOURCE_CLIP, reaction["start"], dur + 0.5, bg_clip)

        # Composite the 9:16 Short
        t0 = time.time()
        compose_vertical_short(
            peanut_video=peanut_seg,
            tts_audio=tts_mp3,
            story_title=reaction["text"],       # shown as title card
            subtitle_text=reaction["text"],     # chunked into subs
            output_path=out_path,
            background_video=bg_clip,
            config=layout,
        )
        log.info("[%d]  → %s  (%.1fs)", idx_1, out_path.name, time.time() - t0)
        n_rendered += 1

    log.info("=== done: %d rendered, %d skipped in %.1fs ===",
             n_rendered, n_skipped, time.time() - t_total_start)
    log.info("Output dir: %s", OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
