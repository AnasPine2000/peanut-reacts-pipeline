"""Smoke-test the Ollama vision integration against a real video clip.

Usage:
  1. Install Ollama: https://ollama.com/download
  2. Pull the model: ollama pull qwen3-vl:8b
  3. Set env (the OLLAMA_VISION_MODEL toggle enables the local path):
        $env:OLLAMA_VISION_MODEL = "qwen3-vl:8b"    # PowerShell
        export OLLAMA_VISION_MODEL=qwen3-vl:8b      # bash
  4. Run:
        PYTHONPATH=src python scripts/smoke_ollama_vision.py [CLIP_PATH]

If CLIP_PATH is omitted, we pick the first clip from data/ai_peanut_clips
(facecam clips work fine too — Qwen3-VL doesn't care).

The script prints timing for each call. First call includes cold-start
model load (10-30 s on an 8 GB GPU with Q4_K_M). Subsequent calls are
the steady-state latency — typically 2-4 s on an RTX 4070.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

# Make sure src is importable when run directly
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from peanut_reacts.character.clip_context import (  # noqa: E402
    OLLAMA_URL,
    OLLAMA_VISION_MODEL,
    _ollama_reachable,
    describe_clip,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)


def _pick_clip() -> Path | None:
    """Find any playable 2-10s clip in the repo for smoke testing."""
    candidates = [
        ROOT / "data" / "ai_peanut_clips",
        ROOT / "data" / "peanut_clips",
        Path("C:/tmp/live_rx_uk"),
        Path("C:/tmp/live_rx_uk_v5"),
    ]
    for d in candidates:
        if d.exists():
            for p in sorted(d.rglob("*.mp4")):
                # Skip the giant concatenated source files
                try:
                    size_mb = p.stat().st_size / (1024 * 1024)
                except OSError:
                    continue
                if 0.1 < size_mb < 50:
                    return p
    return None


def main() -> int:
    print(f"Ollama URL:    {OLLAMA_URL}")
    print(f"Vision model:  {OLLAMA_VISION_MODEL!r}")
    if not OLLAMA_VISION_MODEL:
        print(
            "OLLAMA_VISION_MODEL is not set — the pipeline will use Groq only.\n"
            "Set it to enable the local path, e.g. qwen3-vl:8b"
        )
        return 1

    if _ollama_reachable():
        print("Ollama: reachable")
    else:
        print("Ollama: NOT reachable. Start it with `ollama serve` (or the UI app).")
        return 2

    # Pick a clip
    if len(sys.argv) >= 2:
        clip = Path(sys.argv[1])
    else:
        clip = _pick_clip()
        if clip is None:
            print("No clip found. Pass one as an argument: smoke_ollama_vision.py CLIP.mp4")
            return 3

    if not clip.exists():
        print(f"Clip not found: {clip}")
        return 4

    print(f"\nClip: {clip}")
    print("Running 3 describe_clip calls (first is cold-start, 2nd/3rd are steady-state):\n")

    for i in range(3):
        t0 = time.time()
        desc = describe_clip(clip)
        dt = time.time() - t0
        print(f"  Call {i + 1}: {dt:.2f}s  -> {desc}")

    print("\nSmoke test complete. If 2nd/3rd calls were <5s, you're good to go.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
