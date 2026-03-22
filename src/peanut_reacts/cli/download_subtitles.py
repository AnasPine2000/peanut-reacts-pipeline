#!/usr/bin/env python3
"""CLI entry point for downloading subtitles from a YouTube playlist."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.download.subtitles import SubtitleDownloader, SubtitleJob


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download subtitles for a YouTube playlist (skip videos).")
    p.add_argument("playlist_url", nargs="?", help="Playlist URL (prompts if missing)")
    p.add_argument("-o", "--output", default="subtitles", help="Output directory (default: subtitles)")
    p.add_argument("--lang", default="en", help="Subtitle language (default: en)")
    p.add_argument("--format", default="srt", help="Subtitle format (default: srt)")
    p.add_argument("--cookies-file", default=None)
    p.add_argument("--cookies-from-browser", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("subtitle_download", args.verbose)

    url = (args.playlist_url or "").strip()
    if not url:
        url = input("Enter the playlist URL: ").strip()
        if not url:
            log.error("No URL provided.")
            return 2

    output_dir = Path(args.output).expanduser().resolve()
    cookies_file = Path(args.cookies_file).expanduser().resolve() if args.cookies_file else None

    job = SubtitleJob(
        playlist_url=url,
        output_dir=output_dir,
        language=args.lang,
        subtitle_format=args.format,
        cookies_file=cookies_file,
        cookies_from_browser=args.cookies_from_browser,
    )

    downloader = SubtitleDownloader(log)

    try:
        out = downloader.download(job)
        log.info("Subtitles saved to: %s", out)
        return 0
    except Exception as e:
        log.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
