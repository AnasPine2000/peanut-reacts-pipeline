"""Content discovery — find new videos to process across all sources.

Sources supported:
- YouTube playlists
- YouTube channels (latest uploads)
- YouTube search (by query)
- YouTube trending (by category)
- Twitch channels (latest VODs)
- Reddit (top posts from subreddits)
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class DiscoveredVideo:
    id: str
    title: str
    url: str
    source_type: str  # youtube | twitch | reddit
    source_id: str  # playlist_id, channel_id, subreddit, etc.
    duration_seconds: float = 0
    upload_date: str = ""
    view_count: int = 0
    uploader: str = ""


# ── YouTube Discovery ──────────────────────────────────────────────

def discover_youtube_playlist(playlist_url: str, max_results: int = 20) -> list[DiscoveredVideo]:
    """List videos in a YouTube playlist."""
    log.info("Discovering playlist: %s", playlist_url[:60])
    try:
        result = subprocess.run(
            [
                "yt-dlp", "--flat-playlist",
                "--print", "%(id)s\t%(title)s\t%(duration)s\t%(uploader)s",
                "--playlist-end", str(max_results),
                playlist_url,
            ],
            capture_output=True, text=True, timeout=120,
        )
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            vid_id = parts[0]
            title = parts[1]
            duration = float(parts[2]) if len(parts) > 2 and parts[2] and parts[2] != "NA" else 0
            uploader = parts[3] if len(parts) > 3 else ""
            videos.append(DiscoveredVideo(
                id=vid_id, title=title,
                url=f"https://youtube.com/watch?v={vid_id}",
                source_type="youtube", source_id=playlist_url,
                duration_seconds=duration, uploader=uploader,
            ))
        log.info("Found %d videos in playlist", len(videos))
        return videos
    except Exception as e:
        log.error("Failed to discover playlist: %s", e)
        return []


def discover_youtube_channel(channel_url: str, max_results: int = 10) -> list[DiscoveredVideo]:
    """Get the latest videos from a YouTube channel."""
    log.info("Discovering channel: %s", channel_url[:60])
    try:
        # Use /videos tab for latest uploads
        url = channel_url.rstrip("/")
        if not url.endswith("/videos"):
            url += "/videos"

        result = subprocess.run(
            [
                "yt-dlp", "--flat-playlist",
                "--print", "%(id)s\t%(title)s\t%(duration)s\t%(upload_date)s",
                "--playlist-end", str(max_results),
                url,
            ],
            capture_output=True, text=True, timeout=120,
        )
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            vid_id = parts[0]
            title = parts[1]
            duration = float(parts[2]) if len(parts) > 2 and parts[2] and parts[2] != "NA" else 0
            upload_date = parts[3] if len(parts) > 3 else ""
            videos.append(DiscoveredVideo(
                id=vid_id, title=title,
                url=f"https://youtube.com/watch?v={vid_id}",
                source_type="youtube", source_id=channel_url,
                duration_seconds=duration, upload_date=upload_date,
            ))
        log.info("Found %d videos from channel", len(videos))
        return videos
    except Exception as e:
        log.error("Failed to discover channel: %s", e)
        return []


def discover_youtube_search(query: str, max_results: int = 10) -> list[DiscoveredVideo]:
    """Search YouTube by query and return matching videos."""
    log.info("Searching YouTube: %s", query)
    try:
        result = subprocess.run(
            [
                "yt-dlp", f"ytsearch{max_results}:{query}",
                "--flat-playlist",
                "--print", "%(id)s\t%(title)s\t%(duration)s\t%(uploader)s",
            ],
            capture_output=True, text=True, timeout=60,
        )
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            videos.append(DiscoveredVideo(
                id=parts[0], title=parts[1],
                url=f"https://youtube.com/watch?v={parts[0]}",
                source_type="youtube", source_id=f"search:{query}",
                duration_seconds=float(parts[2]) if len(parts) > 2 and parts[2] and parts[2] != "NA" else 0,
                uploader=parts[3] if len(parts) > 3 else "",
            ))
        return videos
    except Exception as e:
        log.error("Search failed: %s", e)
        return []


# ── Twitch Discovery ───────────────────────────────────────────────

def discover_twitch_vods(channel_name: str, max_results: int = 3) -> list[DiscoveredVideo]:
    """Get latest VODs from a Twitch channel."""
    log.info("Discovering Twitch VODs: %s", channel_name)
    try:
        result = subprocess.run(
            [
                "yt-dlp", "--flat-playlist",
                "--print", "%(id)s\t%(title)s\t%(duration)s\t%(upload_date)s",
                "--playlist-end", str(max_results),
                f"https://www.twitch.tv/{channel_name}/videos?filter=archives&sort=time",
            ],
            capture_output=True, text=True, timeout=60,
        )
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            videos.append(DiscoveredVideo(
                id=parts[0], title=parts[1],
                url=f"https://www.twitch.tv/videos/{parts[0]}",
                source_type="twitch", source_id=channel_name,
                duration_seconds=float(parts[2]) if len(parts) > 2 and parts[2] and parts[2] != "NA" else 0,
                upload_date=parts[3] if len(parts) > 3 else "",
                uploader=channel_name,
            ))
        log.info("Found %d Twitch VODs", len(videos))
        return videos
    except Exception as e:
        log.error("Failed to discover Twitch VODs: %s", e)
        return []


# ── Reddit Discovery ───────────────────────────────────────────────

def discover_reddit_posts(
    subreddit: str,
    min_score: int = 5000,
    max_results: int = 10,
) -> list[DiscoveredVideo]:
    """Get top posts from a subreddit via Reddit JSON API (no auth needed)."""
    log.info("Discovering r/%s", subreddit)
    try:
        import httpx
        resp = httpx.get(
            f"https://www.reddit.com/r/{subreddit}/top.json?t=day&limit={max_results}",
            headers={"User-Agent": "PeanutReacts/1.0"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        posts = []
        for child in data.get("data", {}).get("children", []):
            post = child["data"]
            if post.get("score", 0) < min_score:
                continue
            if post.get("stickied") or post.get("over_18"):
                continue
            posts.append(DiscoveredVideo(
                id=post["id"],
                title=post["title"],
                url=f"https://reddit.com{post['permalink']}",
                source_type="reddit",
                source_id=subreddit,
                view_count=post.get("score", 0),
                uploader=post.get("author", ""),
            ))
        log.info("Found %d Reddit posts in r/%s (score >= %d)", len(posts), subreddit, min_score)
        return posts
    except Exception as e:
        log.error("Failed to discover Reddit posts: %s", e)
        return []


# ── Filtering & Deduplication ──────────────────────────────────────

def filter_new_videos(
    videos: list[DiscoveredVideo],
    processed_ids: set[str],
) -> list[DiscoveredVideo]:
    """Filter out already-processed videos."""
    new = [v for v in videos if v.id not in processed_ids]
    log.info("Filtered %d -> %d new videos", len(videos), len(new))
    return new


def filter_by_duration(
    videos: list[DiscoveredVideo],
    min_minutes: float = 5,
    max_minutes: float = 600,
) -> list[DiscoveredVideo]:
    """Filter videos by duration."""
    min_sec = min_minutes * 60
    max_sec = max_minutes * 60
    return [v for v in videos if min_sec <= v.duration_seconds <= max_sec]


# ── Unified Discovery Dispatcher ───────────────────────────────────

def discover_for_channel(channel_config) -> list[DiscoveredVideo]:
    """Discover videos for any channel configuration.

    Uses the channel's source_type and source_urls/subreddits.
    """
    source_type = channel_config.source_type
    videos: list[DiscoveredVideo] = []

    if source_type == "playlist":
        for url in channel_config.source_urls:
            videos.extend(discover_youtube_playlist(url, max_results=20))

    elif source_type == "youtube_channel":
        for url in channel_config.source_urls:
            videos.extend(discover_youtube_channel(url, max_results=10))

    elif source_type == "twitch_vod":
        for name in channel_config.source_urls:
            videos.extend(discover_twitch_vods(name, max_results=3))

    elif source_type == "reddit":
        for sub in channel_config.subreddits:
            videos.extend(discover_reddit_posts(
                sub, min_score=channel_config.min_score, max_results=10,
            ))

    else:
        log.warning("Unknown source_type: %s", source_type)

    return videos
