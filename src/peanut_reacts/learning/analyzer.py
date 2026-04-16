"""Pattern analyzer — find what correlates with performance.

Simple statistical analysis:
- Top performer signatures: what do top 10% videos have in common?
- Feature correlation: which numeric features predict views?
- Categorical analysis: which duration buckets / emotions / tag patterns win?
- Time analysis: which publish times get more views?

No heavy ML yet — just pandas + correlation. The LLM insight generator
turns these stats into natural language learnings.
"""
from __future__ import annotations

import json
import logging
import statistics
from collections import Counter, defaultdict
from typing import Optional

log = logging.getLogger(__name__)


def _parse_features(row: dict) -> dict:
    if not row.get("features_json"):
        return {}
    try:
        return json.loads(row["features_json"])
    except Exception:
        return {}


def analyze_top_performers(videos_with_metrics: list[dict], top_n: int = 5) -> dict:
    """Find common features among top N performing videos.

    Returns:
        {
            "top_videos": [...],
            "common_features": {...},
            "top_hype_words": [...],
            "avg_duration_minutes": ...,
            "dominant_emotion": ...,
        }
    """
    # Filter to videos with metrics
    with_metrics = [v for v in videos_with_metrics if v.get("view_count") and v.get("view_count") > 0]
    if not with_metrics:
        return {"error": "No videos with metrics yet"}

    # Sort by view count
    sorted_vids = sorted(with_metrics, key=lambda v: v.get("view_count", 0), reverse=True)
    top = sorted_vids[:top_n]
    bottom = sorted_vids[-top_n:] if len(sorted_vids) >= top_n * 2 else []

    top_features = [_parse_features(v) for v in top]
    bottom_features = [_parse_features(v) for v in bottom]

    # Aggregate numeric features
    def agg_numeric(records: list[dict], key: str) -> Optional[float]:
        values = [r.get(key, 0) for r in records if r.get(key) is not None]
        return statistics.mean(values) if values else None

    numeric_keys = [
        "title_length", "title_word_count", "title_uppercase_ratio",
        "title_hype_word_count", "title_exclamation_marks",
        "tag_count", "duration_seconds", "reaction_count",
        "reactions_per_minute", "avg_intensity",
    ]

    comparison = {}
    for key in numeric_keys:
        top_avg = agg_numeric(top_features, key)
        bot_avg = agg_numeric(bottom_features, key) if bottom_features else None
        if top_avg is not None:
            entry = {"top": round(top_avg, 2)}
            if bot_avg is not None and bot_avg != 0:
                entry["bottom"] = round(bot_avg, 2)
                entry["delta_pct"] = round((top_avg - bot_avg) / bot_avg * 100, 1)
            comparison[key] = entry

    # Boolean features
    bool_keys = ["title_has_hours", "title_has_emoji", "title_has_year",
                 "title_has_character_name", "has_sidemen_tag", "has_amongus_tag"]
    bool_comparison = {}
    for key in bool_keys:
        top_true = sum(1 for r in top_features if r.get(key))
        bot_true = sum(1 for r in bottom_features if r.get(key)) if bottom_features else 0
        bool_comparison[key] = {
            "top_ratio": top_true / len(top_features) if top_features else 0,
            "bottom_ratio": bot_true / len(bottom_features) if bottom_features else 0,
        }

    # Top hype words (extracted from titles)
    all_top_titles = " ".join(v.get("title", "") for v in top).lower()
    from .features import HYPE_WORDS
    hype_word_freq = {w: all_top_titles.count(w) for w in HYPE_WORDS}
    top_hype = sorted([w for w in hype_word_freq if hype_word_freq[w] > 0],
                      key=lambda w: -hype_word_freq[w])[:5]

    # Duration buckets
    duration_buckets = Counter(f.get("duration_bucket", "unknown") for f in top_features)

    # Dominant emotions (if reaction data available)
    all_emotions = Counter()
    for f in top_features:
        dist = f.get("emotion_distribution", {})
        if isinstance(dist, dict):
            for e, ratio in dist.items():
                all_emotions[e] += ratio
    top_emotion = all_emotions.most_common(3)

    return {
        "top_video_count": len(top),
        "top_videos": [
            {"video_id": v["video_id"], "title": v["title"][:60],
             "views": v.get("view_count", 0), "engagement": v.get("engagement_rate", 0)}
            for v in top
        ],
        "feature_comparison": comparison,
        "bool_comparison": bool_comparison,
        "top_hype_words": top_hype,
        "duration_buckets": dict(duration_buckets),
        "dominant_emotions": top_emotion,
        "avg_top_views": int(statistics.mean(v.get("view_count", 0) for v in top)),
        "median_top_views": int(statistics.median(v.get("view_count", 0) for v in top)),
    }


