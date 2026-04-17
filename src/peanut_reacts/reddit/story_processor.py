"""Clean raw Reddit post text into a TTS-ready script.

Reddit posts come with markdown, edits, URLs, trigger warnings, and are
often far too long for a 60-second Short. This module:

1. Strips markdown + URLs + Reddit-isms (EDIT:, UPDATE:, TW:, etc.)
2. Expands common abbreviations for clearer TTS ("AITA" → "am I the asshole",
   "BIL" → "brother-in-law") — optional, off by default
3. Generates a short hook line ("Today's AITA:" / "Petty revenge time:")
4. Truncates to a target length at a sentence boundary

Design: one-way transform — input is a `Story` from scraper.py, output is
plain text ready for TTS synthesis.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .db import Story

log = logging.getLogger(__name__)


# ── Content cleanup patterns ────────────────────────────────────────────

# Markdown-style formatting to strip
_MD_BOLD_ITALIC = re.compile(r"\*{1,3}([^*\n]+)\*{1,3}")
_MD_UNDERSCORE = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")
_MD_STRIKE = re.compile(r"~~([^~\n]+)~~")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_MD_HEADING = re.compile(r"^#+\s*", re.MULTILINE)
_MD_BLOCKQUOTE = re.compile(r"^>\s*", re.MULTILINE)
_MD_HR = re.compile(r"^[-=_*]{3,}$", re.MULTILINE)

# Bare URLs
_URL = re.compile(r"https?://\S+")

# Reddit-specific noise — edits, updates, TL;DRs, awards, trigger warnings
_REDDIT_NOISE = [
    re.compile(r"(?im)^edit\s*\d*\s*[:\-]?.*?(?=\n\n|\Z)", re.DOTALL),
    re.compile(r"(?im)^eta\s*[:\-]?.*?(?=\n\n|\Z)", re.DOTALL),
    re.compile(r"(?im)^tl;?dr\s*[:\-]?.*?(?=\n\n|\Z)", re.DOTALL),
    re.compile(r"(?im)^update\s*\d*\s*[:\-]?.*?(?=\n\n|\Z)", re.DOTALL),
    re.compile(r"(?im)^tw\s*[:\-]?.*?(?=\n\n|\Z)", re.DOTALL),
    re.compile(r"(?im)^trigger\s+warning\s*[:\-]?.*?(?=\n\n|\Z)", re.DOTALL),
    # Throwaway disclaimers
    re.compile(r"(?im)^throwaway.*?because.*?(?=\n\n|\Z)", re.DOTALL),
]

# Collapse multiple whitespace / newlines
_MULTI_WS = re.compile(r"[ \t]+")
_MULTI_NL = re.compile(r"\n{3,}")

# Reddit abbreviations → spoken form. Enabled by `expand_abbreviations=True`.
_ABBREVIATIONS = {
    r"\bAITA\b": "Am I the asshole",
    r"\bAITAH\b": "Am I the asshole",
    r"\bNTA\b": "not the asshole",
    r"\bYTA\b": "you're the asshole",
    r"\bESH\b": "everyone sucks here",
    r"\bNAH\b": "no assholes here",
    r"\bMIL\b": "mother in law",
    r"\bFIL\b": "father in law",
    r"\bSIL\b": "sister in law",
    r"\bBIL\b": "brother in law",
    r"\bSO\b": "significant other",
    r"\bDH\b": "dear husband",
    r"\bDW\b": "dear wife",
    r"\bDD\b": "daughter",
    r"\bDS\b": "son",
    r"\bOP\b": "the original poster",
    r"\bTIFU\b": "Today I messed up",
}

# Subreddit → hook intro. If missing, a generic opener is used.
_HOOKS = {
    "amitheasshole":       "Today's AITA: ",
    "pettyrevenge":        "Here's some petty revenge. ",
    "prorevenge":          "Pro revenge story incoming. ",
    "maliciouscompliance": "Malicious compliance time. ",
    "choosingbeggars":     "Choosing beggar alert! ",
    "entitledparents":     "Entitled parent story. ",
    "tifu":                "So, today I messed up. ",
    "bestofredditorupdates": "Reddit update — ",
}


@dataclass
class ProcessedStory:
    """TTS-ready script derived from a Reddit Story."""
    story_id: str
    subreddit: str
    hook: str             # short opener line (0.5-1.5s of TTS)
    body: str             # cleaned + possibly-truncated story body
    tts_text: str         # hook + body, ready for TTS
    original_chars: int   # pre-truncation length
    final_chars: int      # post-truncation length
    truncated: bool       # did we cut the tail off

    @property
    def estimated_duration(self) -> float:
        """Rough TTS seconds estimate (ElevenLabs @ default speed)."""
        return self.final_chars / 13.3


def clean_reddit_text(text: str) -> str:
    """Strip markdown, URLs, and Reddit-specific noise from post text."""
    t = text or ""

    # Strip Reddit noise blocks (EDIT:, TL;DR, TW:, etc.) first — they often
    # contain markdown too and we don't want it leaking into the final text.
    for pat in _REDDIT_NOISE:
        t = pat.sub("", t)

    # Strip markdown formatting (keep inner text)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_BOLD_ITALIC.sub(r"\1", t)
    t = _MD_UNDERSCORE.sub(r"\1", t)
    t = _MD_STRIKE.sub(r"\1", t)
    t = _MD_HEADING.sub("", t)
    t = _MD_BLOCKQUOTE.sub("", t)
    t = _MD_HR.sub("", t)

    # Strip bare URLs
    t = _URL.sub("", t)

    # Collapse whitespace
    t = _MULTI_WS.sub(" ", t)
    t = _MULTI_NL.sub("\n\n", t)

    # Trim
    return t.strip()


def expand_abbreviations(text: str) -> str:
    """Expand Reddit-specific abbreviations for clearer TTS playback."""
    for pat, repl in _ABBREVIATIONS.items():
        text = re.sub(pat, repl, text)
    return text


def _hook_for(subreddit: str) -> str:
    """Pick a hook intro based on subreddit, case-insensitive."""
    return _HOOKS.get(subreddit.lower(), "Here's a wild Reddit story. ")


def _truncate_at_sentence(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate at the last sentence boundary before max_chars.

    Returns (truncated_text, was_truncated_bool).
    """
    if len(text) <= max_chars:
        return text, False

    # Find last sentence-ending punctuation within the budget
    window = text[:max_chars]
    # Prefer . ! ? followed by whitespace; fall back to any of them
    for pattern in (r"[.!?]\s+(?=[A-Z\"'])", r"[.!?]\s+", r"[.!?]"):
        matches = list(re.finditer(pattern, window))
        if matches:
            end = matches[-1].end()
            return text[:end].rstrip(), True

    # Last resort: break at a word boundary
    idx = window.rfind(" ")
    if idx > max_chars // 2:
        return text[:idx].rstrip() + "...", True
    return window.rstrip() + "...", True


