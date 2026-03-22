#!/usr/bin/env python3
"""CLI entry point for downloading a YouTube playlist."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.core.path_sanitizer import DefaultPathSanitizer
from peanut_reacts.download.playlist import (
    PlaylistDownloadApp,
    PlaylistJob,
    YtDlpOptionsFactory,
    YtDlpPlaylistDownloader,
    YtDlpPlaylistInfoProvider,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download a YouTube playlist into a folder named after the playlist.",
    )
    p.add_argument("playlist_url", nargs="?", help="YouTube playlist URL (prompts if missing)")
    p.add_argument("-o", "--output", default="downloads", help="Output root folder (default: downloads)")
    p.add_argument("--no-index", action="store_true", help="Don't prefix filenames with playlist index")
    p.add_argument("--merge", default="mkv", help="Merged container format (default: mkv)")
    p.add_argument("--format", default="bestvideo[ext=webm]+bestaudio[ext=webm]/bestvideo+bestaudio")
    p.add_argument("--no-subs", action="store_true", help="Disable subtitle download")
    p.add_argument("--subs-lang", default="en", help="Subtitle language (default: en)")
    p.add_argument("--subs-format", default="srt", help="Subtitle format (default: srt)")
    p.add_argument("--cookies-file", default=None, help="Path to cookies.txt")
    p.add_argument("--cookies-from-browser", default=None, help='e.g. chrome or "chrome:Profile 1"')
    p.add_argument("--write-comments", action="store_true", help="Download comments (for peanut-react pipeline)")
    p.add_argument("--write-info-json", action="store_true", help="Write info.json metadata files")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logger = build_logger("playlist_download", args.verbose)

    playlist_url = (args.playlist_url or "").strip()
    if not playlist_url:
        playlist_url = input("Enter the playlist URL: ").strip()
        if not playlist_url:
            logger.error("No URL provided.")
            return 2

    output_root = Path(args.output).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    cookies_file = Path(args.cookies_file).expanduser().resolve() if args.cookies_file else None

    job = PlaylistJob(
        playlist_url=playlist_url,
        output_root=output_root,
        format_selector=args.format,
        merge_output_format=args.merge,
        include_index_prefix=not args.no_index,
        write_subtitles=not args.no_subs,
        write_auto_subtitles=not args.no_subs,
        subtitles_langs=(args.subs_lang.strip(),) if args.subs_lang else ("en",),
        subtitles_format=args.subs_format.strip() or "srt",
        cookies_file=cookies_file,
        cookies_from_browser=args.cookies_from_browser,
        write_comments=args.write_comments,
        write_info_json=args.write_info_json or args.write_comments,
    )

    app = PlaylistDownloadApp(
        info_provider=YtDlpPlaylistInfoProvider(logger),
        sanitizer=DefaultPathSanitizer(),
        downloader=YtDlpPlaylistDownloader(logger, YtDlpOptionsFactory(logger)),
        logger=logger,
    )

    try:
        folder = app.run(job)
        logger.info("Download completed! Files saved to: %s", folder)
        return 0
    except Exception as e:
        logger.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
