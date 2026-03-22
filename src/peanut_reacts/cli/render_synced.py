#!/usr/bin/env python3
"""CLI entry point for rendering speech-synced peanut animation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.character.tts import TTSWordTiming, tts_result_from_dict
from peanut_reacts.character.sync import (
    render_reaction_webm,
    word_timings_to_speech_events,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Render speech-synced peanut animation from TTS manifest.",
    )
    p.add_argument("tts_manifest", help="Path to tts_manifest.json")
    p.add_argument("-o", "--output-dir", default="synced_output", help="Output directory for WebM files")
    p.add_argument("--fps", type=int, default=24, help="Animation FPS (default: 24)")
    p.add_argument("--canvas", type=int, default=512, help="Canvas size")
    p.add_argument("--char-size", type=int, default=420, help="Character size")
    p.add_argument("--seed", type=int, default=0, help="Random seed")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("render_synced", args.verbose)

    manifest_path = Path(args.tts_manifest).expanduser().resolve()
    if not manifest_path.exists():
        log.error("Manifest not found: %s", manifest_path)
        return 2

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    for idx, entry in enumerate(manifest):
        line = entry.get("line", {})
        tts = tts_result_from_dict(entry)
        line_start = float(line.get("start", 0))

        if tts.duration <= 0:
            continue

        speech_events = word_timings_to_speech_events(tts.word_timings, line_start=0.0)
        webm_path = output_dir / f"reaction_{idx + 1:03d}.webm"

        log.info("Rendering segment %d (%.2fs) ...", idx + 1, tts.duration)
        render_reaction_webm(
            speech_events, tts.duration + 0.5, webm_path,
            fps=args.fps, canvas=args.canvas, char_size=args.char_size, seed=args.seed,
        )

    log.info("Rendered %d segment(s) to %s", len(manifest), output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
