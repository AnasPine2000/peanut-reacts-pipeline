"""
YouTube playlist thumbnail downloader.

Downloads thumbnails for all videos in a playlist and forces saving as JPEG.
Refactored from archive/thumbnail.py following SOLID principles.
"""

from __future__ import annotations

import io
import logging
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import yt_dlp
from PIL import Image

from peanut_reacts.core.cookies import parse_cookies_from_browser
from peanut_reacts.core.path_sanitizer import DefaultPathSanitizer, IPathSanitizer


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ThumbnailJob:
    playlist_url: str
    output_root: Path
    thumbnails_folder_name: str = "thumbnails"
    include_index_prefix: bool = True
    timeout_sec: int = 30
    retries: int = 3
    sleep_between_retries_sec: float = 0.8
    cookies_file: Optional[Path] = None
    cookies_from_browser: Optional[str] = None


@dataclass(frozen=True)
class PlaylistInfo:
    title: str
    identifier: str


@dataclass(frozen=True)
class VideoEntry:
    playlist_index: int
    title: str
    thumbnail_url: Optional[str]


# ---------------------------------------------------------------------------
# Abstractions
# ---------------------------------------------------------------------------

@runtime_checkable
class IPlaylistExtractor(Protocol):
    def extract(self, job: ThumbnailJob) -> tuple[PlaylistInfo, List[VideoEntry], Optional[str]]: ...


@runtime_checkable
class IHttpFetcher(Protocol):
    def get_bytes(self, url: str) -> bytes: ...


@runtime_checkable
class IImageStore(Protocol):
    def save_jpg(self, image_bytes: bytes, output_path: Path) -> None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _best_thumbnail_url(info_dict: Dict[str, Any]) -> Optional[str]:
    """Select highest-resolution thumbnail URL from a video info dict."""
    thumbs = info_dict.get("thumbnails") or []
    best_url, best_score = None, -1

    for t in thumbs:
        url = t.get("url")
        if not url:
            continue
        w = t.get("width") or 0
        h = t.get("height") or 0
        score = (w * h) if (w and h) else 0
        if score >= best_score:
            best_score = score
            best_url = url

    return best_url or info_dict.get("thumbnail")


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

class YtDlpPlaylistExtractor:
    """Extract playlist metadata + entries via yt-dlp (no downloads)."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def extract(self, job: ThumbnailJob) -> tuple[PlaylistInfo, List[VideoEntry], Optional[str]]:
        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "noplaylist": False,
        }
        if job.cookies_file:
            ydl_opts["cookiefile"] = str(job.cookies_file)
        if job.cookies_from_browser:
            ydl_opts["cookiesfrombrowser"] = parse_cookies_from_browser(job.cookies_from_browser)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(job.playlist_url, download=False)

        title = (info.get("title") or info.get("playlist_title") or "playlist").strip()
        identifier = str(info.get("id") or info.get("playlist_id") or "unknown")
        playlist_thumb_url = _best_thumbnail_url(info)

        entries = info.get("entries") or []
        video_entries: List[VideoEntry] = []
        idx = 0
        for e in entries:
            if not e:
                continue
            idx += 1
            playlist_index = int(e.get("playlist_index") or idx)
            video_title = (e.get("title") or f"video-{playlist_index}").strip()
            thumb_url = _best_thumbnail_url(e)
            video_entries.append(VideoEntry(
                playlist_index=playlist_index, title=video_title, thumbnail_url=thumb_url,
            ))

        return PlaylistInfo(title=title, identifier=identifier), video_entries, playlist_thumb_url


class UrllibFetcher:
    """Simple HTTP fetcher with retries."""

    def __init__(self, logger: logging.Logger, timeout_sec: int, retries: int, sleep_sec: float) -> None:
        self._log = logger
        self._timeout = timeout_sec
        self._retries = retries
        self._sleep = sleep_sec

    def get_bytes(self, url: str) -> bytes:
        last_exc: Optional[Exception] = None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        req = urllib.request.Request(url, headers=headers)

        for attempt in range(1, self._retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    return resp.read()
            except Exception as e:
                last_exc = e
                self._log.warning("Fetch failed (attempt %d/%d): %s", attempt, self._retries, e)
                if attempt < self._retries:
                    time.sleep(self._sleep)

        raise RuntimeError(f"Failed after {self._retries} attempts: {url}") from last_exc


class PillowJpegStore:
    """Converts image bytes to JPEG and writes to disk."""

    def __init__(self, logger: logging.Logger, jpeg_quality: int = 92) -> None:
        self._log = logger
        self._quality = max(1, min(95, jpeg_quality))

    def save_jpg(self, image_bytes: bytes, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                img = img.convert("RGBA")
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")

            img.save(output_path, format="JPEG", quality=self._quality, optimize=True)

        self._log.debug("Wrote JPG: %s", output_path)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class ThumbnailApp:
    def __init__(
        self,
        extractor: IPlaylistExtractor,
        sanitizer: IPathSanitizer,
        fetcher: IHttpFetcher,
        image_store: IImageStore,
        logger: logging.Logger,
    ) -> None:
        self._extractor = extractor
        self._sanitizer = sanitizer
        self._fetcher = fetcher
        self._store = image_store
        self._log = logger

    def run(self, job: ThumbnailJob) -> Path:
        playlist_info, videos, playlist_thumb_url = self._extractor.extract(job)

        playlist_folder = self._sanitizer.sanitize(playlist_info.title)
        if playlist_folder.lower() in {"playlist", "unknown", "untitled"} and playlist_info.identifier:
            playlist_folder = f"{playlist_folder}-{playlist_info.identifier}"

        thumbs_dir = job.output_root / playlist_folder / job.thumbnails_folder_name
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        self._log.info("Saving thumbnails into: %s", thumbs_dir)

        # Playlist thumbnail
        if playlist_thumb_url:
            pl_name = self._sanitizer.sanitize(playlist_info.title)
            pl_out = thumbs_dir / f"000 - {pl_name}.jpg"
            if not pl_out.exists():
                try:
                    data = self._fetcher.get_bytes(playlist_thumb_url)
                    self._store.save_jpg(data, pl_out)
                except Exception as e:
                    self._log.warning("Failed playlist thumbnail: %s", e)

        # Video thumbnails
        for v in videos:
            safe_title = self._sanitizer.sanitize(v.title)
            prefix = f"{v.playlist_index:03d} - " if job.include_index_prefix else ""
            jpg_path = thumbs_dir / f"{prefix}{safe_title}.jpg"

            if jpg_path.exists():
                continue
            if not v.thumbnail_url:
                self._log.warning("No thumbnail URL for #%d: %s", v.playlist_index, v.title)
                continue

            try:
                data = self._fetcher.get_bytes(v.thumbnail_url)
                self._store.save_jpg(data, jpg_path)
                self._log.info("Saved: %s", jpg_path.name)
            except Exception as e:
                self._log.warning("Failed thumbnail #%d: %s", v.playlist_index, e)

        self._log.info("Done. All thumbnails forced to .jpg.")
        return thumbs_dir
