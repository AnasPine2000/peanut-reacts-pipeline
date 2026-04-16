"""Reaction cache — reuse existing reactions to save LLM API costs.

Strategies (in order of preference):
1. Exact match: same video_id already has cached reactions -> reuse entirely
2. Segment-level: match transcript chunks with similar text (LLM is deterministic-ish)
3. TTS cache: match reaction text to existing TTS audio (save TTS calls)
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class CachedReaction:
    start: float
    text: str
    emotion: str
    intensity: float = 1.0
    tts_path: str = ""


def text_hash(text: str) -> str:
    """Stable hash of normalized text for matching."""
    normalized = text.strip().lower()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:16]


class ReactionCache:
    """SQLite-backed cache of reactions and TTS audio files.

    Can cache:
    - Full reaction sets per video_id
    - Individual reaction text -> TTS audio mappings
    - Transcript chunk -> reactions (for near-duplicate content)
    """

    def __init__(self, cache_dir: Path = None):
        if cache_dir is None:
            cache_dir = Path(__file__).resolve().parents[3] / "cache"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.tts_dir = cache_dir / "tts_cache"
        self.tts_dir.mkdir(exist_ok=True)

        self.db_path = cache_dir / "reaction_cache.db"
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS video_reactions (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT,
                character TEXT,
                reactions_json TEXT,
                created_at TEXT,
                video_duration REAL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tts_cache (
                text_hash TEXT PRIMARY KEY,
                text TEXT,
                voice TEXT,
                rate TEXT,
                tts_path TEXT,
                created_at TEXT,
                hit_count INTEGER DEFAULT 0
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS chunk_cache (
                chunk_hash TEXT,
                character TEXT,
                reactions_json TEXT,
                created_at TEXT,
                PRIMARY KEY (chunk_hash, character)
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tts_voice ON tts_cache(voice)")
        self.conn.commit()

    # ── Full Video Cache ──────────────────────────────────────────

    def get_video_reactions(self, video_id: str, character: str = None) -> Optional[list[dict]]:
        """Get cached reactions for a video. Returns None if not cached."""
        query = "SELECT reactions_json, character FROM video_reactions WHERE video_id = ?"
        params = [video_id]
        if character:
            query += " AND character = ?"
            params.append(character)
        row = self.conn.execute(query, params).fetchone()
        if row:
            reactions = json.loads(row[0])
            log.info("CACHE HIT: video_id=%s, %d reactions (character=%s)",
                     video_id, len(reactions), row[1])
            return reactions
        return None

    def store_video_reactions(
        self,
        video_id: str,
        reactions: list[dict],
        channel_id: str = "",
        character: str = "peanut",
        duration: float = 0,
    ):
        """Store reactions for a video."""
        self.conn.execute(
            """INSERT OR REPLACE INTO video_reactions
               (video_id, channel_id, character, reactions_json, created_at, video_duration)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (video_id, channel_id, character, json.dumps(reactions),
             datetime.utcnow().isoformat(), duration),
        )
        self.conn.commit()
        log.info("CACHED: video_id=%s, %d reactions", video_id, len(reactions))

    # ── TTS Cache ─────────────────────────────────────────────────

    def get_tts(self, text: str, voice: str, rate: str = "+0%") -> Optional[Path]:
        """Get cached TTS audio file for exact text + voice match."""
        th = text_hash(f"{voice}|{rate}|{text}")
        row = self.conn.execute(
            "SELECT tts_path FROM tts_cache WHERE text_hash = ?", (th,)
        ).fetchone()
        if row:
            path = Path(row[0])
            if path.exists():
                self.conn.execute(
                    "UPDATE tts_cache SET hit_count = hit_count + 1 WHERE text_hash = ?",
                    (th,),
                )
                self.conn.commit()
                log.debug("TTS cache HIT: '%s...'", text[:40])
                return path
        return None

    def store_tts(self, text: str, voice: str, rate: str, tts_path: Path):
        """Store a TTS audio file in cache."""
        th = text_hash(f"{voice}|{rate}|{text}")
        cache_path = self.tts_dir / f"{th}.mp3"
        if tts_path.exists() and not cache_path.exists():
            shutil.copy2(tts_path, cache_path)
        self.conn.execute(
            """INSERT OR REPLACE INTO tts_cache
               (text_hash, text, voice, rate, tts_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (th, text, voice, rate, str(cache_path), datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    # ── Chunk-Level Cache ─────────────────────────────────────────

    def get_chunk_reactions(self, chunk_text: str, character: str) -> Optional[list[dict]]:
        """Get cached reactions for a transcript chunk (similar content)."""
        ch = text_hash(chunk_text)
        row = self.conn.execute(
            "SELECT reactions_json FROM chunk_cache WHERE chunk_hash = ? AND character = ?",
            (ch, character),
        ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def store_chunk_reactions(
        self, chunk_text: str, character: str, reactions: list[dict]
    ):
        """Store reactions for a chunk."""
        ch = text_hash(chunk_text)
        self.conn.execute(
            """INSERT OR REPLACE INTO chunk_cache
               (chunk_hash, character, reactions_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (ch, character, json.dumps(reactions), datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    # ── Stats ─────────────────────────────────────────────────────

    def stats(self) -> dict:
        videos = self.conn.execute("SELECT COUNT(*) FROM video_reactions").fetchone()[0]
        tts_count = self.conn.execute("SELECT COUNT(*) FROM tts_cache").fetchone()[0]
        tts_hits = self.conn.execute("SELECT SUM(hit_count) FROM tts_cache").fetchone()[0] or 0
        chunks = self.conn.execute("SELECT COUNT(*) FROM chunk_cache").fetchone()[0]
        return {
            "videos_cached": videos,
            "tts_files_cached": tts_count,
            "tts_cache_hits": tts_hits,
            "chunks_cached": chunks,
        }

    def close(self):
        self.conn.close()


# Singleton helper
_default_cache = None

def get_cache() -> ReactionCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = ReactionCache()
    return _default_cache
