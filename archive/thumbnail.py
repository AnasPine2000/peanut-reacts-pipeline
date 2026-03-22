#!/usr/bin/env python3
"""
playlist_thumbnails_force_jpg.py

Download thumbnails for all videos in a YouTube playlist and FORCE saving as JPG.

Why this exists:
- YouTube often serves thumbnails as .webp/.png.
- yt-dlp will happily save those formats unless conversion runs.
- This script guarantees the final saved files are always .jpg by
  downloading the thumbnail URL and encoding it as JPEG.

Folder layout:
  <output_root>/<playlist_name>/thumbnails/
      001 - Video Title.jpg
      002 - Video Title.jpg
      ...
  Also saves the playlist thumbnail (if available) as:
      000 - <playlist_name>.jpg

Requirements:
  pip install -U yt-dlp pillow

Notes:
- Pillow supports WebP on Windows wheels (usually works out-of-the-box).
- If you use cookies, pass either --cookies-file or --cookies-from-browser.
"""

from __future__ import annotations

import argparse
import io
import logging
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple, runtime_checkable

import yt_dlp
from PIL import Image


# ----------------------------
# Domain models
# ----------------------------

@dataclass(frozen=True)
class Job:
    playlist_url: str
    output_root: Path
    thumbnails_folder_name: str = "thumbnails"
    include_index_prefix: bool = True
    timeout_sec: int = 30
    retries: int = 3
    sleep_between_retries_sec: float = 0.8
    cookies_file: Optional[Path] = None
    cookies_from_browser: Optional[str] = None  # e.g. "chrome" or "chrome:Profile 1"


@dataclass(frozen=True)
class PlaylistInfo:
    title: str
    identifier: str


@dataclass(frozen=True)
class VideoEntry:
    playlist_index: int
    title: str
    thumbnail_url: Optional[str]


# ----------------------------
# Abstractions (DIP)
# ----------------------------

@runtime_checkable
class IPlaylistExtractor(Protocol):
    def extract(self, job: Job) -> tuple[PlaylistInfo, List[VideoEntry], Optional[str]]:
        """Returns (playlist_info, video_entries, playlist_thumbnail_url)."""
        ...


@runtime_checkable
class IPathSanitizer(Protocol):
    def sanitize(self, raw: str) -> str:
        ...


@runtime_checkable
class IHttpFetcher(Protocol):
    def get_bytes(self, url: str) -> bytes:
        ...


@runtime_checkable
class IImageStore(Protocol):
    def save_jpg(self, image_bytes: bytes, output_path: Path) -> None:
        ...


# ----------------------------
# Utilities
# ----------------------------

class DefaultPathSanitizer(IPathSanitizer):
    """Cross-platform safe-ish filename/folder sanitizer (Windows-friendly)."""
    _illegal = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
    _ws = re.compile(r"\s+")

    def sanitize(self, raw: str) -> str:
        name = raw.strip()
        name = self._illegal.sub(" ", name)
        name = self._ws.sub(" ", name).strip()
        name = name.rstrip(". ").strip()
        return name or "playlist"


def _parse_cookies_from_browser(spec: str) -> Tuple[str, ...]:
    """
    yt-dlp Python API expects cookiesfrombrowser as a tuple, not a string.
    Examples:
      "chrome"           -> ("chrome",)
      "chrome:Profile 1" -> ("chrome", "Profile 1")
    """
    spec = spec.strip().strip('"').strip("'")
    if ":" in spec:
        b, profile = spec.split(":", 1)
        b = b.strip()
        profile = profile.strip()
        return (b, profile) if profile else (b,)
    return (spec,)


def _best_thumbnail_url(info_dict: Dict[str, Any]) -> Optional[str]:
    """
    Select the best available thumbnail URL from a video info dict.
    Prefer highest resolution from 'thumbnails' list; fallback to 'thumbnail'.
    """
    thumbs = info_dict.get("thumbnails") or []
    best_url = None
    best_score = -1

    for t in thumbs:
        url = t.get("url")
        if not url:
            continue
        w = t.get("width") or 0
        h = t.get("height") or 0
        score = (w * h) if (w and h) else 0
        # If no sizes, still consider it.
        if score >= best_score:
            best_score = score
            best_url = url

    return best_url or info_dict.get("thumbnail")


# ----------------------------
# Implementations
# ----------------------------

