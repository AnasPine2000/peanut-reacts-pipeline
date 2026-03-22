#!/usr/bin/env python3
"""CLI entry point for downloading videos from a YouTube channel or URL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.download.channel import ChannelDownloader, ChannelJob


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download all videos from a YouTube channel or URL.")
    p.add_argument("channel_url", nargs="?", help="Channel or playlist URL (prompts if missing)")
    p.add_argument("-o", "--output", default="downloads", help="Output folder (default: downloads)")
    p.add_argument("--format", default="bestvideo+bestaudio/best")
    p.add_argument("--merge", default="mp4", help="Container format (default: mp4)")
    p.add_argument("--no-subs", action="store_true")
    p.add_argument("--write-comments", action="store_true", help="Download comments (for peanut-react pipeline)")
    p.add_argument("--write-info-json", action="store_true", help="Write info.json metadata files")
    p.add_argument("--cookies-file", default=None)
    p.add_argument("--cookies-from-browser", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("channel_download", args.verbose)

    url = (args.channel_url or "").strip()
    if not url:
        url = input("Enter the channel/URL: ").strip()
        if not url:
            log.error("No URL provided.")
            return 2

    output_folder = Path(args.output).expanduser().resolve()
    cookies_file = Path(args.cookies_file).expanduser().resolve() if args.cookies_file else None

    job = ChannelJob(
        channel_url=url,
        output_folder=output_folder,
        format_selector=args.format,
        merge_output_format=args.merge,
        write_subtitles=not args.no_subs,
        write_auto_subtitles=not args.no_subs,
        cookies_file=cookies_file,
        cookies_from_browser=args.cookies_from_browser,
        write_comments=args.write_comments,
        write_info_json=args.write_info_json or args.write_comments,
    )

    downloader = ChannelDownloader(log)

    try:
        folder = downloader.download(job)
        log.info("Download completed! Files in: %s", folder)
        return 0
    except Exception as e:
        log.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
