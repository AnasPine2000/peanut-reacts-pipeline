"""Export enriched dashboard status and optionally push to Hugging Face Space.

Combines:
- Live YouTube stats (from API)
- Pipeline history (from SQLite)
- Channel config metadata
- Revenue tracking (projected vs actual)
- Character assignments
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


CHARACTER_PROFILES = {
    "peanut": {
        "name": "Peanut",
        "emoji": "🥜",
        "personality": "Excitable, loyal fan, hype reactions",
        "voice": "en-GB-RyanNeural",
        "color": "#D4A574",
        "origin": "A passionate peanut who loves watching Sidemen content",
        "catchphrase": "LET'S GOOO!",
    },
    "chilli": {
        "name": "Chilli",
        "emoji": "🌶️",
        "personality": "Hot takes, spicy opinions, argumentative",
        "voice": "en-US-GuyNeural",
        "color": "#E74C3C",
        "origin": "A politically-charged chilli pepper with strong opinions",
        "catchphrase": "THAT'S a HOT take.",
    },
    "marshmallow": {
        "name": "Marshmallow",
        "emoji": "☁️",
        "personality": "Chill, soft-spoken, cozy vibes",
        "voice": "en-US-JennyNeural",
        "color": "#F8F0E3",
        "origin": "A cloud-like marshmallow for chill vibes only",
        "catchphrase": "Just... vibe with me.",
    },
    "cashew": {
        "name": "Cashew",
        "emoji": "🥜",
        "personality": "Sarcastic, dry humor, witty narrator",
        "voice": "en-US-ChristopherNeural",
        "color": "#DAA520",
        "origin": "A cashew that's seen too much of Reddit",
        "catchphrase": "Oh, this should be GOOD.",
    },
    "pistachio": {
        "name": "Pistachio",
        "emoji": "🟢",
        "personality": "Cracked, unhinged, chaotic energy",
        "voice": "en-GB-ThomasNeural",
        "color": "#93C572",
        "origin": "A pistachio that's lost it watching KSI videos",
        "catchphrase": "I'M CRACKED!",
    },
    "walnut": {
        "name": "Walnut",
        "emoji": "🧠",
        "personality": "Brainy, analytical, detective vibes",
        "voice": "en-US-DavisNeural",
        "color": "#8B4513",
        "origin": "A walnut solving the mysteries of the Sidemen universe",
        "catchphrase": "Let's crack this open.",
    },
    "almond": {
        "name": "Almond",
        "emoji": "🔍",
        "personality": "Smooth, serious true crime narrator",
        "voice": "en-US-AndrewNeural",
        "color": "#DEB887",
        "origin": "An almond with a dark past and a smooth voice",
        "catchphrase": "And that's when things went wrong.",
    },
}


REVENUE_TARGETS = {
    "month_0": 0, "month_1": 0, "month_2": 0,
    "month_3": 70, "month_4": 165, "month_5": 395,
    "month_6": 670, "month_7": 1130, "month_8": 1660,
    "month_9": 2360, "month_10": 3410, "month_11": 4580, "month_12": 6200,
}


def build_status(
    pipeline_config,
    db,
    fetch_youtube_stats: bool = True,
    livestream_url: str = "",
    hf_space_url: str = "",
    output_path: Path = None,
) -> dict:
    """Build the enriched status.json for the dashboard."""
    from .stats_fetcher import fetch_all_channels_stats

    log.info("Building dashboard status...")

    # Pipeline stats from SQLite
    pipeline_stats = db.get_stats()
    recent_jobs = db.get_recent_jobs(30)
    db_channels = {c["id"]: c for c in db.get_all_channels()}

    # Live YouTube stats
    yt_stats = {}
    if fetch_youtube_stats:
        yt_stats = fetch_all_channels_stats(pipeline_config)

    # Enrich each channel
    channels_data = []
    for ch in pipeline_config.channels:
        char = CHARACTER_PROFILES.get(ch.character, {})
        db_info = db_channels.get(ch.id, {})
        yt_info = yt_stats.get(ch.id, {})

        channel_data = {
            "id": ch.id,
            "name": ch.name,
            "source_type": ch.source_type,
            "schedule": ch.schedule,
            "privacy": ch.upload_privacy,

            # Character
            "character": {
                "id": ch.character,
                "name": char.get("name", ch.character.title()),
                "emoji": char.get("emoji", "❓"),
                "personality": char.get("personality", ""),
                "voice": char.get("voice", ch.tts_voice),
                "color": char.get("color", "#888888"),
                "origin": char.get("origin", ""),
                "catchphrase": char.get("catchphrase", ""),
            },

            # YouTube live stats
            "youtube": {
                "exists": bool(yt_info and not yt_info.get("error")),
                "title": yt_info.get("title", ch.name),
                "handle": yt_info.get("handle", ""),
                "url": yt_info.get("url", ""),
                "handle_url": yt_info.get("handle_url", ""),
                "subscriber_count": yt_info.get("subscriber_count", 0),
                "view_count": yt_info.get("view_count", 0),
                "video_count": yt_info.get("video_count", 0),
                "thumbnail": yt_info.get("thumbnail", ""),
                "banner": yt_info.get("banner", ""),
                "description": yt_info.get("description", ""),
                "country": yt_info.get("country", ""),
                "latest_videos": yt_info.get("latest_videos", [])[:5],
                "error": yt_info.get("error"),
            },

            # Pipeline stats
            "pipeline": {
                "total_uploads": db_info.get("total_uploads", 0),
                "total_views": db_info.get("total_views", 0),
                "last_run_at": db_info.get("last_run_at", ""),
                "last_error": db_info.get("last_error", ""),
            },

            # Source URLs
            "source_urls": ch.source_urls,
            "tags": ch.tags,
        }
        channels_data.append(channel_data)

    status = {
        "generated_at": datetime.utcnow().isoformat(),
        "stats": pipeline_stats,
        "channels": channels_data,
        "recent_jobs": recent_jobs,
        "characters": CHARACTER_PROFILES,
        "revenue_targets": REVENUE_TARGETS,
        "links": {
            "livestream": livestream_url,
            "hf_space": hf_space_url,
            "github": "",
        },
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
        log.info("Status saved to %s", output_path)

    return status


def push_to_hf_space(status_path: Path, hf_repo: str = "Ananas4444/peanut-reacts-dashboard") -> bool:
    """Push status.json to the HF Space."""
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        api.upload_file(
            path_or_fileobj=str(status_path),
            path_in_repo="status.json",
            repo_id=hf_repo,
            repo_type="space",
        )
        log.info("Pushed status.json to %s", hf_repo)
        return True
    except Exception as e:
        log.error("HF push failed: %s", e)
        return False
