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
    """Shape status into what docs/index.html expects and roll up totals.

    The legacy dashboard HTML (committed Apr 16) reads:
      - d.network.*              — totals (we compute below)
      - d.links.{github,livestream,hf_space} — string URLs, not empty
      - d.channels[].character   — STRING id, used as dict key for emoji/color
      - d.channels[].youtube_url — top-level string (not nested under .youtube)
      - d.channels[].tiktok_url  — top-level string

    The new status_exporter emits `character` as a nested object. We flatten
    it back to the expected shape while preserving the richer data under
    `character_profile` in case another consumer needs it.
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

        # Legacy-compat reshaping
        char = ch.get("character")
        if isinstance(char, dict):
            ch["character_profile"] = char
            ch["character"] = char.get("id", "")
        ch.setdefault("youtube_url", yt.get("url") or yt.get("handle_url") or "")
        ch.setdefault("tiktok_url", "")

        # HTML reads v.views but stats_fetcher writes view_count.
        # fmt(undefined).toString() would crash renderChannels().
        for vid in yt.get("latest_videos", []) or []:
            vid.setdefault("views", vid.get("view_count", 0))

    status["network"] = {
        "total_channels": len(channels),
        "active_channels": active,
        "total_subscribers": total_subs,
        "total_views": total_views,
        "total_videos": total_videos,
    }
    # Ensure links has non-empty fields the HTML checks for. Use or-coalesce
    # so status_exporter's empty-string defaults are replaced with real URLs.
    links = status.setdefault("links", {})
    links["github"] = links.get("github") or "https://github.com/AnasPine2000/peanut-reacts-pipeline"
    links.setdefault("livestream", "")
    links.setdefault("hf_space", "")

    status["generated_at"] = datetime.now(timezone.utc).isoformat()
    status.setdefault("days_active", 4)   # Day 4 of EXECUTION_PLAN_V2 today
    status.setdefault("current_month", 0)  # Month 0 (pre-launch ramp)

    # HTML's renderProjections/renderAnalytics both dereference
    # DATA.projections.targets — without it the whole render chain
    # crashes AFTER channels, hiding the "updated" stamp and charts.
    # Ramp to $20k/mo target by M12 per EXECUTION_PLAN_V2.
    status.setdefault("projections", {
        "targets": [0, 0, 0, 100, 500, 1000, 2500, 5000, 8000, 12000, 15000, 18000, 20000],
    })

    # Merge live pipeline status (active run + recent uploads) so the
    # dashboard can render the Live Pipeline card. The status file is
    # written by the pipeline itself via pipeline_status.RunTracker.
    try:
        from peanut_reacts.scheduler.pipeline_status import read_status
        status["pipeline"] = read_status()
    except Exception as e:
        import sys
        print(f"[WARN] Could not load pipeline_status: {e}", file=sys.stderr)
        status["pipeline"] = {"active": None, "recent": [], "updated_at": None}

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
