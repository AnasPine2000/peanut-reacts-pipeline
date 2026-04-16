"""Recommender — convert insights into concrete parameters for the next video.

Takes the learned insights from the LLM and generates specific, applyable
recommendations like:
- "Set reactions_per_minute to 8 (top videos avg 7.8)"
- "Use 'HOURS' in title (3.2x higher views)"
- "Publish on Saturday at 14:00 UTC"
- "Target 5+ hour duration for next compilation"

These get stored as "recommendations" in the learning DB and are consumed
by the orchestrator before generating the next video.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

log = logging.getLogger(__name__)


RECOMMENDER_PROMPT = """You are a content strategist converting learnings into concrete actions.

Given the insights below about what has worked for this creator, generate 3-5 SPECIFIC recommendations for their NEXT video. Each recommendation should:
1. Target ONE adjustable parameter (title, duration, reactions_per_minute, publish_time, tags, emotion_focus, etc.)
2. Suggest a specific value or range (not general advice)
3. Reference which insight it's based on
4. Include priority: 1 (try soon) to 5 (critical)

Format as JSON array:
[
  {
    "category": "title | duration | reaction_density | emotion | timing | tags",
    "recommendation": "Specific action (e.g., 'Use title format: N HOURS of [creator] Reacts')",
    "rationale": "Why this should work based on the data",
    "priority": 1-5
  }
]"""


def generate_recommendations(
    insights: list[dict],
    llm_provider,
    channel_name: str = "",
) -> list[dict]:
    """Convert insights into concrete recommendations for the next video."""
    if not insights:
        return []

    insight_text = json.dumps(insights, indent=2)

    try:
        response = llm_provider.complete(
            [
                {"role": "system", "content": RECOMMENDER_PROMPT},
                {"role": "user", "content": f"Channel: {channel_name}\n\nInsights:\n{insight_text}"},
            ],
            temperature=0.4,
            max_tokens=1200,
        )

        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        recs = json.loads(text)
        log.info("Generated %d recommendations", len(recs))
        return recs
    except Exception as e:
        log.error("Recommendation generation failed: %s", e)
        return []


def apply_recommendations_to_channel(
    recommendations: list[dict],
    channel_config,
) -> dict:
    """Apply high-priority recommendations directly to channel config.

    Returns dict of parameters that WOULD change, but doesn't persist.
    Persistence should be done manually after review.
    """
    changes = {}
    for rec in recommendations:
        if rec.get("priority", 3) > 2:
            continue  # Only auto-apply priority 1-2

        category = rec.get("category", "")
        recommendation = rec.get("recommendation", "")

        # Simple text-based parameter extraction
        if category == "reaction_density":
            # Parse "set to N" or "target N"
            import re
            m = re.search(r"(\d+(?:\.\d+)?)", recommendation)
            if m:
                changes["reactions_per_minute"] = float(m.group(1))

        elif category == "duration":
            m = re.search(r"(\d+)\s*hours?", recommendation, re.IGNORECASE)
            if m:
                changes["compilation_hours"] = int(m.group(1))

    return changes
