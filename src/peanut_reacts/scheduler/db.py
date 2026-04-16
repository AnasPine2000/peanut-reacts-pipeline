"""SQLite state management for the autonomous pipeline."""
from __future__ import annotations

import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    video_id TEXT,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    step TEXT DEFAULT 'queued',
    error_msg TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    output_path TEXT,
    youtube_url TEXT
);

CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    name TEXT,
    total_uploads INTEGER DEFAULT 0,
    total_views INTEGER DEFAULT 0,
    subscriber_count INTEGER DEFAULT 0,
    last_run_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    videos_processed INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running'
);

CREATE INDEX IF NOT EXISTS idx_jobs_channel ON jobs(channel_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_runs_channel ON runs(channel_id);
"""


class PipelineDB:
    def __init__(self, db_path: str = "pipeline.db"):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        log.info("Database ready at %s", self.db_path)

    def create_job(self, channel_id: str, video_id: str = "", title: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO jobs (channel_id, video_id, title, status, step, created_at) VALUES (?, ?, ?, 'queued', 'queued', ?)",
            (channel_id, video_id, title, datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_job(self, job_id: int, **kwargs):
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [job_id]
        self.conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)
        self.conn.commit()

    def start_job(self, job_id: int, step: str = "download"):
        self.update_job(job_id, status="running", step=step, started_at=datetime.utcnow().isoformat())

    def complete_job(self, job_id: int, output_path: str = "", youtube_url: str = ""):
        self.update_job(job_id, status="done", step="complete",
                        completed_at=datetime.utcnow().isoformat(),
                        output_path=output_path, youtube_url=youtube_url)

    def fail_job(self, job_id: int, error_msg: str, step: str = ""):
        self.update_job(job_id, status="failed", error_msg=error_msg,
                        completed_at=datetime.utcnow().isoformat(),
                        **({'step': step} if step else {}))

    def get_recent_jobs(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_channel_jobs(self, channel_id: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE channel_id = ? ORDER BY created_at DESC LIMIT ?",
            (channel_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def start_run(self, channel_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (channel_id, started_at) VALUES (?, ?)",
            (channel_id, datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, videos: int = 0, errors: int = 0):
        status = "done" if errors == 0 else "partial"
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, videos_processed = ?, errors = ?, status = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), videos, errors, status, run_id),
        )
        self.conn.commit()

    def upsert_channel(self, channel_id: str, name: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO channels (id, name) VALUES (?, ?)",
            (channel_id, name),
        )
        self.conn.commit()

    def update_channel_stats(self, channel_id: str, **kwargs):
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [channel_id]
        self.conn.execute(f"UPDATE channels SET {sets} WHERE id = ?", vals)
        self.conn.commit()

    def get_all_channels(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM channels").fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        done = self.conn.execute("SELECT COUNT(*) FROM jobs WHERE status='done'").fetchone()[0]
        failed = self.conn.execute("SELECT COUNT(*) FROM jobs WHERE status='failed'").fetchone()[0]
        running = self.conn.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
        return {"total": total, "done": done, "failed": failed, "running": running}

    def export_status(self) -> dict:
        return {
            "stats": self.get_stats(),
            "channels": self.get_all_channels(),
            "recent_jobs": self.get_recent_jobs(20),
            "updated_at": datetime.utcnow().isoformat(),
        }

    def close(self):
        self.conn.close()
