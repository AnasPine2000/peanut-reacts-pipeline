"""
LLM-powered analysis script writer for Sidemen Among Us Decoded.

Takes REAL transcript data and generates insight-driven video essay scripts.
Not generic commentary — the LLM analyzes actual data patterns.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider

logger = logging.getLogger(__name__)


@dataclass
class ScriptSection:
    """One section of the video essay."""
    section_type: str     # "intro", "analysis", "clip_moment", "data_reveal", "outro"
    narration: str        # voiceover text
    visual_note: str      # what should be on screen (clip ref, data card, etc.)
    duration_estimate: float  # seconds
    clip_query: str = ""  # search query or video title for footage
    clip_timestamp: float = 0.0  # timestamp in source video
    emotion: str = "neutral"  # for TTS emotion


@dataclass
class AnalysisScript:
    """Complete video essay script."""
    title: str
    description: str
    tags: list[str]
    sections: list[ScriptSection]
    total_duration: float = 0.0

    def __post_init__(self):
        self.total_duration = sum(s.duration_estimate for s in self.sections)


_SYSTEM_PROMPT = """You are a YouTube video essay writer for the channel "Sidemen AmongUs Decoded".
Your style is analytical but entertaining — like a sports commentator breaking down plays.
Think MrBallen meets Film Theory, but for Sidemen Among Us gameplay.

You analyze REAL DATA from transcripts and turn it into compelling narratives.
You find surprising patterns, explain psychology behind decisions, and build tension.

Rules:
- Every claim must be backed by the data provided
- Use specific quotes from transcripts (cite the video name)
- Build a narrative arc: hook → evidence → surprising insight → conclusion
- Be genuinely analytical, not clickbaity
- Reference specific episodes and moments
- Speak directly to Sidemen fans who already know the players
- Use timestamps when referencing specific moments

Return ONLY valid JSON."""


def write_player_analysis(
    player: str,
    report: dict,
    llm_config: Optional[LLMConfig] = None,
) -> AnalysisScript:
    """Generate a video essay script analyzing one player.

    Takes the real data report from data_analyzer.generate_player_report()
    and creates a compelling narrative script.
    """
    config = llm_config or LLMConfig(provider="deepseek")
    llm = create_llm_provider(config)

    # Prepare data summary for the LLM
    data_summary = (
        f"PLAYER: {player}\n"
        f"Total mentions across transcripts: {report.get('total_mentions', 0)}\n"
        f"Appeared in {report.get('videos_appeared_in', 0)} videos\n"
        f"Times accused by other players: {report.get('times_accused_by_others', 0)}\n"
        f"Roles played: {json.dumps(report.get('roles_played', {}))}\n"
        f"Most common words in their segments: {report.get('most_common_words', [])[:15]}\n\n"
        f"KEY MOMENTS (with video titles and timestamps):\n"
    )

    for m in report.get("key_moments", [])[:15]:
        mins = int(m["timestamp"] // 60)
        secs = int(m["timestamp"] % 60)
        data_summary += f"  [{mins}:{secs:02d}] ({m['video'][:50]}) \"{m['text'][:100]}\"\n"

    data_summary += "\nSPEECH SAMPLES:\n"
    for s in report.get("speech_samples", [])[:20]:
        data_summary += f"  [{s['type']}] ({s['video'][:40]}) \"{s['text'][:100]}\"\n"

    prompt = f"""Write a 10-15 minute video essay script analyzing {player} in Sidemen Among Us.

REAL DATA:
{data_summary}

Generate a JSON script with this structure:
{{
  "title": "YouTube title under 70 chars with emoji (e.g. 'Why {player} ALWAYS Gets Caught as Imposter 🔍')",
  "description": "YouTube description under 300 chars with hashtags",
  "tags": ["tag1", "tag2", ...20 tags],
  "sections": [
    {{
      "section_type": "intro",
      "narration": "Gripping 3-4 sentence hook that poses the central question (30 seconds)",
      "visual_note": "Description of what viewers see (dark background, stats appearing)",
      "duration_estimate": 30,
      "emotion": "neutral"
    }},
    {{
      "section_type": "data_reveal",
      "narration": "Present a surprising statistic from the data (20 seconds)",
      "visual_note": "Animated stat card showing the number",
      "duration_estimate": 20,
      "emotion": "shocked"
    }},
    {{
      "section_type": "clip_moment",
      "narration": "Narrate over a specific clip from the transcripts (30 seconds)",
      "visual_note": "Clip from [specific video name]",
      "clip_query": "the video title to pull clip from",
      "clip_timestamp": 123.4,
      "duration_estimate": 30,
      "emotion": "excited"
    }},
    {{
      "section_type": "analysis",
      "narration": "Deep analysis of the pattern (45-60 seconds)",
      "visual_note": "Data overlays, comparison graphics",
      "duration_estimate": 50,
      "emotion": "neutral"
    }},
    ... more sections to fill 10-15 minutes ...
    {{
      "section_type": "outro",
      "narration": "Concluding insight + CTA (20 seconds)",
      "visual_note": "Subscribe animation",
      "duration_estimate": 20,
      "emotion": "excited"
    }}
  ]
}}

