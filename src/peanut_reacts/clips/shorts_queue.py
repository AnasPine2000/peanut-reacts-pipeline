"""Shorts posting queue — schedules clips across platforms throughout the day.

Instead of posting all clips immediately after extraction, they go into a
queue with scheduled posting times. A separate cron job picks clips from
the queue and posts them at optimal times (7am, 12pm, 6pm, 9pm, 11pm).

This prevents flooding + maximizes algorithm exposure per clip.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS shorts_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    source_video_id TEXT,
    source_video_title TEXT,
    peak_start REAL,
    peak_score REAL,
    peak_emotion TEXT,
    clip_path TEXT NOT NULL,
    caption_yt TEXT,
    caption_tt TEXT,
    hook_text TEXT,
    hashtags TEXT,
    status TEXT DEFAULT 'ready',
    scheduled_at TEXT,
    posted_yt_at TEXT,
    posted_tt_at TEXT,
    youtube_short_url TEXT,
    tiktok_url TEXT,
    error_msg TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sq_status ON shorts_queue(status);
CREATE INDEX IF NOT EXISTS idx_sq_channel ON shorts_queue(channel_id);
CREATE INDEX IF NOT EXISTS idx_sq_scheduled ON shorts_queue(scheduled_at);
"""


class ShortsQueue:
    def __init__(self, db_path: str = "shorts_queue.db"):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def enqueue(
        self,
        channel_id: str,
        clip_path: str,
        source_video_id: str = "",
        source_video_title: str = "",
        peak_start: float = 0,
        peak_score: float = 0,
        peak_emotion: str = "",
        caption_yt: str = "",
        caption_tt: str = "",
        hook_text: str = "",
        hashtags: list[str] = None,
    ) -> int:
        """Add a clip to the posting queue."""
        cur = self.conn.execute(
            """INSERT INTO shorts_queue
               (channel_id, source_video_id, source_video_title,
                peak_start, peak_score, peak_emotion,
                clip_path, caption_yt, caption_tt, hook_text, hashtags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (channel_id, source_video_id, source_video_title,
             peak_start, peak_score, peak_emotion,
             clip_path, caption_yt, caption_tt, hook_text,
             json.dumps(hashtags or []),
             datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        clip_id = cur.lastrowid
        log.info("Queued clip #%d for [%s]: %s", clip_id, channel_id, Path(clip_path).name)
        return clip_id

    def enqueue_batch(self, channel_id: str, clips: list[dict]) -> list[int]:
        """Enqueue multiple clips at once. Each dict has the fields from enqueue()."""
        ids = []
        for clip in clips:
            clip_id = self.enqueue(channel_id=channel_id, **clip)
            ids.append(clip_id)
        return ids

    def schedule_next_clips(
        self,
        channel_id: str,
        clips_per_day: int = 5,
        posting_times: list[str] = None,
    ) -> int:
        """Assign scheduled_at to the next batch of unscheduled 'ready' clips.

        Distributes clips evenly across today's remaining posting times.
        Returns number of clips scheduled.
        """
        if posting_times is None:
            posting_times = ["07:00", "12:00", "18:00", "21:00", "23:00"]

        # Get unscheduled clips for this channel
        rows = self.conn.execute(
            """SELECT id FROM shorts_queue
               WHERE channel_id = ? AND status = 'ready' AND scheduled_at IS NULL
               ORDER BY peak_score DESC LIMIT ?""",
            (channel_id, clips_per_day),
        ).fetchall()

        if not rows:
            return 0

        now = datetime.utcnow()
        today = now.date()
        scheduled = 0

        for i, row in enumerate(rows):
            # Pick the next available posting time
            slot_idx = i % len(posting_times)
            time_str = posting_times[slot_idx]
            hour, minute = map(int, time_str.split(":"))

            # If today's slot already passed, schedule for tomorrow
            slot_dt = datetime.combine(today, datetime.min.time().replace(hour=hour, minute=minute))
            if slot_dt <= now:
                slot_dt += timedelta(days=1)

            # If multiple clips map to same slot, spread across days
            slot_dt += timedelta(days=i // len(posting_times))

            self.conn.execute(
                "UPDATE shorts_queue SET status = 'scheduled', scheduled_at = ? WHERE id = ?",
                (slot_dt.isoformat(), row["id"]),
            )
            scheduled += 1

        self.conn.commit()
        log.info("Scheduled %d clips for [%s]", scheduled, channel_id)
        return scheduled

    def get_due_for_posting(self, platform: str = "youtube_shorts", limit: int = 1) -> list[dict]:
        """Get clips that are due for posting right now.

        platform: 'youtube_shorts' or 'tiktok'
        Returns clips where scheduled_at <= now and not yet posted on this platform.
        """
        now = datetime.utcnow().isoformat()

        if platform == "youtube_shorts":
            condition = "posted_yt_at IS NULL"
        elif platform == "tiktok":
            condition = "posted_tt_at IS NULL"
        else:
            condition = "1=1"

        rows = self.conn.execute(
            f"""SELECT * FROM shorts_queue
                WHERE status IN ('scheduled', 'posted_yt', 'posted_tt')
                AND scheduled_at <= ?
                AND {condition}
                ORDER BY scheduled_at ASC LIMIT ?""",
            (now, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_posted(self, clip_id: int, platform: str, url: str = ""):
        """Mark a clip as posted on a specific platform."""
        now = datetime.utcnow().isoformat()
        if platform == "youtube_shorts":
            self.conn.execute(
                "UPDATE shorts_queue SET posted_yt_at = ?, youtube_short_url = ? WHERE id = ?",
                (now, url, clip_id),
            )
        elif platform == "tiktok":
            self.conn.execute(
                "UPDATE shorts_queue SET posted_tt_at = ?, tiktok_url = ? WHERE id = ?",
                (now, url, clip_id),
            )

        # Check if posted on all configured platforms
        row = self.conn.execute(
            "SELECT posted_yt_at, posted_tt_at FROM shorts_queue WHERE id = ?",
            (clip_id,),
        ).fetchone()
        if row and row["posted_yt_at"] and row["posted_tt_at"]:
            self.conn.execute("UPDATE shorts_queue SET status = 'posted_all' WHERE id = ?", (clip_id,))
        elif row:
            posted_platform = "posted_yt" if row["posted_yt_at"] else "posted_tt"
            self.conn.execute(f"UPDATE shorts_queue SET status = ? WHERE id = ?", (posted_platform, clip_id))

        self.conn.commit()
        log.info("Marked clip #%d as posted on %s", clip_id, platform)

    def mark_failed(self, clip_id: int, error: str):
        self.conn.execute(
            "UPDATE shorts_queue SET status = 'failed', error_msg = ? WHERE id = ?",
            (error[:500], clip_id),
        )
        self.conn.commit()

    def get_queue_stats(self, channel_id: str = None) -> dict:
        """Get queue statistics."""
        where = f"WHERE channel_id = '{channel_id}'" if channel_id else ""
        total = self.conn.execute(f"SELECT COUNT(*) FROM shorts_queue {where}").fetchone()[0]
        ready = self.conn.execute(f"SELECT COUNT(*) FROM shorts_queue {where} AND status = 'ready'" if where else
                                  "SELECT COUNT(*) FROM shorts_queue WHERE status = 'ready'").fetchone()[0]
        scheduled = self.conn.execute(f"SELECT COUNT(*) FROM shorts_queue {where} AND status = 'scheduled'" if where else
                                      "SELECT COUNT(*) FROM shorts_queue WHERE status = 'scheduled'").fetchone()[0]
        posted = self.conn.execute(f"SELECT COUNT(*) FROM shorts_queue {where} AND status = 'posted_all'" if where else
                                   "SELECT COUNT(*) FROM shorts_queue WHERE status = 'posted_all'").fetchone()[0]
        failed = self.conn.execute(f"SELECT COUNT(*) FROM shorts_queue {where} AND status = 'failed'" if where else
                                   "SELECT COUNT(*) FROM shorts_queue WHERE status = 'failed'").fetchone()[0]
        return {
            "total": total,
            "ready": ready,
            "scheduled": scheduled,
            "posted": posted,
            "failed": failed,
        }

    def get_recent(self, limit: int = 20, channel_id: str = None) -> list[dict]:
        q = "SELECT * FROM shorts_queue"
        p = ()
        if channel_id:
            q += " WHERE channel_id = ?"
            p = (channel_id,)
        q += " ORDER BY created_at DESC LIMIT ?"
        rows = self.conn.execute(q, p + (limit,)).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()
