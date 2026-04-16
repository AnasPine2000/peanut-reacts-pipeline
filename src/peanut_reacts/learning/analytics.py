"""YouTube Data API analytics fetcher.

Pulls rich per-video metrics for our uploaded content:
- view_count, like_count, comment_count, favorite_count
- duration, tags, title, description, category
- thumbnails, published_at
- For authenticated owner: revenue proxies, impressions (if YouTube Analytics API available)

The YouTube Analytics API requires additional scopes beyond upload.
For now we use Data API v3 which gives us the public metrics.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def fetch_video_metrics(service, video_id: str) -> Optional[dict]:
    """Fetch public metrics for a single video via YouTube Data API."""
    try:
        resp = service.videos().list(
            part="snippet,statistics,contentDetails,status",
            id=video_id,
        ).execute()

        items = resp.get("items", [])
        if not items:
            return None

        v = items[0]
        snippet = v.get("snippet", {})
        stats = v.get("statistics", {})
        details = v.get("contentDetails", {})

        return {
            "video_id": video_id,
            "title": snippet.get("title", ""),
            "description": snippet.get("description", "")[:500],
            "tags": snippet.get("tags", []),
            "published_at": snippet.get("publishedAt", ""),
            "category_id": snippet.get("categoryId", ""),
            "duration": details.get("duration", ""),
            "definition": details.get("definition", ""),
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
            "favorite_count": int(stats.get("favoriteCount", 0)),
            "privacy": v.get("status", {}).get("privacyStatus", ""),
            "made_for_kids": v.get("status", {}).get("madeForKids", False),
            "fetched_at": datetime.utcnow().isoformat(),
            "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
            "url": f"https://youtube.com/watch?v={video_id}",
        }
    except Exception as e:
        log.error("Failed to fetch metrics for %s: %s", video_id, e)
        return None


def fetch_our_recent_videos(service, max_results: int = 50) -> list[dict]:
    """Fetch all recent videos from the authenticated channel with their stats."""
    try:
        # Get channel uploads playlist
        ch_resp = service.channels().list(part="contentDetails", mine=True).execute()
        items = ch_resp.get("items", [])
        if not items:
            return []

        uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # Get video IDs from uploads playlist
        video_ids = []
        next_token = None
        while len(video_ids) < max_results:
            pl_resp = service.playlistItems().list(
                part="contentDetails",
                playlistId=uploads_playlist,
                maxResults=50,
                pageToken=next_token,
            ).execute()
            for item in pl_resp.get("items", []):
                video_ids.append(item["contentDetails"]["videoId"])
                if len(video_ids) >= max_results:
                    break
            next_token = pl_resp.get("nextPageToken")
            if not next_token:
                break

        log.info("Found %d recent videos", len(video_ids))

        # Batch fetch video stats (up to 50 per API call)
        all_metrics = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            resp = service.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(batch),
            ).execute()

            for v in resp.get("items", []):
                snippet = v.get("snippet", {})
                stats = v.get("statistics", {})
                details = v.get("contentDetails", {})
                all_metrics.append({
                    "video_id": v["id"],
                    "title": snippet.get("title", ""),
                    "description": snippet.get("description", "")[:500],
                    "tags": snippet.get("tags", []),
                    "published_at": snippet.get("publishedAt", ""),
                    "category_id": snippet.get("categoryId", ""),
                    "duration": details.get("duration", ""),
                    "view_count": int(stats.get("viewCount", 0)),
                    "like_count": int(stats.get("likeCount", 0)),
                    "comment_count": int(stats.get("commentCount", 0)),
                    "fetched_at": datetime.utcnow().isoformat(),
                    "url": f"https://youtube.com/watch?v={v['id']}",
                })

        return all_metrics
    except Exception as e:
        log.error("Failed to fetch recent videos: %s", e)
        return []


def parse_iso_duration(iso: str) -> float:
    """Parse ISO 8601 duration (PT1H23M45S) to seconds."""
    import re
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    h = int(m.group(1) or 0)
    m_ = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + m_ * 60 + s


def compute_engagement_rate(metrics: dict) -> float:
    """Compute likes + comments / views as an engagement proxy."""
    views = metrics.get("view_count", 0)
    if views == 0:
        return 0
    engagement = metrics.get("like_count", 0) + metrics.get("comment_count", 0) * 2
    return engagement / views
