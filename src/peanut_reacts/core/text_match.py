"""
Text matching utilities for transcript search.

Consolidates fuzzy/exact keyword matching previously duplicated across 4 scripts.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def fuzzy_match(a: str, b: str, threshold: float = 0.3) -> bool:
    """Return True if the SequenceMatcher ratio of *a* and *b* >= *threshold*."""
    return SequenceMatcher(None, a, b).ratio() >= threshold


def fuzzy_contains(text: str, keyword: str, threshold: float = 0.6) -> bool:
    """Return True if any word in *text* fuzzily matches *keyword*."""
    keyword_lower = keyword.lower()
    for word in text.lower().split():
        if SequenceMatcher(None, word, keyword_lower).ratio() >= threshold:
            return True
    return False


def find_keyword_matches(
    transcript: list[dict],
    keyword: str,
    *,
    mode: str = "both",
    threshold: float = 0.6,
) -> list[dict]:
    """Search *transcript* segments for those matching *keyword*.

    *mode* can be:
    - ``"exact"`` — substring match only
    - ``"fuzzy"`` — fuzzy word-level match only
    - ``"both"``  — either exact or fuzzy
    """
    matches: list[dict] = []
    keyword_lower = keyword.lower()

    for seg in transcript:
        seg_text = seg.get("text", "")
        seg_lower = seg_text.lower()

        exact_hit = keyword_lower in seg_lower
        fuzzy_hit = fuzzy_contains(seg_text, keyword, threshold)

        if mode == "exact" and exact_hit:
            matches.append(seg)
        elif mode == "fuzzy" and fuzzy_hit:
            matches.append(seg)
        elif mode == "both" and (exact_hit or fuzzy_hit):
            matches.append(seg)

    logger.info(
        "Found %d match(es) for '%s' (%s search).", len(matches), keyword, mode,
    )
    return matches
