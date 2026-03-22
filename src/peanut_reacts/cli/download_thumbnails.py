#!/usr/bin/env python3
"""CLI entry point for downloading playlist thumbnails as JPG."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.core.path_sanitizer import DefaultPathSanitizer
from peanut_reacts.download.thumbnails import (
    PillowJpegStore,
    ThumbnailApp,
    ThumbnailJob,
    UrllibFetcher,
    YtDlpPlaylistExtractor,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Force-download playlist thumbnails as JPG.")
    p.add_argument("playlist_url", nargs="?", help="Playlist URL (prompts if missing)")
    p.add_argument("-o", "--output", default="downloads", help="Output root folder")
    p.add_argument("--thumbs-folder", default="thumbnails", help='Subfolder name (default: "thumbnails")')
    p.add_argument("--no-index", action="store_true", help="Don't prefix with playlist index")
    p.add_argument("--cookies-file", default=None)
    p.add_argument("--cookies-from-browser", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("thumbnail_download", args.verbose)

    url = (args.playlist_url or "").strip()
    if not url:
        url = input("Enter the playlist URL: ").strip()
        if not url:
            log.error("No URL provided.")
            return 2

    output_root = Path(args.output).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cookies_file = Path(args.cookies_file).expanduser().resolve() if args.cookies_file else None

    job = ThumbnailJob(
        playlist_url=url,
        output_root=output_root,
        thumbnails_folder_name=args.thumbs_folder.strip() or "thumbnails",
        include_index_prefix=not args.no_index,
        cookies_file=cookies_file,
        cookies_from_browser=args.cookies_from_browser,
    )

    app = ThumbnailApp(
        extractor=YtDlpPlaylistExtractor(log),
        sanitizer=DefaultPathSanitizer(),
        fetcher=UrllibFetcher(log, timeout_sec=job.timeout_sec, retries=job.retries, sleep_sec=job.sleep_between_retries_sec),
        image_store=PillowJpegStore(log, jpeg_quality=92),
        logger=log,
    )

    try:
        out_dir = app.run(job)
        log.info("Thumbnails saved to: %s", out_dir)
        return 0
    except Exception as e:
        log.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
