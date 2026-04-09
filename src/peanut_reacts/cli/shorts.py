#!/usr/bin/env python3
"""CLI entry point for generating oddly satisfying YouTube Shorts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.shorts.pipeline import SatisfyingShortsPipeline, ShortsConfig


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate and upload oddly satisfying YouTube Shorts.",
    )
    p.add_argument("--count", type=int, default=3, help="Number of shorts to generate (default: 3)")
    p.add_argument("--search", default="", help="Specific search query (random if empty)")
    p.add_argument("--no-upload", action="store_true", help="Generate locally without uploading")
    p.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    p.add_argument("--output-dir", default="production/shorts", help="Output directory")
    p.add_argument("--clips-per-short", type=int, default=3, help="Clips per short (default: 3)")
    p.add_argument("--duration", type=float, default=45.0, help="Target duration in seconds")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("shorts", args.verbose)

    config = ShortsConfig(
        pexels_api_key=os.environ.get("PEXELS_API_KEY", ""),
        pixabay_api_key=os.environ.get("PIXABAY_API_KEY", ""),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        youtube_client_secrets=os.environ.get(
            "YOUTUBE_CLIENT_SECRETS",
            str(Path.home() / "Downloads" / "client_secret_914737972580-9pd0j8k5601tk4p4mmq4e5jvnevonppa.apps.googleusercontent.com.json"),
        ),
        output_dir=Path(args.output_dir),
        clips_per_short=args.clips_per_short,
        target_duration=args.duration,
        upload=not args.no_upload,
        privacy=args.privacy,
    )

    if not config.pexels_api_key and not config.pixabay_api_key:
        log.error(
            "No video source API keys! Set PEXELS_API_KEY and/or PIXABAY_API_KEY.\n"
            "  Free signup: pexels.com/api and pixabay.com/api/docs"
        )
        return 2

    pipeline = SatisfyingShortsPipeline(config, log)

    try:
        results = pipeline.generate(count=args.count, query=args.search)

        log.info("")
        log.info("=" * 50)
        log.info("RESULTS: %d shorts generated", len(results))
        for r in results:
            status = f"→ {r.youtube_url}" if r.youtube_url else "(local only)"
            log.info("  %s %s", r.title[:60], status)
        log.info("=" * 50)
        return 0

    except Exception as e:
        log.error("Pipeline failed: %s", e)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
