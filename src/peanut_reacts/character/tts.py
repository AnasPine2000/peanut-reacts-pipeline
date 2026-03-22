"""
Edge TTS wrapper with word-level timing metadata.

Produces audio files and timing data for speech-synced peanut animation.
Uses Microsoft Edge TTS (free, high quality, async Python library).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TTSWordTiming:
    """Timing for a single word within a TTS audio clip."""
    offset_ms: int
    duration_ms: int
    text: str


@dataclass(frozen=True)
class TTSResult:
    """Result of a single TTS synthesis."""
    audio_path: Path
    duration: float              # seconds
    word_timings: tuple[TTSWordTiming, ...]


@dataclass(frozen=True)
class TTSConfig:
    """Configuration for Edge TTS synthesis."""
    voice: str = "en-US-GuyNeural"
    rate: str = "+10%"
    pitch: str = "+0Hz"
    volume: str = "+0%"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def tts_result_to_dict(result: TTSResult, line_index: int) -> dict:
    """Serialize a TTSResult for JSON manifest."""
    return {
        "line_index": line_index,
        "audio_path": str(result.audio_path),
        "duration": result.duration,
        "word_timings": [
            {"offset_ms": w.offset_ms, "duration_ms": w.duration_ms, "text": w.text}
            for w in result.word_timings
        ],
    }


def tts_result_from_dict(data: dict) -> TTSResult:
    """Deserialize a TTSResult from JSON manifest."""
    return TTSResult(
        audio_path=Path(data["audio_path"]),
        duration=data["duration"],
        word_timings=tuple(
            TTSWordTiming(offset_ms=w["offset_ms"], duration_ms=w["duration_ms"], text=w["text"])
            for w in data["word_timings"]
        ),
    )


# ---------------------------------------------------------------------------
# Edge TTS engine
# ---------------------------------------------------------------------------

class EdgeTTSEngine:
    """Async Edge TTS wrapper that produces audio + timing metadata."""

    def __init__(self, config: TTSConfig, log: Optional[logging.Logger] = None) -> None:
        self._config = config
        self._log = log or logger

    async def synthesize(self, text: str, output_path: Path) -> TTSResult:
        """Generate speech audio and return timing metadata.

        Uses edge-tts ``Communicate`` class which yields ``WordBoundary``
        events containing offset and duration for each word.
        """
        import edge_tts

        if not text.strip():
            return TTSResult(audio_path=output_path, duration=0.0, word_timings=())

        output_path.parent.mkdir(parents=True, exist_ok=True)

        communicate = edge_tts.Communicate(
            text,
            voice=self._config.voice,
            rate=self._config.rate,
            pitch=self._config.pitch,
            volume=self._config.volume,
        )

        word_timings: list[TTSWordTiming] = []
        audio_duration_ms = 0

        with open(output_path, "wb") as audio_file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    offset = int(chunk["offset"]) // 10000  # 100ns units → ms
                    duration = int(chunk["duration"]) // 10000
                    word_text = chunk.get("text", "")
                    word_timings.append(TTSWordTiming(
                        offset_ms=offset, duration_ms=duration, text=word_text,
                    ))
                    audio_duration_ms = max(audio_duration_ms, offset + duration)

        duration_sec = audio_duration_ms / 1000.0
        self._log.debug("Synthesized '%s' → %s (%.2fs)", text[:50], output_path.name, duration_sec)

        return TTSResult(
            audio_path=output_path,
            duration=duration_sec,
            word_timings=tuple(word_timings),
        )

    def synthesize_sync(self, text: str, output_path: Path) -> TTSResult:
        """Synchronous wrapper around :meth:`synthesize`."""
        return asyncio.run(self.synthesize(text, output_path))

    def synthesize_lines(
        self,
        lines: list[dict],
        output_dir: Path,
        *,
        delay_between: float = 0.2,
    ) -> list[tuple[dict, TTSResult]]:
        """Generate TTS for multiple reaction lines.

        *lines* should be dicts with at least a ``"text"`` key.
        Returns pairs of ``(original_line, tts_result)``.

        Each line gets its own audio file: ``output_dir/reaction_001.mp3``, etc.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        results: list[tuple[dict, TTSResult]] = []

        for idx, line in enumerate(lines):
            text = line.get("text", "").strip()
            if not text:
                continue

            audio_path = output_dir / f"reaction_{idx + 1:03d}.mp3"
            try:
                result = self.synthesize_sync(text, audio_path)
                results.append((line, result))
                self._log.info(
                    "TTS [%d/%d]: '%s' → %.2fs",
                    idx + 1, len(lines), text[:40], result.duration,
                )
            except Exception as exc:
                self._log.error("TTS failed for line %d: %s", idx + 1, exc)

            # Small delay to avoid rate limits
            if idx < len(lines) - 1:
                time.sleep(delay_between)

        return results

    def save_manifest(
        self,
        results: list[tuple[dict, TTSResult]],
        manifest_path: Path,
    ) -> None:
        """Save TTS results as a JSON manifest for downstream modules."""
        manifest = []
        for idx, (line, tts_result) in enumerate(results):
            entry = tts_result_to_dict(tts_result, idx)
            entry["line"] = line  # include original line data
            manifest.append(entry)

        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self._log.info("Saved TTS manifest to %s", manifest_path)