def process_story(
    story: Story,
    *,
    target_format: str = "shorts",
    expand_abbrev: bool = True,
    max_chars_shorts: int = 850,
    max_chars_longform: int = 6000,
) -> ProcessedStory:
    """Turn a scraped `Story` into a TTS-ready `ProcessedStory`.

    Args:
        story: raw Reddit story from scraper
        target_format: "shorts" (truncate to ~60s) or "longform" (keep full)
        expand_abbrev: expand AITA/MIL/etc. to spoken form — recommended ON
                       for Shorts (viewers can't re-read), OFF for long-form
                       where the peanut "reading" abbreviations sounds natural
        max_chars_shorts: target length for Shorts format
        max_chars_longform: soft cap for long-form (posts longer than this
                            probably warrant multi-part episodes)

    Returns:
        ProcessedStory ready to hand off to TTS.
    """
    cleaned_title = clean_reddit_text(story.title)
    cleaned_body = clean_reddit_text(story.body)

    if expand_abbrev:
        cleaned_title = expand_abbreviations(cleaned_title)
        cleaned_body = expand_abbreviations(cleaned_body)

    # Combine title + body. The title carries strong hook value — use it.
    if cleaned_body:
        full_body = f"{cleaned_title}. {cleaned_body}"
    else:
        full_body = cleaned_title

    original_chars = len(full_body)

    # Truncate depending on target format
    if target_format == "shorts":
        final_body, was_trunc = _truncate_at_sentence(full_body, max_chars_shorts)
    elif target_format == "longform":
        final_body, was_trunc = _truncate_at_sentence(full_body, max_chars_longform)
    else:
        raise ValueError(f"Unknown target_format: {target_format!r}")

    hook = _hook_for(story.subreddit)
    tts_text = f"{hook}{final_body}"

    return ProcessedStory(
        story_id=story.id,
        subreddit=story.subreddit,
        hook=hook,
        body=final_body,
        tts_text=tts_text,
        original_chars=original_chars,
        final_chars=len(tts_text),
        truncated=was_trunc,
    )
