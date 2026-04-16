"""LLM-based insight generator.

Takes the raw statistics from analyzer.py and uses DeepSeek to synthesize
natural-language learnings like:
- "Videos with 'HOURS' in the title get 3x more views"
- "Long-form (8+ hours) significantly outperforms standard format"
- "Hype words boost CTR by ~40%"

These insights get stored in the learning DB and shown on the dashboard.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

log = logging.getLogger(__name__)


INSIGHT_PROMPT = """You are a YouTube analytics expert analyzing a creator's video performance.

Based on the data below, generate 3-7 SPECIFIC actionable insights about what makes their videos successful. Each insight should:
1. Be grounded in the actual numbers provided
2. State the PATTERN observed (not general advice)
3. Include a confidence level (low/medium/high) based on sample size
4. Suggest ONE concrete action to take next

Format your response as a JSON array:
[
  {
    "category": "title | duration | reactions | timing | tags | emotion",
    "insight": "Specific observation about what works for this creator",
    "confidence": "high | medium | low",
    "action": "One specific thing to try in the next video",
    "evidence": "Brief reference to the data (e.g., 'top 5 videos avg 2.6K vs bottom 5 at 800')"
  }
]

Only insights you are confident about. If data is too sparse, return fewer insights. NEVER make up patterns."""


def generate_insights(
    analysis: dict,
    llm_provider,
    channel_name: str = "",
) -> list[dict]:
    """Use LLM to turn statistical analysis into actionable insights."""
    if not analysis or analysis.get("total_videos", 0) == 0:
        return []

    # Build a compact summary for the LLM
    summary = _format_analysis_for_llm(analysis, channel_name)

    try:
        response = llm_provider.complete(
            [
                {"role": "system", "content": INSIGHT_PROMPT},
                {"role": "user", "content": summary},
            ],
            temperature=0.3,
            max_tokens=1200,
        )

        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        insights = json.loads(text)
        log.info("Generated %d insights", len(insights))
        return insights
    except Exception as e:
        log.error("Insight generation failed: %s", e)
        return []


def _format_analysis_for_llm(analysis: dict, channel_name: str) -> str:
    """Format the analysis dict into a compact summary for the LLM."""
    lines = [f"Channel: {channel_name}"]
    lines.append(f"Total videos analyzed: {analysis.get('total_videos', 0)}")
    lines.append(f"Total views: {analysis.get('total_views', 0):,}")
    lines.append(f"Average views per video: {int(analysis.get('avg_views', 0)):,}")
    lines.append("")

    top = analysis.get("top_performers", {})
    if top and not top.get("error"):
        lines.append(f"=== TOP {top.get('top_video_count', 0)} PERFORMERS ===")
        lines.append(f"Avg views: {top.get('avg_top_views', 0):,}")
        lines.append(f"Median views: {top.get('median_top_views', 0):,}")

        if top.get("top_videos"):
            lines.append("Top video titles:")
            for v in top["top_videos"][:5]:
                lines.append(f"  - \"{v['title']}\" ({v['views']:,} views)")

        lines.append("")
        lines.append("TOP vs BOTTOM feature comparison:")
        for key, cmp in top.get("feature_comparison", {}).items():
            if "bottom" in cmp:
                delta = cmp.get("delta_pct", 0)
                arrow = "↑" if delta > 0 else "↓"
                lines.append(f"  {key}: top={cmp['top']}, bottom={cmp['bottom']} ({arrow}{abs(delta):.0f}%)")
            else:
                lines.append(f"  {key}: top={cmp['top']}")

        lines.append("")
        lines.append("Boolean features (top% vs bottom%):")
        for key, cmp in top.get("bool_comparison", {}).items():
            lines.append(f"  {key}: top={cmp['top_ratio']*100:.0f}%, bottom={cmp['bottom_ratio']*100:.0f}%")

        if top.get("top_hype_words"):
            lines.append(f"\nMost common hype words in top titles: {', '.join(top['top_hype_words'])}")

        if top.get("duration_buckets"):
            lines.append(f"Duration buckets (top videos): {dict(top['duration_buckets'])}")

    timing = analysis.get("publish_timing", {})
    if timing:
        lines.append("")
        lines.append("=== PUBLISH TIMING ===")
        if timing.get("best_day"):
            lines.append(f"Best day: {timing['best_day']}")
        if timing.get("best_hour") is not None:
            lines.append(f"Best hour: {timing['best_hour']}:00 UTC")
        if timing.get("day_averages"):
            lines.append(f"Day averages: {timing['day_averages']}")

    correlations = analysis.get("correlations", {})
    if correlations:
        lines.append("")
        lines.append("=== FEATURE CORRELATIONS WITH VIEWS ===")
        for key, r in list(correlations.items())[:8]:
            strength = "strong" if abs(r) > 0.6 else "moderate" if abs(r) > 0.3 else "weak"
            direction = "positive" if r > 0 else "negative"
            lines.append(f"  {key}: r={r} ({strength} {direction})")

    return "\n".join(lines)
