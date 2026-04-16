"""Optimized title generator — uses learned patterns to craft titles.

Takes base video info + ChannelOverrides from applicator.py and generates
a title that applies all learned patterns (ALL CAPS, hour counts, year,
hype words, etc.).

Optionally uses the LLM for creative optimization within the constraints.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


def format_hours(duration_seconds: float) -> str:
    """Format duration as 'N HOURS' or 'N HOUR' for titles."""
    hours = round(duration_seconds / 3600)
    if hours < 1:
        minutes = round(duration_seconds / 60)
        return f"{minutes} MIN"
    return f"{hours} HOURS" if hours != 1 else f"1 HOUR"


def current_year() -> str:
    return str(datetime.utcnow().year)


def apply_rules_to_title(
    base_title: str,
    overrides,
    duration_seconds: float = 0,
    character_name: str = "",
    max_length: int = 100,
) -> str:
    """Apply override rules to a base title string (no LLM, deterministic)."""
    title = base_title.strip()

    # Include HOURS prefix if not already present
    if overrides.title_include_hours and duration_seconds > 60:
        hours_text = format_hours(duration_seconds)
        if not re.search(r"\b\d+\s*HOURS?\b", title, re.IGNORECASE):
            title = f"{hours_text} of {title}"

    # Include year if not present
    if overrides.title_include_year:
        yr = current_year()
        if yr not in title:
            title = f"{title} {yr}"

    # Add a hype word if none present
    if overrides.title_hype_words:
        title_lower = title.lower()
        if not any(w in title_lower for w in overrides.title_hype_words):
            hype = overrides.title_hype_words[0].upper()
            title = f"{hype} {title}"

    # Include character name for branding if missing
    if character_name and character_name.lower() not in title.lower():
        suffix = f" | {character_name.upper()} REACTS"
        if len(title) + len(suffix) <= max_length:
            title += suffix

    # Apply ALL CAPS if needed
    if overrides.title_uppercase:
        title = title.upper()

    # Enforce max length
    if len(title) > max_length:
        title = title[:max_length - 3].rstrip() + "..."

    # Normalize whitespace
    title = re.sub(r"\s+", " ", title).strip()
    return title


OPTIMIZE_PROMPT = """You are a YouTube title optimization expert.

Given a base video title and a set of learned rules that perform well for this creator, generate an OPTIMIZED title that:
1. Follows the rules strictly
2. Keeps the core subject/content clear
3. Is under 100 characters
4. Sounds natural, not spammy

Return ONLY the final title as plain text. No quotes, no explanation."""


def optimize_title_with_llm(
    base_title: str,
    overrides,
    duration_seconds: float,
    character_name: str,
    llm_provider=None,
) -> str:
    """Use LLM to craft an optimized title applying all learned rules."""
    if llm_provider is None:
        return apply_rules_to_title(base_title, overrides, duration_seconds, character_name)

    rules = []
    if overrides.title_uppercase:
        rules.append("- MUST be in ALL CAPS")
    if overrides.title_include_hours and duration_seconds > 60:
        rules.append(f"- MUST include '{format_hours(duration_seconds)}' explicitly")
    if overrides.title_include_year:
        rules.append(f"- MUST include the year {current_year()}")
    if overrides.title_hype_words:
        rules.append(f"- SHOULD include one of these hype words: {', '.join(overrides.title_hype_words)}")
    if character_name:
        rules.append(f"- SHOULD mention '{character_name.upper()} REACTS' for branding")
    rules.append("- Keep under 100 characters")

    prompt = f"""Base title: "{base_title}"

Rules to apply:
{chr(10).join(rules)}

Generate the optimized title:"""

    try:
        result = llm_provider.complete(
            [
                {"role": "system", "content": OPTIMIZE_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=100,
        )
        title = result.strip().strip('"').strip("'").strip()
        title = re.sub(r"\s+", " ", title)
        if len(title) > 100:
            title = title[:97].rstrip() + "..."

        # Safety: ensure hard constraints are met
        if overrides.title_uppercase and not title.isupper():
            title = title.upper()

        return title
    except Exception as e:
        log.warning("LLM title optimization failed: %s — falling back to rules", e)
        return apply_rules_to_title(base_title, overrides, duration_seconds, character_name)


def generate_title(
    base_subject: str,
    channel,
    overrides,
    duration_seconds: float = 0,
    use_llm: bool = True,
    llm_provider=None,
) -> str:
    """Main entry point: generate an optimized title from base subject."""
    character_name = channel.character.title() if channel.character else ""

    if use_llm and llm_provider is not None:
        return optimize_title_with_llm(
            base_subject, overrides, duration_seconds, character_name, llm_provider
        )
    return apply_rules_to_title(
        base_subject, overrides, duration_seconds, character_name
    )
