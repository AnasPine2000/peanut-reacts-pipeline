#!/usr/bin/env python3
"""CLI entry point for synthesizing TTS audio from a reaction script."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.character.tts import EdgeTTSEngine, TTSConfig


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Synthesize TTS audio from a reaction script JSON.",
    )
    p.add_argument("script", help="Path to reaction_script.json")
    p.add_argument("-o", "--output-dir", default="tts_output", help="Output directory for audio files")
    p.add_argument("--voice", default="en-US-GuyNeural", help="Edge TTS voice (default: en-US-GuyNeural)")
    p.add_argument("--rate", default="+10%", help="Speaking rate (default: +10%%)")
    p.add_argument("--pitch", default="+0Hz", help="Pitch adjustment")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("synthesize_tts", args.verbose)

    script_path = Path(args.script).expanduser().resolve()
    if not script_path.exists():
        log.error("Script not found: %s", script_path)
        return 2

    data = json.loads(script_path.read_text(encoding="utf-8"))
    lines = data.get("lines", data) if isinstance(data, dict) else data

    if not lines:
        log.warning("No lines in script.")
        return 0

    config = TTSConfig(voice=args.voice, rate=args.rate, pitch=args.pitch)
    engine = EdgeTTSEngine(config, log)
    output_dir = Path(args.output_dir).expanduser().resolve()

    results = engine.synthesize_lines(lines, output_dir)

    manifest_path = output_dir / "tts_manifest.json"
    engine.save_manifest(results, manifest_path)

    log.info("Synthesized %d line(s) → %s", len(results), output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
