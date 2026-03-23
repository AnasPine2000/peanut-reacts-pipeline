#!/usr/bin/env python3
"""CLI entry point for fetching YouTube comments via the Data API v3."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.download.youtube_comments import YouTubeCommentFetcher


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch YouTube comments via the Data API v3 (faster + more reliable than yt-dlp).",
    )
    p.add_argument("url", nargs="?", help="YouTube video URL or ID (prompts if missing)")
    p.add_argument("-o", "--output", default="downloads", help="Output directory (default: downloads)")
    p.add_argument("-n", "--max-comments", type=int, default=500, help="Max comments to fetch (default: 500)")
    p.add_argument("--include-replies", action="store_true", help="Also fetch reply threads")
    p.add_argument("--api-key", default=None, help="YouTube Data API key (or set YOUTUBE_API_KEY env var)")
    p.add_argument("--oauth", default=None, help="Path to OAuth client_secret.json (for higher quotas)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("fetch_comments", args.verbose)

    url = (args.url or "").strip()
    if not url:
        url = input("Enter the YouTube URL or video ID: ").strip()
        if not url:
            log.error("No URL provided.")
            return 2

    # Authenticate
    api_key = args.api_key or os.environ.get("YOUTUBE_API_KEY", "")

    if args.oauth:
        log.info("Using OAuth authentication ...")
        fetcher = YouTubeCommentFetcher.from_oauth(args.oauth)
    elif api_key:
        fetcher = YouTubeCommentFetcher.from_api_key(api_key)
    else:
        log.error(
            "No API key provided. Set YOUTUBE_API_KEY env var, "
            "use --api-key, or use --oauth with client_secret.json"
        )
        return 2

    output_dir = Path(args.output).expanduser().resolve()

    try:
        path = fetcher.fetch_and_save(
            url,
            output_dir,
            max_results=args.max_comments,
            include_replies=args.include_replies,
        )
        log.info("Done! Comments saved to: %s", path)
        return 0
    except Exception as e:
        log.error("Failed: %s", e)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
