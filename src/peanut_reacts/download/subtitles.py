"""
YouTube playlist subtitle downloader.

Downloads subtitles (manual + auto-generated) for all videos in a playlist
using yt-dlp's Python API. Converts to SRT format.

Refactored from archive/subtitles_playlist_srt.py — fixed bugs (original
incorrectly passed Python kwargs as CLI args) and uses the Python API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yt_dlp

from peanut_reacts.core.cookies import parse_cookies_from_browser


@dataclass(frozen=True)
class SubtitleJob:
    playlist_url: str
    output_dir: Path
    language: str = "en"
    subtitle_format: str = "srt"
    cookies_file: Optional[Path] = None
    cookies_from_browser: Optional[str] = None


class SubtitleDownloader:
    """Download subtitles for all videos in a playlist (skip video download)."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def download(self, job: SubtitleJob) -> Path:
        job.output_dir.mkdir(parents=True, exist_ok=True)

        ydl_opts = {
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [job.language],
            "subtitlesformat": job.subtitle_format,
            "skip_download": True,
            "ignoreerrors": True,
            "postprocessors": [
                {"key": "FFmpegSubtitlesConvertor", "format": job.subtitle_format},
            ],
            "outtmpl": str(job.output_dir / "%(title)s.%(ext)s"),
            "windowsfilenames": True,
        }

        if job.cookies_file:
            ydl_opts["cookiefile"] = str(job.cookies_file)
        if job.cookies_from_browser:
            ydl_opts["cookiesfrombrowser"] = parse_cookies_from_browser(job.cookies_from_browser)

        self._log.info("Downloading subtitles from %s ...", job.playlist_url)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([job.playlist_url])

        self._log.info("Subtitles saved to: %s", job.output_dir)
        return job.output_dir
