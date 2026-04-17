"""SQLite state store for Reddit story pipeline.

Single table `reddit_stories` — the source of truth for what's been
scraped, what's been rendered, and what's been posted. The scraper
inserts `status='new'` rows; the pipeline picks those up and transitions
through the states below.

States:
    new        — just scraped, not yet processed
    processing — video render in progress
    ready      — video rendered, awaiting upload scheduling
    posted     — published to at least one platform
    rejected   — filter flagged it (too long, NSFW, slurs, etc.)
    failed     — render or upload failed, see error_msg
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_DB_PATH = Path("reddit_queue.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS reddit_stories (
    id              TEXT PRIMARY KEY,          -- reddit post id (deduping key)
    subreddit       TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    score           INTEGER NOT NULL,
    num_comments    INTEGER NOT NULL,
    author          TEXT,
    url             TEXT NOT NULL,             -- permalink
    created_utc     INTEGER NOT NULL,
    scraped_at      TEXT NOT NULL,             -- ISO timestamp
    status          TEXT NOT NULL DEFAULT 'new',
    char_count      INTEGER NOT NULL,
    tts_seconds_est REAL,                      -- estimated TTS duration
    video_path      TEXT,                      -- filled when rendered
    youtube_url     TEXT,
    tiktok_url      TEXT,
    posted_at       TEXT,
    error_msg       TEXT,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rs_status ON reddit_stories(status);
CREATE INDEX IF NOT EXISTS idx_rs_subreddit ON reddit_stories(subreddit);
CREATE INDEX IF NOT EXISTS idx_rs_score ON reddit_stories(score DESC);
"""


@dataclass
class Story:
    id: str
    subreddit: str
    title: str
    body: str
    score: int
    num_comments: int
    author: Optional[str]
    url: str
    created_utc: int
    status: str = "new"
    char_count: int = 0
    tts_seconds_est: Optional[float] = None
    video_path: Optional[str] = None
    youtube_url: Optional[str] = None
    tiktok_url: Optional[str] = None
    error_msg: Optional[str] = None

    @property
    def full_text(self) -> str:
        """Title + body, joined for TTS."""
        if self.body.strip():
            return f"{self.title}. {self.body}"
        return self.title


class StoryDB:
    """Thin SQLite wrapper — one connection per instance, not thread-safe.

    For a cron-driven pipeline with sequential jobs this is fine. If we
    ever parallelize, switch to a connection pool or SQLAlchemy.
    """

    def __init__(self, path: Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── Read paths ────────────────────────────────────────────────────

    def has_id(self, story_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM reddit_stories WHERE id = ? LIMIT 1", (story_id,)
        )
        return cur.fetchone() is not None

    def get(self, story_id: str) -> Optional[Story]:
        cur = self._conn.execute(
            "SELECT * FROM reddit_stories WHERE id = ?", (story_id,)
        )
        row = cur.fetchone()
        return _row_to_story(row) if row else None

    def iter_by_status(self, status: str, limit: int = 100) -> Iterator[Story]:
        cur = self._conn.execute(
            "SELECT * FROM reddit_stories WHERE status = ? "
            "ORDER BY score DESC LIMIT ?",
            (status, limit),
        )
        for row in cur:
            yield _row_to_story(row)

    def count_by_status(self) -> dict[str, int]:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM reddit_stories GROUP BY status"
        )
        return {r["status"]: r["n"] for r in cur}

    # ── Write paths ───────────────────────────────────────────────────

    def insert_new(self, story: Story) -> bool:
        """Insert a freshly-scraped story. Returns True if new, False if dup."""
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        try:
            self._conn.execute(
                """INSERT INTO reddit_stories
                   (id, subreddit, title, body, score, num_comments, author,
                    url, created_utc, scraped_at, status, char_count,
                    tts_seconds_est, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story.id, story.subreddit, story.title, story.body,
                    story.score, story.num_comments, story.author, story.url,
                    story.created_utc, now, story.status or "new",
                    story.char_count or len(story.full_text),
                    story.tts_seconds_est, now,
                ),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # duplicate id — already scraped

    def set_status(
        self,
        story_id: str,
        status: str,
        *,
        error_msg: Optional[str] = None,
        video_path: Optional[str] = None,
        youtube_url: Optional[str] = None,
        tiktok_url: Optional[str] = None,
    ) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        fields: list[str] = ["status = ?", "updated_at = ?"]
        params: list[object] = [status, now]
        if error_msg is not None:
            fields.append("error_msg = ?")
            params.append(error_msg)
        if video_path is not None:
            fields.append("video_path = ?")
            params.append(video_path)
        if youtube_url is not None:
            fields.append("youtube_url = ?")
            params.append(youtube_url)
        if tiktok_url is not None:
            fields.append("tiktok_url = ?")
            params.append(tiktok_url)
        if status == "posted":
            fields.append("posted_at = ?")
            params.append(now)
        params.append(story_id)

        self._conn.execute(
            f"UPDATE reddit_stories SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        self._conn.commit()


def _row_to_story(row: sqlite3.Row) -> Story:
    return Story(
        id=row["id"],
        subreddit=row["subreddit"],
        title=row["title"],
        body=row["body"],
        score=row["score"],
        num_comments=row["num_comments"],
        author=row["author"],
        url=row["url"],
        created_utc=row["created_utc"],
        status=row["status"],
        char_count=row["char_count"],
        tts_seconds_est=row["tts_seconds_est"],
        video_path=row["video_path"],
        youtube_url=row["youtube_url"],
        tiktok_url=row["tiktok_url"],
        error_msg=row["error_msg"],
    )
