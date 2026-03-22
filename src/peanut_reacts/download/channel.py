"""
YouTube channel/URL video downloader.

Downloads all videos from a channel or URL with subtitle support.
Refactored from archive/videos_download.py — all hardcoded values parameterised.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yt_dlp

from peanut_reacts.core.cookies import parse_cookies_from_browser


@dataclass(frozen=True)
class ChannelJob:
    channel_url: str
    output_folder: Path
    format_selector: str = "bestvideo+bestaudio/best"
    merge_output_format: str = "mp4"
    write_subtitles: bool = True
    write_auto_subtitles: bool = True
    subtitles_format: str = "srt"
    ignore_errors: bool = True
    cookies_file: Optional[Path] = None
    cookies_from_browser: Optional[str] = None


class ChannelDownloader:
    """Download all videos from a YouTube channel or URL."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def download(self, job: ChannelJob) -> Path:
        job.output_folder.mkdir(parents=True, exist_ok=True)

        ydl_opts = {
            "format": job.format_selector,
            "merge_output_format": job.merge_output_format,
            "writesubtitles": job.write_subtitles,
            "writeautomaticsub": job.write_auto_subtitles,
            "subtitlesformat": "vtt",
            "ignoreerrors": job.ignore_errors,
            "postprocessors": [
                {"key": "FFmpegSubtitlesConvertor", "format": job.subtitles_format},
            ],
            "outtmpl": str(job.output_folder / "%(title)s.%(ext)s"),
        }

        if job.cookies_file:
            ydl_opts["cookiefile"] = str(job.cookies_file)
        if job.cookies_from_browser:
            ydl_opts["cookiesfrombrowser"] = parse_cookies_from_browser(job.cookies_from_browser)

        self._log.info("Downloading from %s into %s", job.channel_url, job.output_folder)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([job.channel_url])

        return job.output_folder
