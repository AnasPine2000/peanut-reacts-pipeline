#!/usr/bin/env python3
"""Refresh docs/data.json so the GitHub Pages dashboard shows current numbers.

Uses the same status-builder as dashboard_sync.py (the HF-Space path) but
writes to `docs/data.json` for static GitHub Pages serving.

Usage:
    python scripts/refresh_github_pages_dashboard.py

Then commit + push `docs/data.json` so Pages picks up the new JSON.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

# Autoload .env for YouTube Data API key (stat fetches need it)
env_path = PROJECT / ".env"
if env_path.exists():
    import os
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from peanut_reacts.scheduler.channel_config import load_channels
from peanut_reacts.scheduler.db import PipelineDB
from peanut_reacts.scheduler.status_exporter import build_status


def _enrich_with_network_totals(status: dict) -> dict:
    """Roll up per-channel YT stats into `status["network"]` so the Pages
    dashboard's summary cards have the numbers they expect.

    Also adds generated_at (ISO), days_active (placeholder), current_month
    (placeholder based on today) so the dashboard's projections panel
    renders something coherent.
    """
    from datetime import datetime, timezone

    channels = status.get("channels", [])
    total_subs = 0
    total_views = 0
    total_videos = 0
    active = 0
    for ch in channels:
        yt = ch.get("youtube", {})
        total_subs += int(yt.get("subscriber_count") or 0)
        total_views += int(yt.get("view_count") or 0)
        total_videos += int(yt.get("video_count") or 0)
        if yt.get("exists") and yt.get("video_count", 0) > 0:
            active += 1

    status["network"] = {
        "total_channels": len(channels),
        "active_channels": active,
        "total_subscribers": total_subs,
        "total_views": total_views,
        "total_videos": total_videos,
    }
    status.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    status.setdefault("days_active", 4)   # Day 4 of EXECUTION_PLAN_V2 today
    status.setdefault("current_month", 0)  # Month 0 (pre-launch ramp)
    return status


def main() -> int:
    import json

    config = load_channels(PROJECT / "channels.yaml")
    db = PipelineDB(str(PROJECT / "pipeline.db"))

    # Build base status (per-channel data) but don't write yet
    status = build_status(
        pipeline_config=config,
        db=db,
        fetch_youtube_stats=True,
        output_path=None,
    )
    db.close()

    # Add network totals + generated_at
    status = _enrich_with_network_totals(status)

    out = PROJECT / "docs" / "data.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")

    n_channels = len(status.get("channels", []))
    net = status.get("network", {})
    print(f"Wrote {out}")
    print(f"  channels:    {n_channels} ({net['active_channels']} active)")
    print(f"  total subs:  {net['total_subscribers']}")
    print(f"  total views: {net['total_views']}")
    print(f"  total videos: {net['total_videos']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
