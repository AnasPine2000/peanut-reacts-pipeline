#!/usr/bin/env python3
"""CLI entry point for generating scary/creepy compilation videos."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from peanut_reacts.compilations.pipeline import CompilationConfig, ScaryCompilationPipeline
from peanut_reacts.core.logging_setup import build_logger


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate scary/creepy compilation videos with narration.",
    )
    p.add_argument("--topic", default="", help="Video topic (random if empty)")
    p.add_argument("--segments", type=int, default=10, help="Number of segments (default: 10)")
    p.add_argument("--no-upload", action="store_true", help="Generate locally only")
    p.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    p.add_argument("--output-dir", default="production/compilations")
    p.add_argument("--max-shorts", type=int, default=5, help="Max shorts to extract")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("compile", args.verbose)

    config = CompilationConfig(
        pexels_api_key=os.environ.get("PEXELS_API_KEY", ""),
        pixabay_api_key=os.environ.get("PIXABAY_API_KEY", ""),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        elevenlabs_api_key=os.environ.get("ELEVENLABS_API_KEY", ""),
        youtube_client_secrets=os.environ.get(
            "YOUTUBE_CLIENT_SECRETS",
            str(Path.home() / "Downloads" / "client_secret_914737972580-9pd0j8k5601tk4p4mmq4e5jvnevonppa.apps.googleusercontent.com.json"),
        ),
        output_dir=Path(args.output_dir),
        segment_count=args.segments,
        max_shorts=args.max_shorts,
        upload=not args.no_upload,
        privacy=args.privacy,
    )

    if not config.pixabay_api_key and not config.pexels_api_key:
        log.error("No video source API key! Set PIXABAY_API_KEY or PEXELS_API_KEY.")
        return 2

    if not config.deepseek_api_key:
        log.error("No DEEPSEEK_API_KEY set for script generation.")
        return 2

    pipeline = ScaryCompilationPipeline(config, log)

    try:
        result = pipeline.generate(topic=args.topic)
        log.info("")
        log.info("=" * 60)
        log.info("COMPILATION COMPLETE")
        log.info("  Long-form: %s", result.long_form_url or result.long_form_path)
        log.info("  Shorts: %d", len(result.shorts_urls or result.shorts_paths))
        for url in result.shorts_urls:
            log.info("    %s", url)
        log.info("=" * 60)
        return 0
    except Exception as e:
        log.error("Pipeline failed: %s", e)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
