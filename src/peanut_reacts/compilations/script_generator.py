"""
Compilation script generator using LLM.

Generates structured "Top 10" narration scripts for scary/creepy
compilation videos. Each script includes intro, numbered segments
with narration text and footage search queries, and outro.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from typing import Optional

from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider

logger = logging.getLogger(__name__)

CREEPY_TOPICS = [
    "Scariest security camera footage ever recorded",
    "Creepiest things caught on dashcam at night",
    "Most terrifying deep sea creatures caught on camera",
    "Abandoned places that will give you nightmares",
    "Unexplained events caught on camera",
    "Scariest nature moments ever filmed",
    "Creepiest things found in forests",
    "Most haunted locations on Earth",
    "Terrifying animal encounters caught on camera",
    "Mysterious sounds nobody can explain",
    "Scariest things found underwater",
    "Creepiest surveillance footage ever leaked",
    "Most disturbing things caught on trail camera",
    "Terrifying weather events caught on camera",
    "Creepy things found in abandoned buildings",
    "Scariest encounters in the wilderness",
    "Unexplained lights in the sky caught on camera",
    "Most chilling 911 calls recreated",
    "Terrifying cave exploration footage",
    "Scariest moments in storm chasing history",
]


@dataclass
class ScriptSegment:
    """One numbered segment in the compilation."""
    number: int              # countdown number (10, 9, 8... 1)
    title: str               # segment title shown on screen
    narration: str           # voiceover text (2-4 sentences)
    search_query: str        # query to find matching footage
    duration_estimate: float # estimated seconds for this segment
    intensity: str           # "low", "medium", "high" — builds toward #1


@dataclass
class CompilationScript:
    """Full compilation script for a "Top N" video."""
    topic: str
    video_title: str         # YouTube title
    intro_narration: str     # hook narration (first 15 seconds)
    segments: list[ScriptSegment]
    outro_narration: str     # CTA narration (last 10 seconds)
    description: str         # YouTube description
    tags: list[str]          # SEO tags
    total_duration: float = 0.0

    def __post_init__(self):
        self.total_duration = sum(s.duration_estimate for s in self.segments) + 25  # +intro+outro


_SYSTEM_PROMPT = """You are a YouTube script writer specializing in scary/creepy compilation videos.
You write scripts for "Top 10" countdown format videos that are 8-12 minutes long.

Your scripts must:
- Start with a gripping hook that makes viewers stay (first 10 seconds are critical)
- Build tension throughout — #10 is mildly creepy, #1 is the most terrifying
- Each segment has 2-3 sentences of narration (not too long)
- Include a search query for finding matching stock footage
- End with a strong call to action

Style: dramatic but not cheesy. Think MrBallen or Chills — serious tone, building dread.
Never use overly dramatic language like "you won't BELIEVE". Be genuinely creepy.

Return ONLY valid JSON. No markdown, no explanation."""


def generate_compilation_script(
    topic: str = "",
    count: int = 10,
    llm_config: Optional[LLMConfig] = None,
) -> CompilationScript:
    """Generate a compilation script using LLM.

    Args:
        topic: Video topic (random if empty)
        count: Number of items in the countdown
        llm_config: LLM configuration (defaults to DeepSeek)

    Returns:
        CompilationScript with all segments and metadata
    """
    if not topic:
        topic = random.choice(CREEPY_TOPICS)

    config = llm_config or LLMConfig(provider="deepseek")
    llm = create_llm_provider(config)

    prompt = f"""Generate a YouTube compilation script for: "{topic}"

Format: Top {count} countdown (#{count} to #1, building intensity)

Return JSON:
{{
  "video_title": "catchy YouTube title under 70 chars with emoji",
  "intro_narration": "gripping 2-sentence hook (15 seconds of speech)",
  "segments": [
    {{
      "number": {count},
      "title": "short segment title (5-8 words)",
      "narration": "2-3 sentence narration for this segment (20-30 seconds of speech)",
      "search_query": "specific query to find matching stock footage on Pexels/Pixabay",
      "duration_estimate": 45,
      "intensity": "low"
    }},
    ...repeat for all {count} segments, intensity goes low→medium→high...
  ],
  "outro_narration": "2-sentence outro with call to action (subscribe, like)",
  "description": "YouTube description with hashtags (under 300 chars)",
  "tags": ["tag1", "tag2", ...15-20 SEO tags]
}}

IMPORTANT:
- search_query should be generic enough for stock footage sites (no specific events/people)
- Each segment narration should be 20-30 seconds when read aloud
- Build tension: #{count} is mildly unsettling, #1 is genuinely terrifying
- Total video should be about 8-12 minutes
- Duration estimates: ~45-70 seconds per segment"""

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    logger.info("Generating script for: %s", topic)
    response = llm.complete(messages, temperature=0.85, max_tokens=4096)

    # Parse JSON
    text = response.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to fix common JSON issues
        text = text.replace("'", '"')
        data = json.loads(text)

    segments = []
    for s in data.get("segments", []):
        segments.append(ScriptSegment(
            number=s["number"],
            title=s["title"],
            narration=s["narration"],
            search_query=s["search_query"],
            duration_estimate=s.get("duration_estimate", 50),
            intensity=s.get("intensity", "medium"),
        ))

    # Sort by number descending (10, 9, 8... 1)
    segments.sort(key=lambda s: s.number, reverse=True)

    script = CompilationScript(
        topic=topic,
        video_title=data.get("video_title", f"Top {count} {topic} 😱"),
        intro_narration=data.get("intro_narration", f"What you're about to see will terrify you. These are the top {count} {topic}."),
        segments=segments,
        outro_narration=data.get("outro_narration", "If this video scared you, smash that like button and subscribe for more."),
        description=data.get("description", f"Top {count} {topic} #scary #creepy #shorts"),
        tags=data.get("tags", ["scary", "creepy", "top 10", "caught on camera"]),
    )

    logger.info("Script generated: '%s' (%d segments, ~%.0fs)",
                script.video_title, len(script.segments), script.total_duration)
    return script