IMPORTANT:
- Total duration should be 600-900 seconds (10-15 minutes)
- Include 8-12 sections
- Mix section types: intro, data_reveal, clip_moment, analysis, outro
- Every narration must reference REAL data from above
- Use actual quotes from the speech samples
- Reference specific video titles from the key moments
- Build a narrative arc: question → evidence → pattern → surprising conclusion"""

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    logger.info("Generating analysis script for %s...", player)
    response = llm.complete(messages, temperature=0.85, max_tokens=4096)

    # Parse JSON
    text = response.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    data = json.loads(text)

    sections = []
    for s in data.get("sections", []):
        sections.append(ScriptSection(
            section_type=s.get("section_type", "analysis"),
            narration=s.get("narration", ""),
            visual_note=s.get("visual_note", ""),
            duration_estimate=s.get("duration_estimate", 40),
            clip_query=s.get("clip_query", ""),
            clip_timestamp=s.get("clip_timestamp", 0),
            emotion=s.get("emotion", "neutral"),
        ))

    script = AnalysisScript(
        title=data.get("title", f"{player} in Sidemen Among Us — DECODED 🔍"),
        description=data.get("description", f"Deep analysis of {player} in Sidemen Among Us"),
        tags=data.get("tags", ["sidemen", "among us", player.lower(), "analysis"]),
        sections=sections,
    )

    logger.info(
        "Script: '%s' — %d sections, ~%.0f seconds (%.1f min)",
        script.title, len(script.sections),
        script.total_duration, script.total_duration / 60,
    )
    return script


def write_episode_breakdown(
    video_title: str,
    transcript: list[dict],
    key_moments: list[dict],
    llm_config: Optional[LLMConfig] = None,
) -> AnalysisScript:
    """Generate a script breaking down one specific episode."""
    config = llm_config or LLMConfig(provider="deepseek")
    llm = create_llm_provider(config)

    # Summarize transcript for LLM context
    transcript_summary = ""
    for seg in transcript[:100]:  # first 100 segments
        mins = int(float(seg.get("start", 0)) // 60)
        secs = int(float(seg.get("start", 0)) % 60)
        transcript_summary += f"[{mins}:{secs:02d}] {seg.get('text', '').strip()}\n"

    moments_text = ""
    for m in key_moments[:10]:
        mins = int(m["timestamp"] // 60)
        secs = int(m["timestamp"] % 60)
        moments_text += f"[{mins}:{secs:02d}] \"{m['text'][:80]}\" (score={m['score']})\n"

    prompt = f"""Write a 10-15 minute video essay script breaking down this Sidemen Among Us episode:

VIDEO: "{video_title}"

TRANSCRIPT EXCERPT (first portion):
{transcript_summary[:3000]}

KEY MOMENTS:
{moments_text}

Generate a JSON script analyzing this episode play-by-play. Structure:
{{
  "title": "catchy title under 70 chars",
  "description": "YouTube description under 300 chars",
  "tags": [...20 tags],
  "sections": [
    {{"section_type": "intro|analysis|clip_moment|data_reveal|outro", "narration": "...", "visual_note": "...", "duration_estimate": 40, "clip_timestamp": 0, "emotion": "neutral"}}
  ]
}}

Include 8-12 sections totaling 600-900 seconds. Reference specific timestamps and quotes."""

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    logger.info("Generating episode breakdown for: %s", video_title)
    response = llm.complete(messages, temperature=0.85, max_tokens=4096)

    text = response.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    data = json.loads(text)

    sections = [
        ScriptSection(
            section_type=s.get("section_type", "analysis"),
            narration=s.get("narration", ""),
            visual_note=s.get("visual_note", ""),
            duration_estimate=s.get("duration_estimate", 40),
            clip_query=video_title,
            clip_timestamp=s.get("clip_timestamp", 0),
            emotion=s.get("emotion", "neutral"),
        )
        for s in data.get("sections", [])
    ]

    return AnalysisScript(
        title=data.get("title", f"{video_title} — DECODED"),
        description=data.get("description", ""),
        tags=data.get("tags", []),
        sections=sections,
    )
