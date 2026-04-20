"""
ElevenLabs TTS engine with emotion-driven voice modulation.

Produces natural, expressive speech that matches the peanut's
personality and reacts with appropriate tone/emotion.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Emotion → ElevenLabs voice settings mapping
# stability: lower = more expressive, higher = more consistent
# similarity_boost: higher = closer to original voice
# style: 0-1, higher = more expressive (only v2 voices)
EMOTION_SETTINGS = {
    "excited": {"stability": 0.30, "similarity_boost": 0.75, "style": 0.8, "speed": 1.15},
    "shocked": {"stability": 0.25, "similarity_boost": 0.70, "style": 0.9, "speed": 1.10},
    "amused":  {"stability": 0.40, "similarity_boost": 0.75, "style": 0.7, "speed": 1.05},
    "sarcastic": {"stability": 0.55, "similarity_boost": 0.80, "style": 0.5, "speed": 0.92},
    "confused": {"stability": 0.45, "similarity_boost": 0.75, "style": 0.6, "speed": 0.95},
    "angry":   {"stability": 0.35, "similarity_boost": 0.70, "style": 0.8, "speed": 1.05},
    "neutral": {"stability": 0.50, "similarity_boost": 0.75, "style": 0.4, "speed": 1.0},
}


@dataclass(frozen=True)
class ElevenLabsConfig:
    """Configuration for ElevenLabs TTS."""
    api_key: str = ""
    voice_id: str = "pNInz6obpgDQGcFmaJgB"  # "Adam" — energetic male voice
    model_id: str = "eleven_multilingual_v2"   # best quality model
    output_format: str = "mp3_44100_128"


@dataclass
class ElevenLabsTTSResult:
    """Result from a single TTS synthesis."""
    audio_path: Path
    duration: float
    text: str
    emotion: str
    chars_used: int


class ElevenLabsTTSEngine:
    """ElevenLabs TTS with emotion-driven voice modulation."""

    def __init__(self, config: ElevenLabsConfig, log: Optional[logging.Logger] = None):
        self._config = config
        self._log = log or logger
        self._api_key = config.api_key or os.environ.get("ELEVENLABS_API_KEY", "")

        if not self._api_key:
            raise ValueError(
                "ElevenLabs API key required. Set ELEVENLABS_API_KEY env var "
                "or pass api_key in config."
            )

    def synthesize(
        self,
        text: str,
        output_path: Path,
        emotion: str = "neutral",
    ) -> ElevenLabsTTSResult:
        """Synthesize text with emotion-appropriate voice settings."""
        from elevenlabs import ElevenLabs

        client = ElevenLabs(api_key=self._api_key)
        settings = EMOTION_SETTINGS.get(emotion, EMOTION_SETTINGS["neutral"])

        output_path.parent.mkdir(parents=True, exist_ok=True)

        self._log.debug("ElevenLabs TTS [%s]: '%s'", emotion, text[:50])

        try:
            audio_generator = client.text_to_speech.convert(
                voice_id=self._config.voice_id,
                text=text,
                model_id=self._config.model_id,
                output_format=self._config.output_format,
                voice_settings={
                    "stability": settings["stability"],
                    "similarity_boost": settings["similarity_boost"],
                    "style": settings.get("style", 0.5),
                    "use_speaker_boost": True,
                },
            )

            # Write audio bytes
            audio_bytes = b"".join(audio_generator)
            output_path.write_bytes(audio_bytes)

            # Get duration via ffprobe
            import subprocess
            duration = float(subprocess.check_output([
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                str(output_path),
            ], stderr=subprocess.PIPE).decode().strip())

            # Apply speed adjustment if needed
            speed = settings.get("speed", 1.0)
            if abs(speed - 1.0) > 0.02:
                sped_path = output_path.with_name(output_path.stem + "_sped" + output_path.suffix)
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(output_path),
                    "-filter:a", f"atempo={speed}",
                    str(sped_path),
                ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                sped_path.replace(output_path)
                duration = duration / speed

            # ASCII-only log message — Windows cp1252 console can't encode
            # the unicode arrow "->" character, which crashes the logger.
            self._log.info(
                "ElevenLabs [%s] '%s' -> %.2fs (%d chars)",
                emotion, text[:40], duration, len(text),
            )

            return ElevenLabsTTSResult(
                audio_path=output_path,
                duration=duration,
                text=text,
                emotion=emotion,
                chars_used=len(text),
            )

        except Exception as e:
            self._log.error("ElevenLabs TTS failed: %s", e)
            raise

    def synthesize_lines(
        self,
        lines: list[dict],
        output_dir: Path,
    ) -> list[tuple[dict, ElevenLabsTTSResult]]:
        """Synthesize multiple reaction lines.

        Args:
            lines: List of dicts with 'text', 'start', 'emotion' keys.
            output_dir: Directory for audio files.

        Returns:
            List of (line_dict, result) tuples for successfully synthesized lines.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        results: list[tuple[dict, ElevenLabsTTSResult]] = []
        total_chars = 0

        for idx, line in enumerate(lines):
            text = line.get("text", "").strip()
            if not text:
                continue

            emotion = line.get("emotion", "neutral")
            audio_path = output_dir / f"reaction_{idx + 1:03d}.mp3"

            try:
                result = self.synthesize(text, audio_path, emotion=emotion)
                results.append((line, result))
                total_chars += result.chars_used
            except Exception as e:
                self._log.warning("TTS failed for line %d: %s", idx + 1, e)

        self._log.info(
            "ElevenLabs: synthesized %d/%d lines, %d chars total",
            len(results), len(lines), total_chars,
        )
        return results


def list_voices(api_key: str = "") -> list[dict]:
    """List available ElevenLabs voices."""
    from elevenlabs import ElevenLabs

    key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
    client = ElevenLabs(api_key=key)
    voices = client.voices.get_all()

    return [
        {
            "voice_id": v.voice_id,
            "name": v.name,
            "category": getattr(v, "category", "unknown"),
            "labels": getattr(v, "labels", {}),
        }
        for v in voices.voices
    ]
