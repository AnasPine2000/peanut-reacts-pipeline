"""
YouTube Data API v3 comment fetcher.

Fetches comments for a video using the YouTube Data API, which is faster
and more reliable than yt-dlp's --write-comments scraping approach.

Supports two authentication methods:
1. API key (simplest — works for public video comments)
2. OAuth 2.0 client credentials (for higher quotas or private videos)

Usage:
    fetcher = YouTubeCommentFetcher.from_api_key("YOUR_KEY")
    comments = fetcher.fetch_comments("VIDEO_ID", max_results=500)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from peanut_reacts.core.retry import retry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class YouTubeComment:
    """A single YouTube comment with metadata."""
    text: str
    author: str
    like_count: int
    published_at: str
    reply_count: int = 0
    is_reply: bool = False


# ---------------------------------------------------------------------------
# Video ID extraction
# ---------------------------------------------------------------------------

_YT_URL_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?.*v=|youtu\.be/)([a-zA-Z0-9_-]{11})"),
    re.compile(r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})"),
]


def extract_video_id(url_or_id: str) -> str:
    """Extract YouTube video ID from a URL or return as-is if already an ID."""
    url_or_id = url_or_id.strip()
    if len(url_or_id) == 11 and re.match(r"^[a-zA-Z0-9_-]+$", url_or_id):
        return url_or_id
    for pattern in _YT_URL_PATTERNS:
        match = pattern.search(url_or_id)
        if match:
            return match.group(1)
    raise ValueError(f"Cannot extract video ID from: {url_or_id}")


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class YouTubeCommentFetcher:
    """Fetch comments from YouTube videos via the Data API v3."""

    def __init__(self, youtube_service) -> None:
        self._yt = youtube_service

    @classmethod
    def from_api_key(cls, api_key: str) -> YouTubeCommentFetcher:
        """Create a fetcher using a simple API key."""
        service = build("youtube", "v3", developerKey=api_key)
        return cls(service)

    @classmethod
    def from_oauth(cls, client_secrets_path: str | Path) -> YouTubeCommentFetcher:
        """Create a fetcher using OAuth 2.0 (shared token with upload module)."""
        from peanut_reacts.upload.youtube_auth import get_authenticated_service
        service = get_authenticated_service(client_secrets_path)
        return cls(service)

    @retry(max_attempts=3, delay=2.0, exceptions=(HttpError, ConnectionError, TimeoutError))
    def fetch_video_info(self, video_id: str) -> dict:
        """Fetch basic video metadata (title, duration, etc.)."""
        response = self._yt.videos().list(
            part="snippet,contentDetails,statistics",
            id=video_id,
        ).execute()

        items = response.get("items", [])
        if not items:
            raise ValueError(f"Video not found: {video_id}")

        item = items[0]
        snippet = item["snippet"]
        stats = item.get("statistics", {})

        return {
            "id": video_id,
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "description": snippet.get("description", ""),
            "published_at": snippet.get("publishedAt", ""),
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
            "duration": item["contentDetails"].get("duration", ""),
        }

    def fetch_comments(
        self,
        video_id: str,
        *,
        max_results: int = 500,
        include_replies: bool = False,
        order: str = "relevance",
    ) -> list[YouTubeComment]:
        """Fetch top-level comments for a video.

        Args:
            video_id: YouTube video ID
            max_results: Maximum number of comments to fetch
            include_replies: Whether to also fetch reply threads
            order: "relevance" (default) or "time"

        Returns:
            List of YouTubeComment objects sorted by like_count (descending)
        """
        comments: list[YouTubeComment] = []
        page_token: Optional[str] = None
        per_page = min(100, max_results)

        logger.info("Fetching comments for video %s (max %d) ...", video_id, max_results)

        while len(comments) < max_results:
            try:
                request = self._yt.commentThreads().list(
                    part="snippet,replies" if include_replies else "snippet",
                    videoId=video_id,
                    maxResults=per_page,
                    order=order,
                    pageToken=page_token,
                    textFormat="plainText",
                )
                response = request.execute()
            except HttpError as e:
                if e.resp.status == 403:
                    logger.warning("Comments disabled or quota exceeded for %s: %s", video_id, e)
                    break
                raise

            for item in response.get("items", []):
                top = item["snippet"]["topLevelComment"]["snippet"]
                comments.append(YouTubeComment(
                    text=top.get("textDisplay", ""),
                    author=top.get("authorDisplayName", ""),
                    like_count=int(top.get("likeCount", 0)),
                    published_at=top.get("publishedAt", ""),
                    reply_count=item["snippet"].get("totalReplyCount", 0),
                ))

                # Optionally fetch replies
                if include_replies and "replies" in item:
                    for reply in item["replies"]["comments"]:
                        r = reply["snippet"]
                        comments.append(YouTubeComment(
                            text=r.get("textDisplay", ""),
                            author=r.get("authorDisplayName", ""),
                            like_count=int(r.get("likeCount", 0)),
                            published_at=r.get("publishedAt", ""),
                            is_reply=True,
                        ))

            page_token = response.get("nextPageToken")
            if not page_token:
                break

            logger.debug("Fetched %d comments so far ...", len(comments))

        # Sort by likes (most popular first)
        comments.sort(key=lambda c: c.like_count, reverse=True)

        logger.info("Fetched %d comments for %s", len(comments), video_id)
        return comments[:max_results]

    def fetch_and_save(
        self,
        url_or_id: str,
        output_dir: Path,
        *,
        max_results: int = 500,
        include_replies: bool = False,
    ) -> Path:
        """Fetch comments and save as a pipeline-compatible .info.json file.

        The output format matches yt-dlp's info.json so the existing
        comment extraction pipeline works without changes.
        """
        video_id = extract_video_id(url_or_id)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Fetch video info
        video_info = self.fetch_video_info(video_id)
        logger.info("Video: %s", video_info["title"])

        # Fetch comments
        comments = self.fetch_comments(
            video_id,
            max_results=max_results,
            include_replies=include_replies,
        )

        # Build info.json in yt-dlp compatible format
        info_data = {
            "id": video_id,
            "title": video_info["title"],
            "channel": video_info["channel"],
            "duration": _parse_iso_duration(video_info["duration"]),
            "view_count": video_info["view_count"],
            "like_count": video_info["like_count"],
            "comment_count": video_info["comment_count"],
            "comments": [
                {
                    "text": c.text,
                    "author": c.author,
                    "like_count": c.like_count,
                    "timestamp": c.published_at,
                }
                for c in comments
            ],
        }

        # Sanitize filename
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', video_info["title"])[:80]
        output_path = output_dir / f"{safe_title}.info.json"
        output_path.write_text(
            json.dumps(info_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info("Saved %d comments to %s", len(comments), output_path.name)
        return output_path


def _parse_iso_duration(iso: str) -> float:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not match:
        return 0.0
    h, m, s = (int(x) if x else 0 for x in match.groups())
    return h * 3600 + m * 60 + s
