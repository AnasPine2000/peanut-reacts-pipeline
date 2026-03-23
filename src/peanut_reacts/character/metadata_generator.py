"""
LLM-powered YouTube metadata generator.

Generates SEO-optimized title, description, and tags for reaction videos
using the same LLM provider interface as reaction_generator.py.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from peanut_reacts.character.reaction_generator import ILLMProvider

logger = logging.getLogger(__name__)


@dataclass
class VideoMetadata:
    """YouTube video metadata."""
    title: str
    description: str
    tags: list[str] = field(default_factory=list)


_SYSTEM_PROMPT = """\
You are a YouTube SEO expert generating metadata for a reaction video.
The video features "Peanut" — an animated peanut character reacting to YouTube content.

Output JSON with exactly these keys:
{
    "title": "...",
    "description": "...",
    "tags": ["tag1", "tag2", ...]
}

Rules:
- title: Under 80 characters. Engaging, clickable. Include the original video topic.
  Format: "Peanut Reacts to [topic/title]" or a creative variation.
- description: 3-5 sentences. Start with a hook. Mention the original video.
  End with hashtags (5-8) and a call-to-action ("Subscribe for more reactions!").
  Do NOT include links or URLs.
- tags: 15-20 tags. Mix of:
  * Original video topic keywords
  * Reaction video keywords (reaction, reacts, commentary, funny)
  * Channel-related (peanut reacts, animated reaction)
  * Trending variations
"""


def generate_metadata(
    llm: ILLMProvider,
    *,
    original_title: str,
    transcript_summary: str = "",
    comment_highlights: Optional[list[str]] = None,
    reaction_lines: Optional[list[str]] = None,
) -> VideoMetadata:
    """Generate YouTube-optimized metadata via LLM.

    Args:
        llm: LLM provider instance
        original_title: Title of the original video being reacted to
        transcript_summary: Brief summary of the video content
        comment_highlights: Top viewer comments (for context)
        reaction_lines: The peanut's reaction dialogue (for context)

    Returns:
        VideoMetadata with title, description, and tags
    """
    # Build user message
    parts = [f'Original video title: "{original_title}"']

    if transcript_summary:
        parts.append(f"Brief content summary: {transcript_summary[:500]}")

    if comment_highlights:
        top = comment_highlights[:5]
        parts.append("Top viewer comments:\n" + "\n".join(f"- {c[:100]}" for c in top))

    if reaction_lines:
        top = reaction_lines[:5]
        parts.append("Peanut's reactions:\n" + "\n".join(f'- "{r}"' for r in top))

    user_message = "\n\n".join(parts)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    logger.info("Generating YouTube metadata via LLM ...")
    response = llm.complete(messages)

    return _parse_metadata(response, original_title)


def _parse_metadata(response: str, fallback_title: str) -> VideoMetadata:
    """Parse LLM JSON response into VideoMetadata."""
    # Strip markdown fences
    text = response.strip()
    if "```" in text:
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = text.replace("```", "").strip()

    # Handle trailing commas
    text = re.sub(r",\s*([}\]])", r"\1", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse metadata JSON, using fallback")
        return VideoMetadata(
            title=f"Peanut Reacts to {fallback_title}"[:80],
            description=f"Peanut reacts to {fallback_title}! Subscribe for more reactions!",
            tags=["peanut reacts", "reaction", "funny", "commentary"],
        )

    title = str(data.get("title", f"Peanut Reacts to {fallback_title}"))[:80]
    description = str(data.get("description", ""))[:5000]
    tags = data.get("tags", [])

    if isinstance(tags, list):
        tags = [str(t)[:30] for t in tags if t]
    else:
        tags = ["peanut reacts", "reaction"]

    return VideoMetadata(title=title, description=description, tags=tags)
