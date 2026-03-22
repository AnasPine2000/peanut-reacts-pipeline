"""
SRT subtitle parsing utilities.

Consolidates SRT parsing previously duplicated across 3 scripts.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def srt_timestamp_to_seconds(ts: str) -> float:
    """Convert an SRT timestamp ``HH:MM:SS,mmm`` to seconds."""
    h, m, s_ms = ts.split(":")
    s, ms = s_ms.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(srt_path: Path) -> list[dict]:
    """Parse an SRT file into a list of ``{"start", "end", "text"}`` dicts.

    Returns an empty list on read errors rather than crashing.
    """
    try:
        content = srt_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("Error reading SRT file %s: %s", srt_path, exc)
        return []

    transcript: list[dict] = []
    blocks = content.strip().split("\n\n")

    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue

        time_line = lines[1]
        try:
            start_str, end_str = time_line.split(" --> ")
            start = srt_timestamp_to_seconds(start_str.strip())
            end = srt_timestamp_to_seconds(end_str.strip())
        except Exception as exc:
            logger.error("Error parsing time in block: %s — %s", block[:80], exc)
            continue

        text = " ".join(lines[2:])
        transcript.append({"start": start, "end": end, "text": text})

    logger.info("Parsed %d segments from %s", len(transcript), srt_path.name)
    return transcript
