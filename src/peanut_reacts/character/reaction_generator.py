"""
LLM-based reaction script generation.

Feeds video transcript + comment highlights to an LLM and generates
timestamped peanut reaction dialogue. Provider-agnostic interface
with DeepSeek, Groq, and Ollama implementations.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from peanut_reacts.analysis.comments import CommentHighlight

logger = logging.getLogger(__name__)

_VALID_EMOTIONS = {"excited", "shocked", "amused", "confused", "sarcastic", "angry"}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMConfig:
    """Configuration for the LLM provider."""
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str = ""       # falls back to env var if empty
    base_url: str = ""      # provider-specific default if empty
    temperature: float = 0.8
    max_tokens: int = 4096


@dataclass
class ReactionLine:
    """A single peanut reaction at a specific video timestamp."""
    start: float
    end: float
    text: str
    emotion: str = "amused"


@dataclass
class ReactionScript:
    """Complete reaction script for a video."""
    lines: list[ReactionLine]
    video_title: str = ""
    generated_by: str = ""


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def script_to_dict(script: ReactionScript) -> dict:
    return {
        "video_title": script.video_title,
        "generated_by": script.generated_by,
        "lines": [
            {"start": l.start, "end": l.end, "text": l.text, "emotion": l.emotion}
            for l in script.lines
        ],
    }


def script_from_dict(data: dict) -> ReactionScript:
    lines = [
        ReactionLine(
            start=l["start"],
            end=l.get("end", l["start"] + _estimate_speech_duration(l["text"])),
            text=l["text"],
            emotion=l.get("emotion", "amused"),
        )
        for l in data.get("lines", [])
    ]
    return ReactionScript(
        lines=lines,
        video_title=data.get("video_title", ""),
        generated_by=data.get("generated_by", ""),
    )


def _estimate_speech_duration(text: str) -> float:
    """Estimate speech duration at ~150 words per minute."""
    words = len(text.split())
    return max(1.0, words / 2.5)  # ~2.5 words/sec


# ---------------------------------------------------------------------------
# LLM Provider interface + implementations
# ---------------------------------------------------------------------------

@runtime_checkable
class ILLMProvider(Protocol):
    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str: ...


class _OpenAICompatibleProvider:
    """Base for providers using OpenAI-compatible chat APIs (DeepSeek, Groq)."""

    def __init__(self, config: LLMConfig, default_base_url: str, env_key: str) -> None:
        import httpx

        self._model = config.model
        self._temperature = config.temperature
        self._max_tokens = config.max_tokens

        api_key = config.api_key or os.environ.get(env_key, "")
        if not api_key:
            raise ValueError(
                f"No API key provided. Set {env_key} env var or pass --api-key.",
            )

        base_url = (config.base_url or default_base_url).rstrip("/")
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=180.0,
        )

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        resp = self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


class DeepSeekProvider(_OpenAICompatibleProvider):
    def __init__(self, config: LLMConfig) -> None:
        cfg = LLMConfig(
            provider=config.provider,
            model=config.model or "deepseek-chat",
            api_key=config.api_key,
            base_url=config.base_url,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
        super().__init__(cfg, "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY")


class GroqProvider(_OpenAICompatibleProvider):
    def __init__(self, config: LLMConfig) -> None:
        cfg = LLMConfig(
            provider=config.provider,
            model=config.model or "llama-3.1-70b-versatile",
            api_key=config.api_key,
            base_url=config.base_url,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
        super().__init__(cfg, "https://api.groq.com/openai/v1", "GROQ_API_KEY")


class OllamaProvider:
    """Uses local Ollama HTTP API."""

    def __init__(self, config: LLMConfig) -> None:
        import httpx

        self._model = config.model or "llama3.1"
        self._temperature = config.temperature
        base_url = (config.base_url or "http://localhost:11434").rstrip("/")
        self._client = httpx.Client(base_url=base_url, timeout=120.0)

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self._temperature},
        }
        resp = self._client.post("/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()["message"]["content"]


def create_llm_provider(config: LLMConfig) -> ILLMProvider:
    """Factory: instantiate the right provider from config."""
    providers = {
        "deepseek": DeepSeekProvider,
        "groq": GroqProvider,
        "ollama": OllamaProvider,
    }
    cls = providers.get(config.provider.lower())
    if cls is None:
        raise ValueError(f"Unknown provider '{config.provider}'. Choose from: {list(providers)}")
    return cls(config)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are writing dialogue for "Peanut" — a peanut-headed character who reacts to YouTube videos like a real content creator.

## PEANUT'S PERSONALITY
- Energetic, witty, and opinionated — like a mix of KSI's energy and Penguinz0's dry humor
- Has CATCHPHRASES: "Bro what?!", "That's actually insane", "No shot!", "Hold on hold on hold on"
- Laughs genuinely at funny moments: write "(laughs)" or "Hahahaha" naturally
- Gets HYPED at intense moments: "OH MY GOD!", "LETS GOOO!", "WAIT WAIT WAIT"
- Uses sarcasm when someone does something dumb: "Yeah... that was smart", "Oh brilliant move genius"
- Shows genuine surprise: gasps, stutters ("I— what?!"), trailing off ("That's so...")
- References what VIEWERS said in comments when relevant
- Talks TO the viewer sometimes: "Chat, are you seeing this?!", "You guys called it!"

## VOICE DIRECTION (the text will be spoken aloud by TTS)
- Write how a REAL PERSON talks, not formal text. Use contractions, slang, filler words
- EXCITED: capitalize key words, use exclamation marks, short punchy sentences
- SHOCKED: stuttering, trailing off, "wait what", repeated words
- SARCASTIC: deadpan delivery, understated reactions, "sure buddy", "totally normal"
- AMUSED: include natural laughter cues like "haha", "bro", casual tone
- CONFUSED: questions, "hold on", "wait", rising intonation style text

## OUTPUT FORMAT
Output ONLY valid JSON: array of objects with keys:
  "start" (float, seconds), "text" (string, Peanut's spoken dialogue), "emotion" (excited|shocked|amused|confused|sarcastic|angry)

## RULES
- React every 12-20 seconds — NEVER leave gaps longer than 25 seconds
- Most reactions: 4-15 words. A few key moments: up to 25 words
- AT LEAST 3-4 reactions per minute of video
- Mix emotions — never use the same emotion 3 times in a row
- Reference what's ACTUALLY happening at that timestamp
- When comments mention a specific moment, react to it with the viewers' energy
- VARY intensity: some are loud outbursts, some are quiet observations, some are just "bruh"
- Do not react during the first 3 seconds
- Make it sound NATURAL — like someone actually watching and reacting live"""


