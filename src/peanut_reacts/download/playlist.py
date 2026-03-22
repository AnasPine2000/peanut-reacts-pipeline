"""
YouTube playlist downloader.

Downloads all videos from a playlist into a folder named after the playlist,
with subtitles, download archive, and optional cookie support.

Refactored from archive/playlist_download.py following SOLID principles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable

import yt_dlp

from peanut_reacts.core.cookies import parse_cookies_from_browser
from peanut_reacts.core.path_sanitizer import DefaultPathSanitizer, IPathSanitizer


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlaylistJob:
    """Input for a playlist download run."""
    playlist_url: str
    output_root: Path

    format_selector: str = "bestvideo[ext=webm]+bestaudio[ext=webm]/bestvideo+bestaudio"
    merge_output_format: str = "mkv"
    include_index_prefix: bool = True

    write_subtitles: bool = True
    write_auto_subtitles: bool = True
    subtitles_langs: tuple[str, ...] = ("en",)
    subtitles_format: str = "srt"

    ignore_errors: bool = True
    retries: int = 10
    fragment_retries: int = 10
    concurrent_fragment_downloads: int = 4

    # Comments & metadata
    write_comments: bool = False
    write_info_json: bool = False

    cookies_file: Optional[Path] = None
    cookies_from_browser: Optional[str] = None


@dataclass(frozen=True)
class PlaylistInfo:
    """Minimal playlist metadata for naming the output folder."""
    title: str
    identifier: str


# ---------------------------------------------------------------------------
# Abstractions
# ---------------------------------------------------------------------------

@runtime_checkable
class IPlaylistInfoProvider(Protocol):
    def fetch(self, url: str) -> PlaylistInfo: ...


@runtime_checkable
class IYtDlpOptionsFactory(Protocol):
    def build(self, job: PlaylistJob, target_folder: Path, archive_file: Path) -> Dict[str, Any]: ...


@runtime_checkable
class IPlaylistDownloader(Protocol):
    def download(self, job: PlaylistJob, target_folder: Path) -> None: ...


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

class YtDlpPlaylistInfoProvider:
    """Fetch playlist title/id without downloading."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def fetch(self, url: str) -> PlaylistInfo:
        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title = (info.get("title") or info.get("playlist_title") or "").strip()
        identifier = str(info.get("id") or info.get("playlist_id") or "unknown")

        if not title:
            self._log.warning("Could not detect playlist title; falling back to identifier.")
            title = "playlist"

        return PlaylistInfo(title=title, identifier=identifier)


class YtDlpOptionsFactory:
    """Build yt-dlp options dict from our domain model."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def build(self, job: PlaylistJob, target_folder: Path, archive_file: Path) -> Dict[str, Any]:
        if job.include_index_prefix:
            filename_tmpl = "%(playlist_index)03d - %(title)s.%(ext)s"
        else:
            filename_tmpl = "%(title)s.%(ext)s"

        ydl_opts: Dict[str, Any] = {
            "format": job.format_selector,
            "merge_output_format": job.merge_output_format,
            "writesubtitles": job.write_subtitles,
            "writeautomaticsub": job.write_auto_subtitles,
            "subtitleslangs": list(job.subtitles_langs),
            "subtitlesformat": job.subtitles_format,
            "postprocessors": [
                {"key": "FFmpegSubtitlesConvertor", "format": job.subtitles_format},
            ],
            "outtmpl": str(target_folder / filename_tmpl),
            "windowsfilenames": True,
            "noplaylist": False,
            "download_archive": str(archive_file),
            "ignoreerrors": job.ignore_errors,
            "continuedl": True,
            "retries": job.retries,
            "fragment_retries": job.fragment_retries,
            "concurrent_fragment_downloads": job.concurrent_fragment_downloads,
        }

        if job.write_comments:
            ydl_opts["getcomments"] = True
        if job.write_info_json:
            ydl_opts["writeinfojson"] = True

        if job.cookies_file:
            ydl_opts["cookiefile"] = str(job.cookies_file)
        if job.cookies_from_browser:
            ydl_opts["cookiesfrombrowser"] = parse_cookies_from_browser(job.cookies_from_browser)

        return ydl_opts


class YtDlpPlaylistDownloader:
    """Download using yt-dlp into a prepared folder."""

    def __init__(self, logger: logging.Logger, options_factory: IYtDlpOptionsFactory) -> None:
        self._log = logger
        self._opts_factory = options_factory

    def download(self, job: PlaylistJob, target_folder: Path) -> None:
        target_folder.mkdir(parents=True, exist_ok=True)
        archive_file = target_folder / "downloaded.txt"

        self._log.info("Downloading playlist into: %s", target_folder)
        ydl_opts = self._opts_factory.build(job, target_folder, archive_file)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([job.playlist_url])


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class PlaylistDownloadApp:
    """Orchestrator: fetch playlist info -> compute folder -> download."""

    def __init__(
        self,
        info_provider: IPlaylistInfoProvider,
        sanitizer: IPathSanitizer,
        downloader: IPlaylistDownloader,
        logger: logging.Logger,
    ) -> None:
        self._info_provider = info_provider
        self._sanitizer = sanitizer
        self._downloader = downloader
        self._log = logger

    def run(self, job: PlaylistJob) -> Path:
        info = self._info_provider.fetch(job.playlist_url)
        folder_name = self._sanitizer.sanitize(info.title)

        if folder_name.lower() in {"playlist", "unknown", "untitled"} and info.identifier:
            folder_name = f"{folder_name}-{info.identifier}"

        target_folder = job.output_root / folder_name
        self._downloader.download(job, target_folder)
        return target_folder
