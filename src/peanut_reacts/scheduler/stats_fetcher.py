"""Fetch live YouTube stats for each channel using YouTube Data API v3.

Pulls: subscriber count, view count, video count, recent uploads,
channel description, thumbnail, creation date, country, banner.

Uses the channel's OAuth token to authenticate.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def fetch_channel_stats(channel_id: str, oauth_token_path: Path, client_secrets_path: Path) -> dict:
    """Fetch full stats for an authenticated YouTube channel.

    Runs in non-interactive mode — if a channel's OAuth token has expired
    and can't refresh, we return {} for that channel rather than opening a
    browser prompt that would hang the stats poller / daemon.
    """
    try:
        from peanut_reacts.upload.youtube_auth import get_authenticated_service
        service = get_authenticated_service(
            client_secrets_path,
            token_path=oauth_token_path,
            interactive=False,
        )

        # Get the authenticated channel
        response = service.channels().list(
            part="snippet,statistics,brandingSettings,contentDetails,status",
            mine=True,
        ).execute()

        items = response.get("items", [])
        if not items:
            log.warning("No channel found for %s", channel_id)
            return {}

        ch = items[0]
        snippet = ch.get("snippet", {})
        stats = ch.get("statistics", {})
        branding = ch.get("brandingSettings", {}).get("channel", {})

        # Get latest videos
        uploads_playlist = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
        latest_videos = []
        if uploads_playlist:
            pl_resp = service.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=uploads_playlist,
                maxResults=10,
            ).execute()

            # Get video statistics
            video_ids = [item["contentDetails"]["videoId"] for item in pl_resp.get("items", [])]
            if video_ids:
                videos_resp = service.videos().list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(video_ids),
                ).execute()

                for v in videos_resp.get("items", []):
                    vs = v.get("statistics", {})
                    vsn = v.get("snippet", {})
                    latest_videos.append({
                        "id": v["id"],
                        "title": vsn.get("title", ""),
                        "published_at": vsn.get("publishedAt", ""),
                        "thumbnail": vsn.get("thumbnails", {}).get("medium", {}).get("url", ""),
                        "view_count": int(vs.get("viewCount", 0)),
                        "like_count": int(vs.get("likeCount", 0)),
                        "comment_count": int(vs.get("commentCount", 0)),
                        "url": f"https://youtube.com/watch?v={v['id']}",
                        "duration": v.get("contentDetails", {}).get("duration", ""),
                    })

        handle = snippet.get("customUrl", "")
        if handle and not handle.startswith("@"):
            handle = "@" + handle

        return {
            "id": ch["id"],
            "title": snippet.get("title", ""),
            "handle": handle,
            "description": snippet.get("description", "")[:500],
            "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
            "banner": branding.get("image", {}).get("bannerExternalUrl", ""),
            "country": snippet.get("country", ""),
            "created_at": snippet.get("publishedAt", ""),
            "url": f"https://www.youtube.com/channel/{ch['id']}",
            "handle_url": f"https://www.youtube.com/{handle}" if handle else f"https://www.youtube.com/channel/{ch['id']}",
            "subscriber_count": int(stats.get("subscriberCount", 0)),
            "view_count": int(stats.get("viewCount", 0)),
            "video_count": int(stats.get("videoCount", 0)),
            "latest_videos": latest_videos,
            "fetched_at": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        log.error("Failed to fetch stats for %s: %s", channel_id, e)
        return {"error": str(e), "id": channel_id, "fetched_at": datetime.utcnow().isoformat()}


def fetch_all_channels_stats(pipeline_config) -> dict:
    """Fetch stats for all channels in the pipeline config."""
    all_stats = {}
    for channel in pipeline_config.channels:
        if not channel.oauth_token or not channel.client_secrets:
            log.debug("Skipping %s (no credentials)", channel.id)
            continue

        token_path = Path(channel.oauth_token).expanduser()
        secrets_path = Path(channel.client_secrets).expanduser()

        if not token_path.exists() or not secrets_path.exists():
            log.debug("Skipping %s (missing files): token=%s(%s) secrets=%s(%s)",
                      channel.id, token_path, token_path.exists(),
                      secrets_path, secrets_path.exists())
            continue

        log.info("Fetching stats for %s...", channel.id)
        stats = fetch_channel_stats(channel.id, token_path, secrets_path)
        all_stats[channel.id] = stats

    return all_stats