def analyze_publish_timing(videos_with_metrics: list[dict]) -> dict:
    """Find which publish times correlate with higher views."""
    by_day = defaultdict(list)
    by_hour = defaultdict(list)

    for v in videos_with_metrics:
        if not v.get("view_count"):
            continue
        features = _parse_features(v)
        dow = features.get("publish_day_of_week", -1)
        hour = features.get("publish_hour", -1)
        if dow >= 0:
            by_day[dow].append(v["view_count"])
        if hour >= 0:
            by_hour[hour].append(v["view_count"])

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_avg = {day_names[d]: statistics.mean(vs) for d, vs in by_day.items() if vs}
    hour_avg = {h: statistics.mean(vs) for h, vs in by_hour.items() if vs}

    best_day = max(day_avg.items(), key=lambda x: x[1])[0] if day_avg else None
    best_hour = max(hour_avg.items(), key=lambda x: x[1])[0] if hour_avg else None

    return {
        "day_averages": {k: int(v) for k, v in day_avg.items()},
        "hour_averages": {k: int(v) for k, v in hour_avg.items()},
        "best_day": best_day,
        "best_hour": best_hour,
    }


def compute_correlations(videos_with_metrics: list[dict]) -> dict:
    """Simple correlation between numeric features and views."""
    numeric_keys = [
        "title_length", "title_word_count", "title_uppercase_ratio",
        "title_hype_word_count", "tag_count", "duration_seconds",
        "reaction_count", "reactions_per_minute", "avg_intensity",
    ]

    correlations = {}
    for key in numeric_keys:
        pairs = []
        for v in videos_with_metrics:
            features = _parse_features(v)
            x = features.get(key)
            y = v.get("view_count")
            if x is not None and y is not None and y > 0:
                pairs.append((float(x), float(y)))

        if len(pairs) < 3:
            continue

        # Pearson correlation
        n = len(pairs)
        mean_x = sum(x for x, _ in pairs) / n
        mean_y = sum(y for _, y in pairs) / n
        num = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
        den_x = sum((x - mean_x) ** 2 for x, _ in pairs) ** 0.5
        den_y = sum((y - mean_y) ** 2 for _, y in pairs) ** 0.5
        if den_x == 0 or den_y == 0:
            continue
        r = num / (den_x * den_y)
        correlations[key] = round(r, 3)

    # Sort by absolute correlation
    sorted_corr = sorted(correlations.items(), key=lambda x: abs(x[1]), reverse=True)
    return dict(sorted_corr)


def full_analysis(videos_with_metrics: list[dict]) -> dict:
    """Run all analyses on the full dataset."""
    log.info("Running full analysis on %d videos", len(videos_with_metrics))

    return {
        "total_videos": len(videos_with_metrics),
        "total_views": sum(v.get("view_count", 0) or 0 for v in videos_with_metrics),
        "avg_views": statistics.mean([v.get("view_count", 0) or 0 for v in videos_with_metrics])
                     if videos_with_metrics else 0,
        "top_performers": analyze_top_performers(videos_with_metrics),
        "publish_timing": analyze_publish_timing(videos_with_metrics),
        "correlations": compute_correlations(videos_with_metrics),
    }
