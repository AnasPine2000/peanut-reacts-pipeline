#!/usr/bin/env python3
"""
playlist_download_solid.py

Download all videos from a YouTube playlist into a dedicated folder named after the playlist.

Features
- Creates an output folder using the playlist title (sanitized for Windows/macOS/Linux).
- Downloads best video+audio (prefers webm) and merges to a chosen container (default: mkv).
- Downloads subtitles (manual + auto), keeps English by default, ensures SRT.
- Uses a download archive file so re-running the script skips already downloaded videos.
- Optional cookies support (cookies file OR cookies-from-browser).

Requirements
- pip install -U yt-dlp
- ffmpeg on PATH (recommended) for merging and subtitle conversion

Examples (PowerShell)
    python .\playlist_download_solid.py `
      "https://www.youtube.com/playlist?list=PL..." `
      -o "$env:USERPROFILE\Videos\4K Video Downloader+\downloads"

    # Cookies from Chrome (fixes "Sign in to confirm you're not a bot" in many cases):
    python .\playlist_download_solid.py "https://www.youtube.com/playlist?list=PL..." `
      -o "$env:USERPROFILE\Videos\downloads" `
      --cookies-from-browser chrome

    # Cookies from Chrome with a specific profile:
    python .\playlist_download_solid.py "https://www.youtube.com/playlist?list=PL..." `
      -o "$env:USERPROFILE\Videos\downloads" `
      --cookies-from-browser "chrome:Profile 1"

    # Cookies via exported cookies.txt (Netscape format):
    python .\playlist_download_solid.py "https://www.youtube.com/playlist?list=PL..." `
      -o "$env:USERPROFILE\Videos\downloads" `
      --cookies-file "C:\path\to\cookies.txt"
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable

import yt_dlp


# =============================================================================
# Domain models
# =============================================================================

@dataclass(frozen=True)
class PlaylistJob:
    """Input for a playlist download run."""
    playlist_url: str
    output_root: Path

    # Download behavior
    format_selector: str = "bestvideo[ext=webm]+bestaudio[ext=webm]/bestvideo+bestaudio"
    merge_output_format: str = "mkv"
    include_index_prefix: bool = True

    # Subtitles behavior
    write_subtitles: bool = True
    write_auto_subtitles: bool = True
    subtitles_langs: tuple[str, ...] = ("en",)
    subtitles_format: str = "srt"

    # Reliability / convenience
    ignore_errors: bool = True
    retries: int = 10
    fragment_retries: int = 10
    concurrent_fragment_downloads: int = 4

    # Optional auth
    cookies_file: Optional[Path] = None
    cookies_from_browser: Optional[str] = None  # e.g. "chrome" or "chrome:Profile 1"


@dataclass(frozen=True)
class PlaylistInfo:
    """Minimal playlist metadata used for naming output folder."""
    title: str
    identifier: str


# =============================================================================
# SOLID: abstractions (DIP)
# =============================================================================

@runtime_checkable
class IPlaylistInfoProvider(Protocol):
    def fetch(self, url: str) -> PlaylistInfo:
        ...


@runtime_checkable
class IPathSanitizer(Protocol):
    def sanitize_folder_name(self, raw: str) -> str:
        ...


@runtime_checkable
class IYtDlpOptionsFactory(Protocol):
    def build(self, job: PlaylistJob, target_folder: Path, archive_file: Path) -> Dict[str, Any]:
        ...


@runtime_checkable
class IPlaylistDownloader(Protocol):
    def download(self, job: PlaylistJob, target_folder: Path) -> None:
        ...


# =============================================================================
# Implementations
# =============================================================================

class DefaultPathSanitizer(IPathSanitizer):
    """
    Sanitizes folder names across platforms.

    - Removes Windows-reserved characters and control chars
    - Collapses whitespace
    - Avoids empty names
    """
    _illegal = re.compile(r'[<>:"/\\|?*\x00-\x1F]')  # Windows + control chars
    _whitespace = re.compile(r"\s+")

    def sanitize_folder_name(self, raw: str) -> str:
        name = raw.strip()
        name = self._illegal.sub(" ", name)
        name = self._whitespace.sub(" ", name).strip()
        name = name.rstrip(". ").strip()
        return name or "playlist"


class YtDlpPlaylistInfoProvider(IPlaylistInfoProvider):
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


def _parse_cookies_from_browser_spec(spec: str) -> tuple[str, ...]:
    """
    yt-dlp's Python API expects cookiesfrombrowser as a tuple, not a plain string.

    CLI examples:
      --cookies-from-browser chrome
      --cookies-from-browser "chrome:Profile 1"

    This helper converts:
      "chrome"            -> ("chrome",)
      "chrome:Profile 1"  -> ("chrome", "Profile 1")
    """
    spec = spec.strip().strip('"').strip("'")
    if not spec:
        raise ValueError("cookies-from-browser spec is empty")

    # Keep it intentionally simple: browser[:profile]
    if ":" in spec:
        browser, profile = spec.split(":", 1)
        browser = browser.strip()
        profile = profile.strip()
        if profile:
            return (browser, profile)
        return (browser,)
    return (spec,)


class YtDlpOptionsFactory(IYtDlpOptionsFactory):
    """Build yt-dlp options dict from our domain model."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def build(self, job: PlaylistJob, target_folder: Path, archive_file: Path) -> Dict[str, Any]:
        # Filenames
        if job.include_index_prefix:
            filename_tmpl = "%(playlist_index)03d - %(title)s.%(ext)s"
        else:
            filename_tmpl = "%(title)s.%(ext)s"

        outtmpl = str(target_folder / filename_tmpl)

        ydl_opts: Dict[str, Any] = {
            # Core selection
            "format": job.format_selector,
            "merge_output_format": job.merge_output_format,

            # Subtitles
            "writesubtitles": job.write_subtitles,
            "writeautomaticsub": job.write_auto_subtitles,
            "subtitleslangs": list(job.subtitles_langs),
            "subtitlesformat": job.subtitles_format,
            "postprocessors": [
                {
                    "key": "FFmpegSubtitlesConvertor",
                    "format": job.subtitles_format,
                }
            ],

            # Output control
            "outtmpl": outtmpl,
            "windowsfilenames": True,   # safer filenames on Windows
            "noplaylist": False,

            # Resume / reliability
            "download_archive": str(archive_file),
            "ignoreerrors": job.ignore_errors,
            "continuedl": True,
            "retries": job.retries,
            "fragment_retries": job.fragment_retries,
            "concurrent_fragment_downloads": job.concurrent_fragment_downloads,
        }

        # Optional cookies
        if job.cookies_file:
            ydl_opts["cookiefile"] = str(job.cookies_file)

        if job.cookies_from_browser:
            ydl_opts["cookiesfrombrowser"] = _parse_cookies_from_browser_spec(job.cookies_from_browser)

        return ydl_opts


