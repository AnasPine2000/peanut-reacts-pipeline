#!/usr/bin/env python3
"""CLI entry point for Sidemen AmongUs Decoded video essays."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.decoded.pipeline import DecodedConfig, DecodedPipeline


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate Sidemen Among Us analysis video essays.",
    )
    p.add_argument("--type", default="player_analysis",
                   choices=["player_analysis", "episode_breakdown"],
                   help="Type of analysis")
    p.add_argument("--player", default="KSI", help="Player to analyze (for player_analysis)")
    p.add_argument("--video", default="", help="Video title (for episode_breakdown)")
    p.add_argument("--no-upload", action="store_true")
    p.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    p.add_argument("--output-dir", default="production/decoded")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("decoded", args.verbose)

    config = DecodedConfig(
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        elevenlabs_api_key=os.environ.get("ELEVENLABS_API_KEY", ""),
        youtube_client_secrets=os.environ.get(
            "YOUTUBE_CLIENT_SECRETS",
            str(Path.home() / "Downloads" / "client_secret_914737972580-9pd0j8k5601tk4p4mmq4e5jvnevonppa.apps.googleusercontent.com.json"),
        ),
        output_dir=Path(args.output_dir),
        upload=not args.no_upload,
        privacy=args.privacy,
    )

    if not config.deepseek_api_key:
        log.error("DEEPSEEK_API_KEY not set.")
        return 2

    pipeline = DecodedPipeline(config, log)

    try:
        if args.type == "player_analysis":
            result = pipeline.generate_player_analysis(args.player)
        else:
            log.error("Episode breakdown not yet implemented via CLI")
            return 1

        log.info("")
        log.info("=" * 60)
        log.info("ANALYSIS COMPLETE: %s", result.title)
        log.info("  Video: %s", result.youtube_url or result.video_path)
        log.info("  Shorts: %d", len(result.shorts_urls or result.shorts_paths))
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
