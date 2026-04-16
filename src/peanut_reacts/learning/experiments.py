"""Experiment tracker — records which recommendations were applied to which
videos, and measures the delta in performance.

This is how "exponential improvement" becomes measurable:
- Each generation of videos gets recorded with applied_recommendations list
- After a video accumulates enough views (e.g., 7 days), we compare its
  performance to the baseline (channel average before the experiment)
- Positive deltas confirm the recommendation; negative deltas flag it for review
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


def record_experiment(
    db_path: str,
    video_id: str,
    channel_id: str,
    applied_recommendations: list[dict],
    overrides_notes: list[str],
):
    """Store an experiment record at the moment of upload.

    Measured later via `evaluate_experiments` once the video has data.
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS video_experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                applied_rec_ids TEXT,
                overrides_json TEXT,
                baseline_median_views INTEGER,
                baseline_avg_views INTEGER,
                measured_at TEXT,
                measured_views INTEGER,
                delta_vs_median_pct REAL,
                delta_vs_avg_pct REAL
            )
        """)

        rec_ids = json.dumps([r.get("id") for r in applied_recommendations])
        overrides_json = json.dumps({
            "notes": overrides_notes,
            "recommendations": [
                {
                    "category": r.get("category"),
                    "recommendation": r.get("recommendation"),
                    "priority": r.get("priority"),
                }
                for r in applied_recommendations
            ],
        })

        # Compute baseline BEFORE this upload
        baseline = _compute_baseline(conn, channel_id)

        conn.execute("""
            INSERT INTO video_experiments
            (video_id, channel_id, created_at, applied_rec_ids, overrides_json,
             baseline_median_views, baseline_avg_views)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            video_id, channel_id, datetime.utcnow().isoformat(),
            rec_ids, overrides_json,
            baseline.get("median", 0), baseline.get("avg", 0),
        ))
        conn.commit()
        log.info("Recorded experiment: video=%s, applied_recs=%d, baseline_median=%d",
                 video_id, len(applied_recommendations), baseline.get("median", 0))
    finally:
        conn.close()


def _compute_baseline(conn, channel_id: str) -> dict:
    """Compute current channel baseline (median and average views) across all tracked videos."""
    import statistics
    rows = conn.execute("""
        SELECT s.view_count FROM videos v
        LEFT JOIN (
            SELECT video_id, MAX(snapshot_at) as max_at
            FROM snapshots GROUP BY video_id
        ) latest ON v.video_id = latest.video_id
        LEFT JOIN snapshots s ON s.video_id = v.video_id AND s.snapshot_at = latest.max_at
        WHERE v.channel_id = ? AND s.view_count > 0
    """, (channel_id,)).fetchall()

    views = [r[0] for r in rows if r[0]]
    if not views:
        return {"median": 0, "avg": 0, "count": 0}

    return {
        "median": int(statistics.median(views)),
        "avg": int(statistics.mean(views)),
        "count": len(views),
    }


def _ensure_table(conn):
    """Create the video_experiments table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS video_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            applied_rec_ids TEXT,
            overrides_json TEXT,
            baseline_median_views INTEGER,
            baseline_avg_views INTEGER,
            measured_at TEXT,
            measured_views INTEGER,
            delta_vs_median_pct REAL,
            delta_vs_avg_pct REAL
        )
    """)


def evaluate_experiments(db_path: str, channel_id: Optional[str] = None) -> list[dict]:
    """Re-measure all unfinished experiments against their latest view counts.

    Returns list of experiments with current delta vs baseline.
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    try:
        q = """
            SELECT e.*, s.view_count as current_views
            FROM video_experiments e
            LEFT JOIN (
                SELECT video_id, MAX(snapshot_at) as max_at
                FROM snapshots GROUP BY video_id
            ) latest ON e.video_id = latest.video_id
            LEFT JOIN snapshots s ON s.video_id = e.video_id AND s.snapshot_at = latest.max_at
        """
        params = ()
        if channel_id:
            q += " WHERE e.channel_id = ?"
            params = (channel_id,)
        q += " ORDER BY e.created_at DESC"

        rows = conn.execute(q, params).fetchall()
        results = []

        for row in rows:
            r = dict(row)
            current = r.get("current_views") or 0
            median = r.get("baseline_median_views") or 0
            avg = r.get("baseline_avg_views") or 0

            delta_median = ((current - median) / median * 100) if median > 0 else 0
            delta_avg = ((current - avg) / avg * 100) if avg > 0 else 0

            r["current_views"] = current
            r["delta_vs_median_pct"] = round(delta_median, 1)
            r["delta_vs_avg_pct"] = round(delta_avg, 1)

            # Update the stored values
            conn.execute("""
                UPDATE video_experiments
                SET measured_at = ?, measured_views = ?,
                    delta_vs_median_pct = ?, delta_vs_avg_pct = ?
                WHERE id = ?
            """, (
                datetime.utcnow().isoformat(), current,
                round(delta_median, 1), round(delta_avg, 1),
                r["id"],
            ))

            try:
                r["overrides"] = json.loads(r.get("overrides_json") or "{}")
            except Exception:
                r["overrides"] = {}
            results.append(r)

        conn.commit()
        return results
    finally:
        conn.close()


def get_recent_experiments(db_path: str, limit: int = 20, channel_id: Optional[str] = None) -> list[dict]:
    """Get recent experiments (for dashboard display)."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    try:
        q = "SELECT * FROM video_experiments"
        params = ()
        if channel_id:
            q += " WHERE channel_id = ?"
            params = (channel_id,)
        q += " ORDER BY created_at DESC LIMIT ?"

        rows = conn.execute(q, params + (limit,)).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["overrides"] = json.loads(d.get("overrides_json") or "{}")
            except Exception:
                d["overrides"] = {}
            results.append(d)
        return results
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def summarize_experiments(db_path: str, channel_id: Optional[str] = None) -> dict:
    """Summarize experiment results for a channel."""
    experiments = evaluate_experiments(db_path, channel_id)
    if not experiments:
        return {"total": 0, "measured": 0, "positive": 0, "negative": 0}

    measured = [e for e in experiments if e.get("current_views", 0) > 0]
    positive = [e for e in measured if e.get("delta_vs_median_pct", 0) > 10]
    negative = [e for e in measured if e.get("delta_vs_median_pct", 0) < -10]

    # Which recommendation categories were most effective?
    category_impact = {}
    for exp in measured:
        overrides = exp.get("overrides", {})
        for rec in overrides.get("recommendations", []):
            cat = rec.get("category", "unknown")
            if cat not in category_impact:
                category_impact[cat] = []
            category_impact[cat].append(exp.get("delta_vs_median_pct", 0))

    category_avg = {
        cat: round(sum(deltas) / len(deltas), 1)
        for cat, deltas in category_impact.items() if deltas
    }

    return {
        "total": len(experiments),
        "measured": len(measured),
        "positive": len(positive),
        "negative": len(negative),
        "avg_delta_pct": round(
            sum(e.get("delta_vs_median_pct", 0) for e in measured) / len(measured), 1
        ) if measured else 0,
        "category_impact": category_avg,
        "recent_experiments": experiments[:10],
    }
