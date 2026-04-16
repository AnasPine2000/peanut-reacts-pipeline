"""Applicator — reads learned recommendations and applies them to channel config.

Closes the feedback loop by translating the recommender's output into
concrete parameter overrides that the orchestrator uses when generating
the next video.

The applicator also records which recommendations were applied to which
video, so we can measure impact later (experiment tracker).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class ChannelOverrides:
    """Parameter overrides applied to a ChannelConfig before a run."""
    reactions_per_minute: Optional[float] = None
    compilation_hours: Optional[float] = None
    tts_rate: Optional[str] = None
    tags: Optional[list[str]] = None
    llm_personality_append: Optional[str] = None

    # Title-generation hints (consumed by title_generator.py)
    title_uppercase: bool = False
    title_include_hours: bool = False
    title_include_year: bool = False
    title_hype_words: list[str] = field(default_factory=list)
    title_max_tags: Optional[int] = None

    # Tracking
    applied_recommendation_ids: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# Parameter parsers: translate "set reactions_per_minute to 8" → 8
_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")
_HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)", re.IGNORECASE)
_SECONDS_RE = re.compile(r"(\d+[,.]?\d*)\s*seconds?", re.IGNORECASE)


def parse_recommendation(rec: dict) -> dict:
    """Parse a single recommendation into a dict of override kwargs.

    Accepts a recommendation row from the learning DB:
        {category, recommendation, rationale, priority, id}
    Returns a dict with the fields to set on ChannelOverrides.
    """
    category = (rec.get("category") or "").lower().replace("_", " ").strip()
    text = (rec.get("recommendation") or "") + " " + (rec.get("rationale") or "")
    text_lower = text.lower()
    override = {}
    notes = []

    # ── Reaction density ────────────────────────────────────────
    if "reaction" in category or "density" in category:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:per minute|/min|rpm)", text_lower)
        if not m:
            m = _NUMBER_RE.search(text)
        if m:
            try:
                override["reactions_per_minute"] = float(m.group(1))
                notes.append(f"reactions/min -> {m.group(1)}")
            except ValueError:
                pass

    # ── Duration / compilation length ───────────────────────────
    if "duration" in category or "length" in category:
        m = _HOURS_RE.search(text)
        if m:
            try:
                override["compilation_hours"] = float(m.group(1))
                notes.append(f"target hours -> {m.group(1)}")
            except ValueError:
                pass
        else:
            m = _SECONDS_RE.search(text)
            if m:
                try:
                    seconds = float(m.group(1).replace(",", ""))
                    override["compilation_hours"] = round(seconds / 3600, 1)
                    notes.append(f"target hours -> {override['compilation_hours']}")
                except ValueError:
                    pass

    # ── Title format ────────────────────────────────────────────
    if "title" in category:
        if "all caps" in text_lower or "uppercase" in text_lower or "caps" in text_lower:
            override["title_uppercase"] = True
            notes.append("title: ALL CAPS")

        if "hours" in text_lower or "hour count" in text_lower or re.search(r"\d+\s*hours?", text_lower):
            override["title_include_hours"] = True
            notes.append("title: include HOURS count")

        if re.search(r"year|202[4-9]", text_lower):
            override["title_include_year"] = True
            notes.append("title: include year")

        # Hype words
        hype_candidates = ["insane", "craziest", "best", "worst", "funniest", "most",
                           "ultimate", "mega", "epic", "greatest", "amazing", "shocking",
                           "wild", "unhinged", "cursed", "broken", "god", "chaos"]
        for w in hype_candidates:
            if w in text_lower:
                override.setdefault("title_hype_words", []).append(w)

        if "title_hype_words" in override:
            notes.append(f"title: include hype words {override['title_hype_words']}")

    # ── Tags ────────────────────────────────────────────────────
    if "tags" in category or "tag" in category:
        # "use 4 tags" or "exactly N tags"
        m = re.search(r"(?:use|exactly|max of?)\s*(\d+)\s*tags?", text_lower)
        if m:
            try:
                override["title_max_tags"] = int(m.group(1))
                notes.append(f"tags: max {m.group(1)}")
            except ValueError:
                pass

    # ── TTS rate / emotion ──────────────────────────────────────
    if "emotion" in category or "tts" in category:
        m = re.search(r"([+-]\d+%)", text)
        if m:
            override["tts_rate"] = m.group(1)
            notes.append(f"tts rate -> {m.group(1)}")

    # ── Personality (append to LLM system prompt) ───────────────
    if "personality" in category or "character" in category:
        short = rec.get("recommendation", "")[:150]
        if short:
            override["llm_personality_append"] = f"\n\nLearned style: {short}"
            notes.append("LLM personality update")

    override["notes"] = notes
    return override


def build_overrides(recommendations: list[dict]) -> ChannelOverrides:
    """Merge multiple recommendations into a single ChannelOverrides object.

    Priority: lower priority number wins conflicts.
    """
    # Sort by priority (1=highest, 5=lowest)
    sorted_recs = sorted(recommendations, key=lambda r: r.get("priority", 3))

    overrides = ChannelOverrides()
    for rec in sorted_recs:
        parsed = parse_recommendation(rec)
        applied_anything = False

        for key, value in parsed.items():
            if key == "notes":
                continue
            if value is None or value == [] or value == "":
                continue
            # Don't overwrite earlier (higher priority) settings
            current = getattr(overrides, key, None)
            if current in (None, False, [], "", 0):
                setattr(overrides, key, value)
                applied_anything = True
            elif isinstance(current, list) and isinstance(value, list):
                merged = list(set(current + value))
                setattr(overrides, key, merged)
                applied_anything = True

        if applied_anything and rec.get("id"):
            overrides.applied_recommendation_ids.append(rec["id"])
            overrides.notes.extend(parsed.get("notes", []))

    log.info("Built overrides from %d recommendations: %s",
             len(sorted_recs), overrides.notes)
    return overrides


def apply_overrides_to_channel(channel, overrides: ChannelOverrides):
    """Mutate a ChannelConfig in-place with overrides (only non-None fields)."""
    if overrides.reactions_per_minute is not None:
        channel.reactions_per_minute = overrides.reactions_per_minute
    if overrides.compilation_hours is not None:
        channel.compilation_hours = overrides.compilation_hours
    if overrides.tts_rate is not None:
        channel.tts_rate = overrides.tts_rate
    if overrides.title_max_tags is not None:
        channel.tags = channel.tags[:overrides.title_max_tags]
    if overrides.llm_personality_append:
        channel.llm_personality = (channel.llm_personality or "") + overrides.llm_personality_append
    return channel


def load_pending_recommendations(db_path: str, channel_id: str, max_recs: int = 10) -> list[dict]:
    """Load pending recommendations for a channel from learning.db."""
    from .database import LearningDB
    db = LearningDB(db_path)
    try:
        recs = db.get_pending_recommendations(channel_id)
        return recs[:max_recs]
    finally:
        db.close()


def mark_applied(db_path: str, rec_ids: list[int]):
    """Mark recommendations as applied after use."""
    from .database import LearningDB
    db = LearningDB(db_path)
    try:
        for rid in rec_ids:
            db.mark_applied(rid)
    finally:
        db.close()
