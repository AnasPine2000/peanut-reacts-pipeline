"""
YouTube comment extraction and timestamp parsing.

Parses yt-dlp's info.json comment data, extracts timestamped references
from comment text, and clusters them into ranked highlights indicating
which moments the audience found most interesting.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Matches timestamps like "3:42", "03:42", "1:03:22"
_TIMESTAMP_RE = re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Comment:
    """A single YouTube comment with parsed timestamp references."""
    text: str
    author: str
    likes: int
    timestamp_refs: tuple[float, ...] = ()


@dataclass(frozen=True)
class CommentHighlight:
    """A moment in the video that multiple comments reference."""
    time: float
    mention_count: int
    total_likes: int
    sample_comments: tuple[str, ...]
    score: float


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def parse_timestamps(text: str) -> list[float]:
    """Extract all timestamps from *text* and return as seconds.

    Handles ``MM:SS`` and ``H:MM:SS`` formats.

    >>> parse_timestamps("check 3:42 and 1:05:30 lol")
    [222.0, 3930.0]
    """
    results: list[float] = []
    for match in _TIMESTAMP_RE.finditer(text):
        g1, g2, g3 = match.groups()
        if g3 is not None:
            # H:MM:SS
            seconds = int(g1) * 3600 + int(g2) * 60 + int(g3)
        else:
            # MM:SS
            seconds = int(g1) * 60 + int(g2)
        results.append(float(seconds))
    return results


# ---------------------------------------------------------------------------
# Comment loading
# ---------------------------------------------------------------------------

def load_comments(info_json_path: Path) -> list[Comment]:
    """Load comments from a yt-dlp ``.info.json`` file.

    Returns an empty list if the file has no comments.
    """
    try:
        data = json.loads(info_json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to read %s: %s", info_json_path, exc)
        return []

    raw_comments = data.get("comments") or []
    if not raw_comments:
        logger.warning("No comments found in %s", info_json_path.name)
        return []

    comments: list[Comment] = []
    for c in raw_comments:
        text = c.get("text", "")
        author = c.get("author", "")
        likes = int(c.get("like_count") or c.get("likes") or 0)
        timestamp_refs = tuple(parse_timestamps(text))
        comments.append(Comment(
            text=text, author=author, likes=likes, timestamp_refs=timestamp_refs,
        ))

    logger.info(
        "Loaded %d comments (%d with timestamps) from %s",
        len(comments),
        sum(1 for c in comments if c.timestamp_refs),
        info_json_path.name,
    )
    return comments


# ---------------------------------------------------------------------------
# Highlight computation
# ---------------------------------------------------------------------------

def compute_highlights(
    comments: list[Comment],
    *,
    cluster_window: float = 15.0,
    max_highlights: int = 10,
    max_samples: int = 5,
) -> list[CommentHighlight]:
    """Cluster timestamped comment references into ranked highlights.

    Comments referencing times within *cluster_window* seconds of each
    other are merged into a single highlight. Highlights are ranked by
    a score combining mention count and total likes.
    """
    # Collect all (timestamp, comment) pairs
    refs: list[tuple[float, Comment]] = []
    for c in comments:
        seen: set[float] = set()
        for ts in c.timestamp_refs:
            if ts not in seen:
                refs.append((ts, c))
                seen.add(ts)

    if not refs:
        logger.info("No timestamped references found in comments.")
        return []

    refs.sort(key=lambda r: r[0])

    # Cluster adjacent references
    clusters: list[list[tuple[float, Comment]]] = []
    current: list[tuple[float, Comment]] = [refs[0]]

    for ts, comment in refs[1:]:
        if ts - current[-1][0] <= cluster_window:
            current.append((ts, comment))
        else:
            clusters.append(current)
            current = [(ts, comment)]
    clusters.append(current)

    # Build highlights from clusters
    highlights: list[CommentHighlight] = []
    for cluster in clusters:
        times = [ts for ts, _ in cluster]
        unique_comments = {c.text: c for _, c in cluster}

        median_time = sorted(times)[len(times) // 2]
        mention_count = len(cluster)
        total_likes = sum(c.likes for c in unique_comments.values())
        score = mention_count * 2 + math.log(total_likes + 1)

        # Top samples by likes
        sorted_comments = sorted(unique_comments.values(), key=lambda c: c.likes, reverse=True)
        samples = tuple(c.text[:200] for c in sorted_comments[:max_samples])

        highlights.append(CommentHighlight(
            time=median_time,
            mention_count=mention_count,
            total_likes=total_likes,
            sample_comments=samples,
            score=score,
        ))

    # Rank by score, return top N
    highlights.sort(key=lambda h: h.score, reverse=True)
    highlights = highlights[:max_highlights]
    # Re-sort by time for chronological order
    highlights.sort(key=lambda h: h.time)

    logger.info("Computed %d highlight(s) from %d timestamped references.", len(highlights), len(refs))
    return highlights


# ---------------------------------------------------------------------------
# High-level convenience
# ---------------------------------------------------------------------------

def extract_comment_highlights(
    info_json_path: Path,
    *,
    cluster_window: float = 15.0,
    max_highlights: int = 10,
) -> list[CommentHighlight]:
    """Load comments from info.json and compute highlights in one call."""
    comments = load_comments(info_json_path)
    return compute_highlights(
        comments, cluster_window=cluster_window, max_highlights=max_highlights,
    )


def highlights_to_dicts(highlights: list[CommentHighlight]) -> list[dict]:
    """Serialize highlights to JSON-compatible dicts."""
    return [
        {
            "time": h.time,
            "mention_count": h.mention_count,
            "total_likes": h.total_likes,
            "sample_comments": list(h.sample_comments),
            "score": h.score,
        }
        for h in highlights
    ]


def highlights_from_dicts(data: list[dict]) -> list[CommentHighlight]:
    """Deserialize highlights from JSON-compatible dicts."""
    return [
        CommentHighlight(
            time=d["time"],
            mention_count=d["mention_count"],
            total_likes=d["total_likes"],
            sample_comments=tuple(d["sample_comments"]),
            score=d["score"],
        )
        for d in data
    ]
