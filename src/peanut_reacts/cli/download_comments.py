#!/usr/bin/env python3
"""CLI entry point for downloading YouTube comments."""

from __future__ import annotations

import argparse
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.download.comments import CommentDownloader, CommentDownloadJob


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download comments from a YouTube video or playlist (saves as .info.json).",
    )
    p.add_argument("url", nargs="?", help="YouTube video or playlist URL (prompts if missing)")
    p.add_argument("-o", "--output", default="downloads", help="Output directory (default: downloads)")
    p.add_argument("--cookies-file", default=None, help="Path to cookies.txt (Netscape format)")
    p.add_argument("--cookies-from-browser", default=None, help='e.g. chrome or "chrome:Profile 1"')
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("download_comments", args.verbose)

    url = (args.url or "").strip()
    if not url:
        url = input("Enter the YouTube URL: ").strip()
        if not url:
            log.error("No URL provided.")
            return 2

    output_dir = Path(args.output).expanduser().resolve()
    cookies_file = Path(args.cookies_file).expanduser().resolve() if args.cookies_file else None

    job = CommentDownloadJob(
        url=url,
        output_dir=output_dir,
        cookies_file=cookies_file,
        cookies_from_browser=args.cookies_from_browser,
    )

    downloader = CommentDownloader(log)

    try:
        files = downloader.download(job)
        if files:
            log.info("Downloaded comments to %d file(s):", len(files))
            for f in files:
                log.info("  %s", f.name)
        else:
            log.warning("No info.json files generated.")
        return 0
    except Exception as e:
        log.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
