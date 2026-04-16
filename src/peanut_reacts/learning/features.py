"""Feature extraction — quantify every aspect of our videos.

For each uploaded video, extract features we can correlate with performance:
- Title features: length, has_hours, has_emoji, word count, sentiment
- Tag features: count, popular tags present, category match
- Duration features: minutes, bucket (short/medium/long/ultra)
- Reaction features: density, emotion distribution, intensity avg
- Source features: source channel, playlist, day of week, hour
- Thumbnail features: needs vision model, stored as path for now

Features become the input to the pattern analyzer.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# Feature extraction patterns
HOUR_PATTERNS = [
    r"(\d+)\s*HOURS?",
    r"(\d+)\s*HRS?",
    r"(\d+)H\b",
]

HYPE_WORDS = {"insane", "craziest", "best", "worst", "funniest", "most",
              "ultimate", "mega", "epic", "greatest", "amazing", "shocking",
              "wild", "unhinged", "cursed", "broken", "god", "chaos"}

CLICKBAIT_CHARS = set("!?.|:")


@dataclass
class VideoFeatures:
    # Identity
    video_id: str
    title: str = ""

    # Title features
    title_length: int = 0
    title_word_count: int = 0
    title_uppercase_ratio: float = 0
    title_has_hours: bool = False
    title_hours_count: int = 0
    title_has_emoji: bool = False
    title_hype_word_count: int = 0
    title_question_marks: int = 0
    title_exclamation_marks: int = 0
    title_has_year: bool = False
    title_has_character_name: bool = False

    # Tag features
    tag_count: int = 0
    has_sidemen_tag: bool = False
    has_amongus_tag: bool = False
    has_shorts_tag: bool = False

    # Duration features
    duration_seconds: float = 0
    duration_bucket: str = "unknown"  # short/standard/long/ultra

    # Reaction features (if we generated them)
    reaction_count: int = 0
    reactions_per_minute: float = 0
    emotion_distribution: dict = field(default_factory=dict)
    avg_intensity: float = 0
    dominant_emotion: str = ""

    # Publishing features
    publish_day_of_week: int = -1  # 0=Monday
    publish_hour: int = -1
    days_since_publish: int = 0

    # Source features (what we made this from)
    source_type: str = ""  # playlist | twitch_vod | reddit | lofi
    source_channel: str = ""  # uploader of source content

    # Character used
    character: str = ""

    # Compilation features
    is_compilation: bool = False
    episodes_combined: int = 1


def extract_title_features(title: str) -> dict:
    """Extract all title-based features."""
    if not title:
        return {}

    features = {
        "title": title,
        "title_length": len(title),
        "title_word_count": len(title.split()),
    }

    # Uppercase ratio
    letters = [c for c in title if c.isalpha()]
    features["title_uppercase_ratio"] = (
        sum(1 for c in letters if c.isupper()) / len(letters) if letters else 0
    )

    # Hours detection
    for pattern in HOUR_PATTERNS:
        m = re.search(pattern, title, re.IGNORECASE)
        if m:
            features["title_has_hours"] = True
            try:
                features["title_hours_count"] = int(m.group(1))
            except Exception:
                pass
            break
    else:
        features["title_has_hours"] = False
        features["title_hours_count"] = 0

    # Emoji detection (basic)
    features["title_has_emoji"] = any(ord(c) > 127 for c in title)

    # Hype words
    title_lower = title.lower()
    features["title_hype_word_count"] = sum(1 for w in HYPE_WORDS if w in title_lower)

    # Punctuation
    features["title_question_marks"] = title.count("?")
    features["title_exclamation_marks"] = title.count("!")

    # Year
    features["title_has_year"] = bool(re.search(r"\b(202[4-9])\b", title))

    # Character names
    char_names = ["peanut", "chilli", "marshmallow", "cashew", "pistachio", "walnut", "almond"]
    features["title_has_character_name"] = any(c in title_lower for c in char_names)

    return features


def extract_tag_features(tags: list[str]) -> dict:
    """Extract tag-based features."""
    tags_lower = [t.lower() for t in (tags or [])]
    return {
        "tag_count": len(tags),
        "has_sidemen_tag": any("sidemen" in t for t in tags_lower),
        "has_amongus_tag": any("among us" in t or "amongus" in t for t in tags_lower),
        "has_shorts_tag": any("short" in t or "#shorts" in t for t in tags_lower),
    }


def extract_duration_features(duration_seconds: float) -> dict:
    """Categorize duration."""
    if duration_seconds < 60:
        bucket = "short"
    elif duration_seconds < 20 * 60:
        bucket = "standard"
    elif duration_seconds < 2 * 3600:
        bucket = "long"
    else:
        bucket = "ultra"
    return {
        "duration_seconds": duration_seconds,
        "duration_bucket": bucket,
    }


def extract_reaction_features(reactions: list[dict], duration_seconds: float) -> dict:
    """Extract features from the reaction script (if available)."""
    if not reactions:
        return {
            "reaction_count": 0,
            "reactions_per_minute": 0,
            "emotion_distribution": {},
            "avg_intensity": 0,
            "dominant_emotion": "",
        }

    emotions = Counter(r.get("emotion", "neutral").lower() for r in reactions)
    total = sum(emotions.values())
    dist = {e: c / total for e, c in emotions.items()}
    intensities = [float(r.get("intensity", 1.0)) for r in reactions]

    return {
        "reaction_count": len(reactions),
        "reactions_per_minute": (len(reactions) / (duration_seconds / 60)) if duration_seconds > 0 else 0,
        "emotion_distribution": dist,
        "avg_intensity": sum(intensities) / len(intensities) if intensities else 0,
        "dominant_emotion": emotions.most_common(1)[0][0] if emotions else "",
    }


def extract_publish_features(published_at: str) -> dict:
    """Extract features from publish timestamp."""
    if not published_at:
        return {"publish_day_of_week": -1, "publish_hour": -1, "days_since_publish": 0}

    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        now = datetime.utcnow().replace(tzinfo=dt.tzinfo)
        return {
            "publish_day_of_week": dt.weekday(),
            "publish_hour": dt.hour,
            "days_since_publish": (now - dt).days,
        }
    except Exception:
        return {"publish_day_of_week": -1, "publish_hour": -1, "days_since_publish": 0}


def extract_all_features(
    video_data: dict,
    reactions: Optional[list[dict]] = None,
    source_info: Optional[dict] = None,
) -> VideoFeatures:
    """Combine all feature extractors into a VideoFeatures object."""
    from .analytics import parse_iso_duration

    duration = parse_iso_duration(video_data.get("duration", ""))

    features_dict = {
        "video_id": video_data.get("video_id", ""),
    }
    features_dict.update(extract_title_features(video_data.get("title", "")))
    features_dict.update(extract_tag_features(video_data.get("tags", [])))
    features_dict.update(extract_duration_features(duration))
    features_dict.update(extract_reaction_features(reactions or [], duration))
    features_dict.update(extract_publish_features(video_data.get("published_at", "")))

    if source_info:
        features_dict["source_type"] = source_info.get("source_type", "")
        features_dict["source_channel"] = source_info.get("source_channel", "")
        features_dict["character"] = source_info.get("character", "")
        features_dict["is_compilation"] = source_info.get("is_compilation", False)
        features_dict["episodes_combined"] = source_info.get("episodes_combined", 1)

    # Filter to known fields
    known = {f.name for f in VideoFeatures.__dataclass_fields__.values()}
    filtered = {k: v for k, v in features_dict.items() if k in known}
    return VideoFeatures(**filtered)