def _compress_transcript(
    transcript: list[dict],
    highlights: list[CommentHighlight],
    max_chars: int = 10000,
) -> str:
    """Compress transcript to fit token limits.

    Includes full context (30s window) around highlights and every 3rd
    segment elsewhere. Higher max_chars allows more context for denser reactions.
    """
    highlight_times = {h.time for h in highlights}

    # Mark segments near highlights
    important_segments: list[dict] = []
    for seg in transcript:
        near_highlight = any(
            abs(seg["start"] - ht) < 30 or abs(seg["end"] - ht) < 30
            for ht in highlight_times
        )
        important_segments.append({**seg, "_important": near_highlight})

    lines: list[str] = []
    total_chars = 0
    for i, seg in enumerate(important_segments):
        # Include important segments + every 3rd segment for better coverage
        if seg["_important"] or i % 3 == 0:
            line = f"[{seg['start']:.1f}s] {seg['text'].strip()}"
            if total_chars + len(line) > max_chars:
                lines.append("... (transcript truncated)")
                break
            lines.append(line)
            total_chars += len(line)

    return "\n".join(lines)


def _build_user_message(
    transcript: list[dict],
    highlights: list[CommentHighlight],
    video_title: str,
    video_duration: float,
) -> str:
    """Build the user message with transcript and highlights context."""
    parts = [f'VIDEO: "{video_title}"', f"DURATION: {video_duration:.0f}s", ""]

    compressed = _compress_transcript(transcript, highlights)
    parts.append("TRANSCRIPT (key moments):")
    parts.append(compressed)
    parts.append("")

    if highlights:
        parts.append("VIEWER HIGHLIGHTS (moments the audience loved, ranked by engagement):")
        parts.append("USE THESE to fuel Peanut's reactions — reference what viewers said, agree/disagree with them, read comments aloud sometimes.")
        for i, h in enumerate(highlights, 1):
            samples = "; ".join(f'"{s[:80]}"' for s in h.sample_comments[:3])
            parts.append(
                f"{i}. [{h.time:.0f}s] {h.mention_count} comments, "
                f"{h.total_likes} likes — samples: {samples}",
            )
        parts.append("")

    parts.append("Generate Peanut's reactions to this video.")
    parts.append(
        "IMPORTANT: Reference specific comments when relevant — say things like "
        "'The comments are going crazy about this!' or 'Someone said [quote comment] "
        "and honestly? They're right.' Make Peanut feel like he's reading the "
        "comments AND watching the video simultaneously."
    )
    if not highlights:
        parts.append(
            "Focus on the most interesting transcript moments — look for "
            "emotional peaks, surprising statements, topic changes, or humor.",
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_llm_response(response: str, video_duration: float) -> list[ReactionLine]:
    """Parse JSON array of reactions from LLM response.

    Handles markdown code fences and common JSON quirks.
    """
    # Strip markdown fences
    text = response.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # Try parsing as JSON
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try fixing trailing commas
        fixed = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            data = json.loads(fixed)
        except json.JSONDecodeError:
            logger.error("Failed to parse LLM response as JSON")
            return []

    if not isinstance(data, list):
        data = data.get("reactions", data.get("lines", []))

    lines: list[ReactionLine] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        start = float(item.get("start", 0))
        text = str(item.get("text", "")).strip()
        emotion = str(item.get("emotion", "amused")).lower()

        if not text or start < 0:
            continue
        if start > video_duration > 0:
            continue
        if emotion not in _VALID_EMOTIONS:
            emotion = "amused"

        end = start + _estimate_speech_duration(text)
        lines.append(ReactionLine(start=start, end=end, text=text, emotion=emotion))

    # Sort and remove overlaps
    lines.sort(key=lambda l: l.start)
    filtered: list[ReactionLine] = []
    for line in lines:
        if filtered and line.start < filtered[-1].end + 2.0:
            continue
        filtered.append(line)

    return filtered


# ---------------------------------------------------------------------------
# Main generator class
# ---------------------------------------------------------------------------

class ReactionScriptGenerator:
    """Generate peanut reaction scripts using an LLM provider."""

    def __init__(self, llm: ILLMProvider, log: Optional[logging.Logger] = None) -> None:
        self._llm = llm
        self._log = log or logger

    def generate(
        self,
        transcript: list[dict],
        highlights: list[CommentHighlight] | None = None,
        *,
        video_title: str = "",
        video_duration: float = 0.0,
        max_reactions: int = 15,
    ) -> ReactionScript:
        """Generate a reaction script from transcript and comment highlights."""
        highlights = highlights or []

        user_message = _build_user_message(
            transcript, highlights, video_title, video_duration,
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        self._log.info("Generating reactions via LLM ...")
        response = self._llm.complete(messages)
        self._log.debug("LLM response: %s", response[:500])

        lines = _parse_llm_response(response, video_duration)

        if not lines:
            # Retry with a nudge
            self._log.warning("No valid reactions parsed. Retrying ...")
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": "That response was not valid JSON. Please output ONLY a JSON array of reaction objects.",
            })
            response2 = self._llm.complete(messages)
            lines = _parse_llm_response(response2, video_duration)

        lines = lines[:max_reactions]

        model_name = getattr(self._llm, "_model", "unknown")
        script = ReactionScript(
            lines=lines, video_title=video_title, generated_by=model_name,
        )

        self._log.info("Generated %d reaction line(s).", len(lines))
        return script