class YtDlpPlaylistExtractor(IPlaylistExtractor):
    """
    Extract playlist metadata + entries via yt-dlp (no downloads).
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def extract(self, job: Job) -> tuple[PlaylistInfo, List[VideoEntry], Optional[str]]:
        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,   # we want thumbnails in entries
            "noplaylist": False,
        }

        if job.cookies_file:
            ydl_opts["cookiefile"] = str(job.cookies_file)
        if job.cookies_from_browser:
            ydl_opts["cookiesfrombrowser"] = _parse_cookies_from_browser(job.cookies_from_browser)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(job.playlist_url, download=False)

        title = (info.get("title") or info.get("playlist_title") or "playlist").strip()
        identifier = str(info.get("id") or info.get("playlist_id") or "unknown")
        playlist_thumb_url = _best_thumbnail_url(info)

        entries = info.get("entries") or []
        video_entries: List[VideoEntry] = []

        # Some entries can be None when ignoreerrors-like behavior occurs upstream.
        idx = 0
        for e in entries:
            if not e:
                continue
            idx += 1
            playlist_index = int(e.get("playlist_index") or idx)
            video_title = (e.get("title") or f"video-{playlist_index}").strip()
            thumb_url = _best_thumbnail_url(e)
            video_entries.append(VideoEntry(playlist_index=playlist_index, title=video_title, thumbnail_url=thumb_url))

        return PlaylistInfo(title=title, identifier=identifier), video_entries, playlist_thumb_url


class UrllibFetcher(IHttpFetcher):
    """
    Simple HTTP fetcher with retries.
    Uses urllib to avoid extra dependencies.
    """

    def __init__(self, logger: logging.Logger, timeout_sec: int, retries: int, sleep_sec: float) -> None:
        self._log = logger
        self._timeout = timeout_sec
        self._retries = retries
        self._sleep = sleep_sec

    def get_bytes(self, url: str) -> bytes:
        last_exc: Optional[Exception] = None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        req = urllib.request.Request(url, headers=headers)

        for attempt in range(1, self._retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    return resp.read()
            except Exception as e:
                last_exc = e
                self._log.warning(f"Fetch failed (attempt {attempt}/{self._retries}): {e}")
                if attempt < self._retries:
                    time.sleep(self._sleep)

        raise RuntimeError(f"Failed to download URL after {self._retries} attempts: {url}") from last_exc


class PillowJpegStore(IImageStore):
    """
    Converts image bytes (webp/png/jpg/etc) to JPEG and writes output_path.
    Guarantees output is .jpg encoded.
    """

    def __init__(self, logger: logging.Logger, jpeg_quality: int = 92) -> None:
        self._log = logger
        self._quality = max(1, min(95, jpeg_quality))

    def save_jpg(self, image_bytes: bytes, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(io.BytesIO(image_bytes)) as img:
            # JPEG doesn't support alpha; convert safely
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                img = img.convert("RGBA")
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")

            img.save(output_path, format="JPEG", quality=self._quality, optimize=True)

        self._log.debug(f"Wrote JPG: {output_path}")


# ----------------------------
# Orchestration (SOLID-ish)
# ----------------------------

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

    def run(self, job: Job) -> Path:
        playlist_info, videos, playlist_thumb_url = self._extractor.extract(job)

        playlist_folder = self._sanitizer.sanitize(playlist_info.title)
        if playlist_folder.lower() in {"playlist", "unknown"} and playlist_info.identifier:
            playlist_folder = f"{playlist_folder}-{playlist_info.identifier}"

        playlist_dir = job.output_root / playlist_folder
        thumbs_dir = playlist_dir / job.thumbnails_folder_name
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        self._log.info(f"Saving thumbnails into: {thumbs_dir}")

        # Save playlist thumbnail as 000 - <playlist>.jpg if available
        if playlist_thumb_url:
            pl_name = self._sanitizer.sanitize(playlist_info.title)
            pl_out = thumbs_dir / f"000 - {pl_name}.jpg"
            if not pl_out.exists():
                try:
                    data = self._fetcher.get_bytes(playlist_thumb_url)
                    self._store.save_jpg(data, pl_out)
                except Exception as e:
                    self._log.warning(f"Failed playlist thumbnail: {e}")

        # Save each video thumbnail as NNN - Title.jpg
        for v in videos:
            safe_title = self._sanitizer.sanitize(v.title)
            prefix = f"{v.playlist_index:03d} - " if job.include_index_prefix else ""
            jpg_path = thumbs_dir / f"{prefix}{safe_title}.jpg"

            if jpg_path.exists():
                continue

            if not v.thumbnail_url:
                self._log.warning(f"No thumbnail URL for #{v.playlist_index}: {v.title}")
                continue

            try:
                data = self._fetcher.get_bytes(v.thumbnail_url)
                self._store.save_jpg(data, jpg_path)

                # Cleanup: remove any non-jpg variants with same stem
                stem = jpg_path.with_suffix("").name
                for ext in (".webp", ".png", ".jpeg", ".jfif", ".bmp", ".gif", ".avif"):
                    p = thumbs_dir / f"{stem}{ext}"
                    if p.exists():
                        try:
                            p.unlink()
                        except Exception:
                            pass

                self._log.info(f"Saved: {jpg_path.name}")
            except Exception as e:
                self._log.warning(f"Failed thumbnail #{v.playlist_index}: {e}")

        # Extra cleanup: remove leftover .webp in folder (from older runs)
        for p in thumbs_dir.glob("*.webp"):
            try:
                p.unlink()
            except Exception:
                pass

        self._log.info("Done. All thumbnails are forced to .jpg.")
        return thumbs_dir


# ----------------------------
# CLI
# ----------------------------

def build_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("playlist_thumbs_force_jpg")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        h.setLevel(logging.DEBUG if verbose else logging.INFO)
        logger.addHandler(h)
    return logger


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Force-download playlist thumbnails as JPG.")
    p.add_argument("playlist_url", nargs="?", help="Playlist URL (optional; prompts if missing)")
    p.add_argument("-o", "--output", default=str(Path.cwd() / "downloads"), help="Output root folder")
    p.add_argument("--thumbs-folder", default="thumbnails", help='Thumbnails folder name (default: "thumbnails")')
    p.add_argument("--no-index", action="store_true", help="Do not prefix files with playlist index")
    p.add_argument("--cookies-file", default=None, help="Path to cookies.txt (Netscape format)")
    p.add_argument("--cookies-from-browser", default=None, help='e.g. chrome or "chrome:Profile 1"')
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log = build_logger(args.verbose)

    url = (args.playlist_url or "").strip()
    if not url:
        url = input("Enter the playlist URL: ").strip()
        if not url:
            log.error("No URL provided.")
            return 2

    output_root = Path(args.output).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    cookies_file = Path(args.cookies_file).expanduser().resolve() if args.cookies_file else None

    job = Job(
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
        log.info(f"Final JPG thumbnails saved to: {out_dir}")
        return 0
    except Exception as e:
        log.error(f"Failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