class YtDlpPlaylistDownloader(IPlaylistDownloader):
    """Download using yt-dlp into a prepared folder."""

    def __init__(self, logger: logging.Logger, options_factory: IYtDlpOptionsFactory) -> None:
        self._log = logger
        self._opts_factory = options_factory

    def download(self, job: PlaylistJob, target_folder: Path) -> None:
        target_folder.mkdir(parents=True, exist_ok=True)
        archive_file = target_folder / "downloaded.txt"

        self._log.info(f"Downloading playlist into: {target_folder}")
        self._log.info(f"Archive file: {archive_file}")

        ydl_opts = self._opts_factory.build(job, target_folder, archive_file)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([job.playlist_url])


class PlaylistDownloadApp:
    """
    Orchestrator (SRP): fetch playlist info -> compute folder -> download.
    Depends on abstractions (DIP).
    """

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

        folder_name = self._sanitizer.sanitize_folder_name(info.title)
        # If it ends up too generic, append id to reduce collisions
        if folder_name.lower() in {"playlist", "unknown"} and info.identifier:
            folder_name = f"{folder_name}-{info.identifier}"

        target_folder = job.output_root / folder_name
        self._downloader.download(job, target_folder)
        return target_folder


# =============================================================================
# CLI / bootstrap
# =============================================================================

def _build_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("playlist_download")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        logger.addHandler(handler)

    return logger


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download a YouTube playlist into a folder named after the playlist (with subtitles)."
    )
    p.add_argument("playlist_url", nargs="?", help="YouTube playlist URL (optional; will prompt if missing)")
    p.add_argument(
        "-o", "--output",
        default=str(Path.cwd() / "downloads"),
        help="Output root folder. A playlist-named subfolder will be created inside it.",
    )
    p.add_argument("--no-index", action="store_true", help="Do not prefix filenames with playlist index")
    p.add_argument("--merge", default="mkv", help="Merged container format (default: mkv)")
    p.add_argument(
        "--format",
        default="bestvideo[ext=webm]+bestaudio[ext=webm]/bestvideo+bestaudio",
        help="yt-dlp format selector string",
    )

    # Subtitles
    p.add_argument("--no-subs", action="store_true", help="Disable subtitle download")
    p.add_argument("--subs-lang", default="en", help="Subtitle language (default: en)")
    p.add_argument("--subs-format", default="srt", help="Subtitle format (default: srt)")

    # Cookies / auth
    p.add_argument("--cookies-file", default=None, help="Path to cookies.txt (Netscape format)")
    p.add_argument(
        "--cookies-from-browser",
        default=None,
        help='Import cookies from browser, e.g. chrome or "chrome:Profile 1"',
    )

    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logger = _build_logger(args.verbose)

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
    )

    app = PlaylistDownloadApp(
        info_provider=YtDlpPlaylistInfoProvider(logger),
        sanitizer=DefaultPathSanitizer(),
        downloader=YtDlpPlaylistDownloader(logger, YtDlpOptionsFactory(logger)),
        logger=logger,
    )

    try:
        folder = app.run(job)
        logger.info(f"Download completed! Files saved to: {folder}")
        return 0
    except Exception as e:
        logger.error(f"Failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
