"""
Video source clients for fetching royalty-free satisfying clips.

Supports Pexels and Pixabay APIs. Both are free, no attribution required.
Tracks used video IDs to avoid duplicates across runs.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Curated search queries for oddly satisfying content
SATISFYING_QUERIES = [
    "satisfying", "oddly satisfying", "cleaning", "power washing",
    "soap cutting", "paint mixing", "cake decorating", "kinetic sand",
    "slime", "hydraulic press", "laser cutting", "sand art",
    "chocolate making", "glass blowing", "pottery wheel", "carpet cleaning",
    "pressure washing", "wax melting", "ice crushing", "food cutting",
    "calligraphy", "resin art", "wood turning", "metal polishing",
]


@dataclass
class VideoClip:
    """A downloaded video clip with metadata."""
    source: str          # "pexels" or "pixabay"
    video_id: str        # unique ID from the source
    url: str             # download URL
    local_path: Path     # path after download
    duration: float      # seconds
    width: int
    height: int
    query: str           # search query that found this


class UsedVideoTracker:
    """Track which videos have been used to avoid duplicates."""

    def __init__(self, path: Path):
        self._path = path
        self._used: set[str] = set()
        if path.exists():
            self._used = set(json.loads(path.read_text(encoding="utf-8")))

    def is_used(self, source: str, video_id: str) -> bool:
        return f"{source}:{video_id}" in self._used

    def mark_used(self, source: str, video_id: str) -> None:
        self._used.add(f"{source}:{video_id}")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(sorted(self._used)), encoding="utf-8")


class PexelsClient:
    """Fetch videos from Pexels API (free, 200 req/hour)."""

    BASE_URL = "https://api.pexels.com/videos/search"

    def __init__(self, api_key: str):
        self._key = api_key
        self._session = requests.Session()
        self._session.headers["Authorization"] = api_key

    def search(
        self,
        query: str,
        per_page: int = 10,
        min_duration: int = 5,
        max_duration: int = 30,
        orientation: str = "landscape",
    ) -> list[dict]:
        """Search for videos. Returns raw API results."""
        resp = self._session.get(
            self.BASE_URL,
            params={
                "query": query,
                "per_page": per_page,
                "min_duration": min_duration,
                "max_duration": max_duration,
                "orientation": orientation,
                "size": "medium",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("videos", [])

    def get_best_file(self, video: dict) -> Optional[dict]:
        """Get the best quality video file from a Pexels video result."""
        files = video.get("video_files", [])
        # Prefer HD (1920x1080) or higher, then sort by quality
        hd_files = [f for f in files if f.get("height", 0) >= 720]
        if not hd_files:
            hd_files = files
        if not hd_files:
            return None
        # Sort by resolution descending
        hd_files.sort(key=lambda f: f.get("height", 0), reverse=True)
        return hd_files[0]

    def download(self, url: str, output_path: Path) -> Path:
        """Download a video file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        resp = self._session.get(url, stream=True)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return output_path


class PixabayClient:
    """Fetch videos from Pixabay API (free, unlimited)."""

    BASE_URL = "https://pixabay.com/api/videos/"

    def __init__(self, api_key: str):
        self._key = api_key

    def search(
        self,
        query: str,
        per_page: int = 10,
        min_duration: int = 5,
        max_duration: int = 30,
    ) -> list[dict]:
        """Search for videos."""
        resp = requests.get(
            self.BASE_URL,
            params={
                "key": self._key,
                "q": query,
                "per_page": per_page,
                "video_type": "film",
                "min_width": 1280,
                "order": "popular",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        # Filter by duration
        hits = data.get("hits", [])
        return [
            h for h in hits
            if min_duration <= h.get("duration", 0) <= max_duration
        ]

    def get_best_file(self, video: dict) -> Optional[str]:
        """Get the best quality download URL."""
        videos = video.get("videos", {})
        for quality in ["large", "medium", "small"]:
            info = videos.get(quality, {})
            if info.get("url"):
                return info["url"]
        return None

    def download(self, url: str, output_path: Path) -> Path:
        """Download a video file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(url, stream=True)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return output_path


def fetch_satisfying_clips(
    pexels_key: str = "",
    pixabay_key: str = "",
    count: int = 4,
    query: str = "",
    output_dir: Path = Path("shorts_clips"),
    tracker: Optional[UsedVideoTracker] = None,
) -> list[VideoClip]:
    """Fetch satisfying video clips from available sources.

    Downloads `count` clips, avoiding duplicates via the tracker.
    If no query specified, picks randomly from SATISFYING_QUERIES.
    """
    import random

    if not query:
        query = random.choice(SATISFYING_QUERIES)

    clips: list[VideoClip] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    # Try Pexels first
    if pexels_key:
        logger.info("Searching Pexels for '%s'...", query)
        client = PexelsClient(pexels_key)
        try:
            results = client.search(query, per_page=count * 3)
            for video in results:
                if len(clips) >= count:
                    break
                vid = str(video["id"])
                if tracker and tracker.is_used("pexels", vid):
                    continue
                best = client.get_best_file(video)
                if not best:
                    continue
                url = best["link"]
                fname = f"pexels_{vid}.mp4"
                local = output_dir / fname
                if not local.exists():
                    logger.info("  Downloading pexels/%s...", vid)
                    client.download(url, local)
                clips.append(VideoClip(
                    source="pexels", video_id=vid, url=url,
                    local_path=local,
                    duration=video.get("duration", 10),
                    width=best.get("width", 1920),
                    height=best.get("height", 1080),
                    query=query,
                ))
                if tracker:
                    tracker.mark_used("pexels", vid)
        except Exception as e:
            logger.warning("Pexels search failed: %s", e)

    # Fill remaining from Pixabay
    if pixabay_key and len(clips) < count:
        remaining = count - len(clips)
        logger.info("Searching Pixabay for '%s' (%d more)...", query, remaining)
        client = PixabayClient(pixabay_key)
        try:
            results = client.search(query, per_page=remaining * 3)
            for video in results:
                if len(clips) >= count:
                    break
                vid = str(video["id"])
                if tracker and tracker.is_used("pixabay", vid):
                    continue
                url = client.get_best_file(video)
                if not url:
                    continue
                fname = f"pixabay_{vid}.mp4"
                local = output_dir / fname
                if not local.exists():
                    logger.info("  Downloading pixabay/%s...", vid)
                    client.download(url, local)
                clips.append(VideoClip(
                    source="pixabay", video_id=vid, url=url,
                    local_path=local,
                    duration=video.get("duration", 10),
                    width=1920, height=1080,
                    query=query,
                ))
                if tracker:
                    tracker.mark_used("pixabay", vid)
        except Exception as e:
            logger.warning("Pixabay search failed: %s", e)

    logger.info("Fetched %d clips for '%s'", len(clips), query)
    return clips
