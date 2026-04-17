"""Reddit story scraper — fetch top posts from chosen subreddits.

Uses Reddit's public JSON API with no auth (read-only, rate-limited to
~60 requests/min for unauthenticated clients). For higher volume or if
Reddit tightens the free path, swap in PRAW with a script-type app.

Usage:
    from peanut_reacts.reddit.scraper import scrape_subreddits, DEFAULT_CONFIG
    from peanut_reacts.reddit.db import StoryDB

    db = StoryDB()
    n_new = scrape_subreddits(DEFAULT_CONFIG, db)
    print(f"Scraped {n_new} new stories")
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from .db import Story, StoryDB

log = logging.getLogger(__name__)


# Default starter pack — high-viral subreddits for the TikTok story format.
DEFAULT_SUBREDDITS = [
    "AmItheAsshole",
    "MaliciousCompliance",
    "pettyrevenge",
    "ChoosingBeggars",
    "entitledparents",
    "tifu",
]

# TTS rate-of-speech estimate. ElevenLabs at default speed ~= 155 words/min,
# ~= 800 chars/min, ~= 13.3 chars/sec. Used to filter for target video length.
_CHARS_PER_SEC = 13.3

# Basic content filter — rejects posts containing any of these case-insensitive
# substrings. Not exhaustive; YouTube/TikTok policies are stricter than these.
_BLOCKED_SUBSTRINGS = [
    # Self-harm
    "suicide", "kill myself", "kill him", "kill her",
    # Sexual
    "rape", "raped", "molest", "sexual assault",
    # Slurs — keep this list short; add as needed. Not exhaustive.
    "nigger", "faggot", "retard",
]


@dataclass
class ScraperConfig:
    """Controls what gets scraped and kept.

    The scraper is format-agnostic — it stores the full post text. Length
    normalization (truncate for TikTok, keep for YouTube long-form) is
    handled by `story_processor.py` at render time. This lets one scrape
    feed multiple output formats without re-fetching.

    Reference post lengths in the wild:
        pettyrevenge / ChoosingBeggars:  500-1500 chars
        AITA / MaliciousCompliance:      1500-4000 chars
        tifu / bestofredditorupdates:    2000-8000 chars
    """
    subreddits: list[str] = field(default_factory=lambda: DEFAULT_SUBREDDITS.copy())
    sort: str = "top"                 # "top" | "hot" | "new" | "rising"
    time_filter: str = "week"         # "hour" | "day" | "week" | "month" | "year" | "all"
    per_subreddit_limit: int = 25     # Reddit caps at 100
    min_score: int = 500
    min_chars: int = 300              # too-short posts don't fill even 30s
    max_chars: int = 8000             # above this, split into multi-part is better than one long video
    skip_nsfw: bool = True
    skip_stickied: bool = True
    skip_locked: bool = True
    user_agent: str = "peanut-reacts-bot/0.1 (+https://github.com/anas/peanut-reacts)"
    request_delay: float = 2.0        # seconds between subreddit fetches
    max_retries: int = 3


def scrape_subreddits(config: ScraperConfig, db: StoryDB) -> int:
    """Scrape configured subreddits, insert new stories into the DB.

    Returns the count of newly-inserted (non-duplicate, non-rejected) stories.
    """
    n_new = 0
    n_seen = 0
    n_rejected = 0
    n_dup = 0

    for sub in config.subreddits:
        log.info("Scraping r/%s (%s, %s)...", sub, config.sort, config.time_filter)
        try:
            raw_posts = _fetch_subreddit(sub, config)
        except Exception as e:
            log.error("Failed to fetch r/%s: %s", sub, e)
            continue

        for raw in raw_posts:
            n_seen += 1

            # Pre-filter on raw Reddit flags (skip_nsfw etc. are config-driven)
            if config.skip_nsfw and raw.get("over_18"):
                log.debug("Rejected %s: NSFW", raw.get("id"))
                n_rejected += 1
                continue
            if config.skip_stickied and raw.get("stickied"):
                n_rejected += 1
                continue
            if config.skip_locked and raw.get("locked"):
                n_rejected += 1
                continue

            story = _post_to_story(raw, sub)
            if story is None:
                n_rejected += 1
                continue

            # Apply length + content filters
            reason = _filter_reason(story, config)
            if reason:
                log.debug("Rejected %s: %s", story.id, reason)
                n_rejected += 1
                continue

            if db.insert_new(story):
                n_new += 1
                log.info("  + %s [%s] score=%d chars=%d — %s",
                         story.id, sub, story.score, story.char_count,
                         story.title[:60])
            else:
                n_dup += 1

        time.sleep(config.request_delay)  # be polite

    log.info("Scrape complete: %d new, %d duplicates, %d rejected, %d total seen",
             n_new, n_dup, n_rejected, n_seen)
    return n_new


def _fetch_subreddit(subreddit: str, config: ScraperConfig) -> list[dict]:
    """Fetch raw post dicts from a subreddit's JSON endpoint."""
    url = (
        f"https://www.reddit.com/r/{subreddit}/{config.sort}.json"
        f"?t={config.time_filter}&limit={config.per_subreddit_limit}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": config.user_agent})

    last_err: Optional[Exception] = None
    for attempt in range(config.max_retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [c["data"] for c in data["data"]["children"] if c.get("kind") == "t3"]
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                # Rate limited — exponential backoff with jitter
                wait = (2 ** attempt) * 5 + random.uniform(0, 3)
                log.warning("r/%s rate-limited (429), waiting %.1fs ...", subreddit, wait)
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"Giving up on r/{subreddit}: {last_err}")


def _post_to_story(raw: dict, subreddit: str) -> Optional[Story]:
    """Convert a Reddit JSON post dict to a Story. Returns None if unusable."""
    post_id = raw.get("id")
    if not post_id:
        return None

    # Skip non-text posts (images, videos, link-only)
    if raw.get("is_video") or raw.get("post_hint") in ("image", "rich:video", "hosted:video"):
        return None
    if raw.get("url_overridden_by_dest") and not raw.get("is_self"):
        return None

    title = (raw.get("title") or "").strip()
    body = (raw.get("selftext") or "").strip()
    if not title:
        return None

    # Reddit marks removed/deleted content with these placeholders
    if body in ("[removed]", "[deleted]", "[removed by reddit]"):
        body = ""

    # For subreddits that put the whole story in the title (pettyrevenge etc.)
    # we'll accept body-only, title-only, or both — but one must be non-empty.
    full = f"{title}. {body}".strip()
    if len(full) < 50:
        return None

    return Story(
        id=post_id,
        subreddit=subreddit,
        title=title,
        body=body,
        score=int(raw.get("score", 0)),
        num_comments=int(raw.get("num_comments", 0)),
        author=raw.get("author") or None,
        url=f"https://reddit.com{raw.get('permalink', '')}",
        created_utc=int(raw.get("created_utc", 0)),
        char_count=len(full),
        tts_seconds_est=len(full) / _CHARS_PER_SEC,
    )


def _filter_reason(story: Story, config: ScraperConfig) -> Optional[str]:
    """Return a rejection reason, or None if the story passes."""
    if story.score < config.min_score:
        return f"score {story.score} < {config.min_score}"
    if story.char_count < config.min_chars:
        return f"too short ({story.char_count} < {config.min_chars})"
    if story.char_count > config.max_chars:
        return f"too long ({story.char_count} > {config.max_chars})"

    # Content filter (case-insensitive substring match)
    lower = story.full_text.lower()
    for bad in _BLOCKED_SUBSTRINGS:
        if bad in lower:
            return f"blocked term: {bad!r}"

    return None
