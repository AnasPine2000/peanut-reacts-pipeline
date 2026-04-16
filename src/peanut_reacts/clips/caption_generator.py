"""Caption + hook generator for Shorts and TikTok clips.

Generates platform-specific captions:
- YouTube Shorts: max 100 char title, #shorts tag
- TikTok: hook + description + hashtags, max 2200 chars, #fyp #foryou

Uses LLM for creative hooks when available, with deterministic fallbacks.
"""
from __future__ import annotations

import logging
import random
from typing import Optional

log = logging.getLogger(__name__)

# Pre-built hooks by emotion (no LLM needed)
EMOTION_HOOKS = {
    "shocked":      ["NO WAY", "WAIT WHAT", "I CAN'T BELIEVE THIS", "THIS IS INSANE"],
    "laughing":     ["I'M DYING", "LMAOOO", "THIS IS TOO FUNNY", "I CAN'T BREATHE"],
    "excited":      ["LET'S GOOO", "THIS IS HYPE", "ABSOLUTE SCENES", "UNREAL"],
    "celebrating":  ["WE DID IT", "INSANE WIN", "CLUTCH MOMENT", "VICTORY"],
    "confused":     ["WAIT WHAT", "HOW???", "MAKE IT MAKE SENSE", "I'M SO CONFUSED"],
    "scared":       ["OH NO", "RUN", "THIS IS TERRIFYING", "I'M SCARED"],
    "sarcastic":    ["OBVIOUSLY", "SHOCKING...", "WHO COULD'VE GUESSED", "CLASSIC"],
    "mind_blown":   ["MIND BLOWN", "NO SHOT", "ACTUALLY INSANE", "DID THAT JUST HAPPEN"],
    "amused":       ["LOL", "THE FACE HE MADE", "I CAN'T", "PEAK CONTENT"],
    "neutral":      ["WAIT FOR IT", "WATCH THIS", "YOU WON'T EXPECT THIS"],
}

BASE_HASHTAGS = {
    "sidemen": ["sidemen", "sidemenamongus", "ksi", "miniminter", "w2s"],
    "hasanabi": ["hasanabi", "hasan", "politics", "twitch", "clips"],
    "ksi": ["ksi", "sidemen", "reaction", "gaming"],
    "reddit": ["reddit", "stories", "aita", "storytime"],
    "general": ["reaction", "funny", "viral", "trending"],
}

PLATFORM_ALWAYS_TAGS = {
    "youtube_shorts": ["shorts"],
    "tiktok": ["fyp", "foryou", "viral"],
}


def generate_hook_text(
    peak_emotion: str = "",
    peak_score: float = 0,
    comment_count: int = 0,
    replay_intensity: float = 0,
) -> str:
    """Generate a 2-5 word hook overlay for the clip's first 2 seconds."""
    if comment_count >= 5:
        return f"{comment_count}+ FANS TIMESTAMPED THIS"
    if replay_intensity > 0.8:
        return "MOST REPLAYED MOMENT"
    emotion = peak_emotion.lower().strip()
    hooks = EMOTION_HOOKS.get(emotion, EMOTION_HOOKS["neutral"])
    return random.choice(hooks)


def generate_caption(
    platform: str,
    source_title: str,
    character_name: str = "Peanut",
    peak_emotion: str = "",
    hook_text: str = "",
    niche: str = "sidemen",
    source_video_id: str = "",
    custom_hashtags: list[str] = None,
    llm_provider=None,
) -> str:
    """Generate a platform-specific caption for a Short/TikTok clip.

    Returns formatted caption string ready to paste.
    """
    if llm_provider:
        return _llm_caption(
            platform, source_title, character_name,
            peak_emotion, niche, source_video_id, llm_provider,
        )
    return _rule_based_caption(
        platform, source_title, character_name,
        peak_emotion, hook_text, niche, source_video_id, custom_hashtags,
    )


def _rule_based_caption(
    platform: str,
    source_title: str,
    character_name: str,
    peak_emotion: str,
    hook_text: str,
    niche: str,
    source_video_id: str,
    custom_hashtags: list[str] = None,
) -> str:
    """Deterministic caption without LLM."""
    # Build hook
    if not hook_text:
        hook_text = generate_hook_text(peak_emotion=peak_emotion)

    # Build hashtag string
    tags = list(PLATFORM_ALWAYS_TAGS.get(platform, []))
    tags.extend(BASE_HASHTAGS.get(niche, BASE_HASHTAGS["general"]))
    if custom_hashtags:
        tags.extend(custom_hashtags)
    # Dedupe while preserving order
    seen = set()
    unique_tags = []
    for t in tags:
        t_lower = t.lower().strip("#")
        if t_lower not in seen:
            seen.add(t_lower)
            unique_tags.append(t_lower)
    tag_str = " ".join(f"#{t}" for t in unique_tags[:15])

    # Clean source title
    clean_title = source_title.replace("#", "").strip()[:60]

    if platform == "youtube_shorts":
        # YouTube: short title, max 100 chars
        title = f"{hook_text} | {character_name} Reacts to {clean_title}"
        if len(title) > 100:
            title = f"{hook_text} | {character_name} Reacts"
        return f"{title} #{' #'.join(unique_tags[:5])}"

    elif platform == "tiktok":
        # TikTok: hook line + description + CTA + hashtags
        lines = [
            hook_text,
            "",
            f"{character_name} reacts to {clean_title}",
            "",
        ]
        if source_video_id:
            lines.append(f"Full video on YouTube (link in bio)")
        lines.append("")
        lines.append(tag_str)
        return "\n".join(lines)

    else:
        return f"{hook_text} | {character_name} Reacts {tag_str}"


_LLM_PROMPT = """You are a viral social media copywriter. Write a caption for a {platform} clip.

Info:
- Character: {character} (animated reaction character)
- Source: "{source_title}"
- Emotion: {emotion}
- Platform: {platform}

Rules for {platform}:
{platform_rules}

Return ONLY the caption text. No quotes, no explanation."""

_PLATFORM_RULES = {
    "youtube_shorts": "- Title only, max 100 characters\n- Include #shorts\n- ALL CAPS hook at the start\n- Character name included",
    "tiktok": "- First line is a hook (max 8 words, attention-grabbing)\n- Then 1-2 sentences describing the clip\n- End with 'Full video on YouTube (link in bio)'\n- Add 5-8 relevant hashtags including #fyp #foryou",
}


def _llm_caption(
    platform: str,
    source_title: str,
    character_name: str,
    peak_emotion: str,
    niche: str,
    source_video_id: str,
    llm_provider,
) -> str:
    """LLM-generated caption."""
    try:
        prompt = _LLM_PROMPT.format(
            platform=platform,
            character=character_name,
            source_title=source_title[:80],
            emotion=peak_emotion or "excited",
            platform_rules=_PLATFORM_RULES.get(platform, "Be engaging and concise."),
        )
        response = llm_provider.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.7, max_tokens=200,
        )
        caption = response.strip().strip('"').strip("'")

        # Ensure platform tags are included
        platform_tags = PLATFORM_ALWAYS_TAGS.get(platform, [])
        for tag in platform_tags:
            if f"#{tag}" not in caption.lower():
                caption += f" #{tag}"

        return caption
    except Exception as e:
        log.warning("LLM caption failed: %s — using rule-based", e)
        return _rule_based_caption(
            platform, source_title, character_name,
            peak_emotion, "", niche, source_video_id, None,
        )
