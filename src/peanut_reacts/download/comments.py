"""
Standalone YouTube comment downloader.

Downloads comments for a video (or all videos in a playlist) and saves
them as .info.json files containing the comment data. Works with
videos that have already been downloaded — just fetches metadata.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yt_dlp

from peanut_reacts.core.cookies import parse_cookies_from_browser


@dataclass(frozen=True)
class CommentDownloadJob:
    """Configuration for downloading comments."""
    url: str                         # video URL, playlist URL, or channel URL
    output_dir: Path
    cookies_file: Optional[Path] = None
    cookies_from_browser: Optional[str] = None


class CommentDownloader:
    """Download comments and metadata for YouTube videos."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def download(self, job: CommentDownloadJob) -> list[Path]:
        """Download comments and save as .info.json files.

        Returns a list of paths to the saved info.json files.
        """
        job.output_dir.mkdir(parents=True, exist_ok=True)

        ydl_opts = {
            "skip_download": True,
            "writeinfojson": True,
            "getcomments": True,
            "ignoreerrors": True,
            "outtmpl": str(job.output_dir / "%(title)s.%(ext)s"),
            "windowsfilenames": True,
            "quiet": False,
        }

        if job.cookies_file:
            ydl_opts["cookiefile"] = str(job.cookies_file)
        if job.cookies_from_browser:
            ydl_opts["cookiesfrombrowser"] = parse_cookies_from_browser(job.cookies_from_browser)

        self._log.info("Downloading comments from %s ...", job.url)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([job.url])

        # Collect generated info.json files
        info_files = sorted(job.output_dir.glob("*.info.json"))
        self._log.info("Saved %d info.json file(s) to %s", len(info_files), job.output_dir)
        return info_files
