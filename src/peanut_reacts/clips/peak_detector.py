"""Peak moment detection — find the most viral moments in a video.

Combines 3 signals:
1. YouTube most-replayed heatmap (strongest: actual viewer behavior)
2. Comment timestamp clustering (viewers tell us where the good parts are)
3. Reaction density peaks (from our own pipeline output)

Returns ranked list of peak moments for clip extraction.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class PeakMoment:
    start_seconds: float
    end_seconds: float
    score: float  # Combined 0-1 score
    replay_intensity: float = 0
    comment_count: int = 0
    comment_likes: int = 0
    reaction_density: int = 0
    peak_emotion: str = ""
    sample_comments: list[str] = field(default_factory=list)
    source_video_id: str = ""


# ── Method 1: YouTube Heatmap ──────────────────────────────────────

def get_youtube_heatmap(video_id: str) -> list[dict]:
    """Fetch most-replayed heatmap from YouTube via yt-dlp.

    Returns: [{start_time: float, end_time: float, value: float}, ...]
    """
    try:
        import yt_dlp
        ydl = yt_dlp.YoutubeDL({"quiet": True, "skip_download": True})
        info = ydl.extract_info(f"https://youtube.com/watch?v={video_id}", download=False)
        heatmap = info.get("heatmap", [])
        if heatmap:
            log.info("Heatmap: %d entries for %s", len(heatmap), video_id)
        else:
            log.info("No heatmap available for %s (too new or not popular)", video_id)
        return heatmap or []
    except Exception as e:
        log.warning("Failed to fetch heatmap for %s: %s", video_id, e)
        return []


def heatmap_peaks(heatmap: list[dict], top_n: int = 20) -> list[PeakMoment]:
    """Convert heatmap entries to peak moments, sorted by intensity."""
    peaks = []
    for entry in heatmap:
        start = float(entry.get("start_time", 0))
        end = float(entry.get("end_time", start + 10))
        value = float(entry.get("value", 0))
        peaks.append(PeakMoment(
            start_seconds=start,
            end_seconds=end,
            score=value,
            replay_intensity=value,
        ))
    peaks.sort(key=lambda p: p.score, reverse=True)
    return peaks[:top_n]


# ── Method 2: Comment Timestamp Clustering ─────────────────────────

TIMESTAMP_RE = re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b")


def extract_comment_timestamps(
    comments: list[dict],
    bucket_seconds: int = 10,
) -> dict[int, dict]:
    """Find timestamps in comment text and cluster into time buckets.

    Returns: {bucket_start_seconds: {count, likes, samples}}
    """
    timestamps = defaultdict(lambda: {"count": 0, "likes": 0, "samples": []})

    for comment in comments:
        text = comment.get("text") or comment.get("originalComment", "")
        if not text:
            continue

        for match in TIMESTAMP_RE.finditer(text):
            parts = [int(x) for x in match.groups() if x]
            # Interpret: if 3 parts = H:M:S, if 2 parts = M:S
            if len(parts) == 3:
                seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
            elif len(parts) == 2:
                seconds = parts[0] * 60 + parts[1]
            else:
                continue

            if seconds < 0 or seconds > 24 * 3600:
                continue

            bucket = (seconds // bucket_seconds) * bucket_seconds
            timestamps[bucket]["count"] += 1
            timestamps[bucket]["likes"] += int(comment.get("like_count", 0) or comment.get("like", 0) or 0)
            if len(timestamps[bucket]["samples"]) < 3:
                sample = text.strip()[:150]
                if sample not in timestamps[bucket]["samples"]:
                    timestamps[bucket]["samples"].append(sample)

    return dict(timestamps)


def comment_peaks(
    comments: list[dict],
    min_count: int = 2,
    window_seconds: int = 30,
) -> list[PeakMoment]:
    """Find peak moments from comment timestamps."""
    buckets = extract_comment_timestamps(comments)
    if not buckets:
        return []

    peaks = []
    for bucket_start, data in buckets.items():
        if data["count"] < min_count:
            continue
        # Normalize score: more comments + more likes = higher
        score = min(1.0, (data["count"] / 10.0) + (data["likes"] / 500.0))
        peaks.append(PeakMoment(
            start_seconds=max(0, bucket_start - 5),
            end_seconds=bucket_start + window_seconds,
            score=score,
            comment_count=data["count"],
            comment_likes=data["likes"],
            sample_comments=data["samples"],
        ))

    peaks.sort(key=lambda p: p.score, reverse=True)
    return peaks


# ── Method 3: Reaction Density from Our Pipeline ──────────────────

def reaction_peaks(
    reactions: list[dict],
    window_seconds: int = 15,
    min_density: int = 3,
) -> list[PeakMoment]:
    """Find clusters of high-intensity reactions in our own pipeline output.

    reactions format: [{start: float, text: str, emotion: str, intensity: float}, ...]
    """
    if not reactions:
        return []

    peaks = []
    HYPE_EMOTIONS = {"shocked", "laughing", "mind_blown", "screaming", "amazed", "excited"}

    # Sliding window over reactions
    for i, r in enumerate(reactions):
        start = float(r.get("start", 0))
        window_end = start + window_seconds
        window_reactions = [
            x for x in reactions[i:]
            if float(x.get("start", 0)) < window_end
        ]

        if len(window_reactions) < min_density:
            continue

        # Check if multiple hype emotions
        hype_count = sum(1 for x in window_reactions if x.get("emotion", "").lower() in HYPE_EMOTIONS)
        avg_intensity = sum(float(x.get("intensity", 1.0)) for x in window_reactions) / len(window_reactions)

        if hype_count == 0 and avg_intensity < 1.5:
            continue

        # Score: density + intensity + hype ratio
        score = min(1.0, (len(window_reactions) / 8.0) * 0.5 +
                   (avg_intensity / 3.0) * 0.3 +
                   (hype_count / len(window_reactions)) * 0.2)

        peaks.append(PeakMoment(
            start_seconds=max(0, start - 3),
            end_seconds=window_end,
            score=score,
            reaction_density=len(window_reactions),
            peak_emotion=window_reactions[0].get("emotion", ""),
        ))

    # Dedupe overlapping peaks
    peaks.sort(key=lambda p: p.score, reverse=True)
    filtered = []
    for p in peaks:
        if not any(abs(p.start_seconds - f.start_seconds) < window_seconds for f in filtered):
            filtered.append(p)

    return filtered


# ── Combined Scoring ───────────────────────────────────────────────

def combine_peaks(
    heatmap_list: list[PeakMoment],
    comment_list: list[PeakMoment],
    reaction_list: list[PeakMoment],
    min_clip_seconds: int = 15,
    max_clip_seconds: int = 60,
    min_spacing_seconds: int = 60,
    weights: dict = None,
) -> list[PeakMoment]:
    """Merge peaks from all sources with weighted scoring."""
    weights = weights or {"heatmap": 0.5, "comments": 0.3, "reactions": 0.2}

    # Bucket all peaks by 10-second time slots
    buckets = defaultdict(lambda: PeakMoment(start_seconds=0, end_seconds=0, score=0))

    def add_to_bucket(peak: PeakMoment, weight: float):
        bucket_key = int(peak.start_seconds // 10) * 10
        b = buckets[bucket_key]
        if b.start_seconds == 0:
            b.start_seconds = peak.start_seconds
            b.end_seconds = peak.end_seconds
        b.score += peak.score * weight
        b.replay_intensity = max(b.replay_intensity, peak.replay_intensity)
        b.comment_count = max(b.comment_count, peak.comment_count)
        b.comment_likes = max(b.comment_likes, peak.comment_likes)
        b.reaction_density = max(b.reaction_density, peak.reaction_density)
        if peak.peak_emotion and not b.peak_emotion:
            b.peak_emotion = peak.peak_emotion
        if peak.sample_comments:
            b.sample_comments = (b.sample_comments + peak.sample_comments)[:3]

    for p in heatmap_list:
        add_to_bucket(p, weights["heatmap"])
    for p in comment_list:
        add_to_bucket(p, weights["comments"])
    for p in reaction_list:
        add_to_bucket(p, weights["reactions"])

    # Clamp clip duration
    merged = []
    for b in buckets.values():
        duration = b.end_seconds - b.start_seconds
        if duration < min_clip_seconds:
            b.end_seconds = b.start_seconds + min_clip_seconds
        elif duration > max_clip_seconds:
            b.end_seconds = b.start_seconds + max_clip_seconds
        merged.append(b)

    # Sort by score and enforce min spacing
    merged.sort(key=lambda p: p.score, reverse=True)
    final = []
    for p in merged:
        if p.score < 0.1:
            continue
        if any(abs(p.start_seconds - f.start_seconds) < min_spacing_seconds for f in final):
            continue
        final.append(p)

    log.info("Combined %d heatmap + %d comment + %d reaction peaks -> %d final",
             len(heatmap_list), len(comment_list), len(reaction_list), len(final))
    return final


# ── Full Detection Pipeline ────────────────────────────────────────

def detect_peaks(
    video_id: str,
    comments: Optional[list[dict]] = None,
    reactions: Optional[list[dict]] = None,
    top_n: int = 15,
    min_clip_seconds: int = 15,
    max_clip_seconds: int = 60,
) -> list[PeakMoment]:
    """Run full peak detection using all available signals.

    Args:
        video_id: YouTube video ID for heatmap lookup
        comments: Optional list of comments for timestamp clustering
        reactions: Optional list of our pipeline's reaction output
        top_n: Max number of peaks to return
    """
    # Method 1: Heatmap
    heatmap_data = get_youtube_heatmap(video_id)
    hm_peaks = heatmap_peaks(heatmap_data, top_n=top_n * 2)

    # Method 2: Comments
    cm_peaks = comment_peaks(comments or [])

    # Method 3: Reactions
    rx_peaks = reaction_peaks(reactions or [])

    # Combine
    final = combine_peaks(
        hm_peaks, cm_peaks, rx_peaks,
        min_clip_seconds=min_clip_seconds,
        max_clip_seconds=max_clip_seconds,
    )

    for p in final:
        p.source_video_id = video_id

    return final[:top_n]
