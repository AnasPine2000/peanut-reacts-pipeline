"""Comment sentiment analyzer — parse viewer feedback at scale.

Uses DeepSeek to batch-classify comments and extract:
- Overall sentiment distribution (positive / neutral / negative)
- Recurring themes (what do viewers ask for / complain about)
- Sample positive and negative comments
- Common requests for next videos
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


SENTIMENT_PROMPT = """You are a comment analyst. Classify each comment into: POSITIVE, NEUTRAL, or NEGATIVE.

Also identify:
- Main themes (what viewers are talking about)
- Requests (what they want more of)
- Complaints (what they want less of)

Return JSON:
{
  "classifications": [{"id": 0, "sentiment": "POSITIVE|NEUTRAL|NEGATIVE"}, ...],
  "themes": ["theme 1", "theme 2"],
  "requests": ["request 1", "request 2"],
  "complaints": ["complaint 1", "complaint 2"]
}"""


def analyze_comments(
    comments: list[dict],
    llm_provider,
    batch_size: int = 20,
) -> dict:
    """Analyze a batch of comments for sentiment and themes."""
    if not comments:
        return {
            "total": 0, "positive": 0, "neutral": 0, "negative": 0,
            "themes": [], "requests": [], "complaints": [],
            "sample_positive": [], "sample_negative": [],
        }

    # Take top comments by like count
    sorted_comments = sorted(comments, key=lambda c: c.get("like_count", 0), reverse=True)
    top_comments = sorted_comments[:batch_size]

    # Format for LLM
    numbered = "\n".join(
        f"{i}. {c.get('text', '')[:200]}" for i, c in enumerate(top_comments)
    )

    try:
        response = llm_provider.complete(
            [
                {"role": "system", "content": SENTIMENT_PROMPT},
                {"role": "user", "content": f"Comments:\n{numbered}"},
            ],
            temperature=0.2,
            max_tokens=1500,
        )

        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        result = json.loads(text)

        # Count sentiments
        classifications = result.get("classifications", [])
        counts = {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0}
        positive_samples = []
        negative_samples = []

        for cls in classifications:
            sentiment = cls.get("sentiment", "NEUTRAL").upper()
            if sentiment in counts:
                counts[sentiment] += 1
            idx = cls.get("id", -1)
            if 0 <= idx < len(top_comments):
                comment_text = top_comments[idx].get("text", "")[:200]
                if sentiment == "POSITIVE" and len(positive_samples) < 3:
                    positive_samples.append(comment_text)
                elif sentiment == "NEGATIVE" and len(negative_samples) < 3:
                    negative_samples.append(comment_text)

        return {
            "total": len(classifications),
            "positive": counts["POSITIVE"],
            "neutral": counts["NEUTRAL"],
            "negative": counts["NEGATIVE"],
            "themes": result.get("themes", []),
            "requests": result.get("requests", []),
            "complaints": result.get("complaints", []),
            "sample_positive": positive_samples,
            "sample_negative": negative_samples,
        }
    except Exception as e:
        log.error("Comment sentiment analysis failed: %s", e)
        return {
            "total": len(comments), "positive": 0, "neutral": 0, "negative": 0,
            "themes": [], "requests": [], "complaints": [],
            "sample_positive": [], "sample_negative": [],
            "error": str(e),
        }
