"""
Dense reaction generator for live-style reaction videos.

Generates 200-300 reaction lines for a full video — one every 10-15 seconds.
The peanut comments on everything: gameplay, conversations, kills, meetings,
funny moments, accusations, and more. Feels like watching with a friend.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider
from peanut_reacts.core.srt import parse_srt

logger = logging.getLogger(__name__)


@dataclass
class DenseReaction:
    """A single reaction line with timing."""
    start: float      # seconds into video
    text: str          # what peanut says
    emotion: str       # for voice modulation
    intensity: float   # 0-1, how energetic


def generate_dense_reactions(
    transcript: list[dict],
    video_duration: float,
    llm_config: Optional[LLMConfig] = None,
    reactions_per_minute: float = 5.0,
    batch_size: int = 30,
) -> list[DenseReaction]:
    """Generate dense, continuous reactions throughout the video.

    Processes the transcript in chunks and generates natural,
    conversational reactions every 10-15 seconds.
    """
    config = llm_config or LLMConfig(provider="deepseek")
    llm = create_llm_provider(config)

    target_count = int(video_duration / 60 * reactions_per_minute)
    logger.info("Generating %d reactions for %.1f min video (%.1f/min)",
                target_count, video_duration / 60, reactions_per_minute)

    all_reactions: list[DenseReaction] = []

    # Process transcript in time chunks (2 minutes each)
    chunk_duration = 120.0  # seconds
    chunk_start = 0.0

    while chunk_start < video_duration:
        chunk_end = min(chunk_start + chunk_duration, video_duration)

        # Get transcript segments for this chunk
        chunk_segments = [
            s for s in transcript
            if chunk_start <= float(s.get("start", 0)) < chunk_end
        ]

        if not chunk_segments:
            chunk_start = chunk_end
            continue

        # Build context for LLM
        context = "\n".join(
            f"[{float(s['start']):.0f}s] {s.get('text', '').strip()}"
            for s in chunk_segments[:50]  # limit context
        )

        target_for_chunk = int((chunk_end - chunk_start) / 60 * reactions_per_minute)
        target_for_chunk = max(5, min(target_for_chunk, batch_size))

        prompt = (
            f"You are Peanut, watching Sidemen Among Us. Generate {target_for_chunk} "
            f"reactions for the video segment from {chunk_start:.0f}s to {chunk_end:.0f}s.\n\n"
            f"TRANSCRIPT:\n{context}\n\n"
            f"Generate a JSON array of reactions. Peanut is watching LIVE and reacting "
            f"naturally like a friend on the couch — NOT scripted commentary.\n\n"
            f"Reaction styles (mix these):\n"
            f"- Hype: 'OH! OH NO! HE DID NOT JUST—' 'LETS GOOO!'\n"
            f"- Funny: 'Bro what is he doing hahaha' 'That's actually hilarious'\n"
            f"- Shocked: 'WAIT WHAT?!' 'No way. No way. NO WAY.'\n"
            f"- Analysis: 'See, this is why Simon always wins' 'Classic KSI move'\n"
            f"- Suspense: 'He's about to die... watch... CALLED IT!'\n"
            f"- Casual: 'lol' 'bruhhh' 'okay okay okay'\n\n"
            f"Keep reactions SHORT (2-8 words mostly, max 15 words).\n"
            f"Space them {12}-{18} seconds apart.\n\n"
            f'JSON: [{{"start":{chunk_start+5},"text":"OH here we go!","emotion":"excited","intensity":0.7}}, ...]'
        )

        try:
            response = llm.complete(
                [{"role": "system", "content": "You generate natural, live reactions. Return ONLY a JSON array."},
                 {"role": "user", "content": prompt}],
                temperature=0.95,
                max_tokens=2000,
            )

            text = response.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            # Fix truncated JSON
            if not text.endswith("]"):
                last_brace = text.rfind("}")
                if last_brace > 0:
                    text = text[:last_brace + 1] + "]"

            data = json.loads(text)

            for r in data:
                start = float(r.get("start", chunk_start))
                if start < chunk_start or start >= chunk_end:
                    continue
                all_reactions.append(DenseReaction(
                    start=start,
                    text=r.get("text", ""),
                    emotion=r.get("emotion", "neutral"),
                    intensity=float(r.get("intensity", 0.5)),
                ))

            logger.info("  Chunk %.0f-%.0fs: %d reactions", chunk_start, chunk_end, len(data))

        except Exception as e:
            logger.warning("  Chunk %.0f-%.0fs failed: %s", chunk_start, chunk_end, e)

        chunk_start = chunk_end

    # Sort by time and deduplicate (remove reactions within 5s of each other)
    all_reactions.sort(key=lambda r: r.start)
    deduped: list[DenseReaction] = []
    for r in all_reactions:
        if not deduped or r.start - deduped[-1].start >= 8:
            deduped.append(r)

    logger.info("Generated %d dense reactions (%.1f/min)",
                len(deduped), len(deduped) / (video_duration / 60))
    return deduped


def load_transcript_for_video(video_path: Path) -> list[dict]:
    """Load transcript from SRT file matching the video."""
    srt_path = video_path.with_suffix(".en.srt")
    if srt_path.exists():
        return parse_srt(srt_path)

    # Try parent directory
    for srt in video_path.parent.glob("*.en.srt"):
        if video_path.stem.lower() in srt.stem.lower():
            return parse_srt(srt)

    return []
