"""Poll the HasanAbi Archive source channel and surface new streams.

Intended usage:
  1. Run on a schedule (GitHub Actions cron every 30 min — see
     .github/workflows/hasan-watcher.yml) so it keeps polling even when
     the local machine is off.
  2. On each run, fetches the latest N videos from the archive channel
     via yt-dlp, compares against `data/hasan_watcher_state.json`, and
     appends any newly-seen video_ids to `data/hasan_trigger_queue.json`.
  3. The local machine's `process_hasan_queue.py` reads that queue and
     fires the pipeline for each pending entry.

Design notes:
  - We persist BOTH a state file (the set of all video_ids we've ever
    seen — never shrinks) AND a queue file (pending-to-process — shrinks
    when the local processor removes done items). Two-file split means
    the watcher and the processor can each mutate their own file without
    stepping on each other.
  - The watcher's commit message names the video title so the user can
    see "oh, a new Trump stream dropped" from git notifications alone.
  - Idempotent: running twice in a row with no new videos is a no-op.
  - Falls back gracefully if yt-dlp is slow / rate-limited: any video
    that was in state before is still in state after.

Exit codes:
  0 = success (may or may not have found new videos)
  1 = fetch failed entirely (network / yt-dlp error)
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "hasan_watcher_state.json"
QUEUE_PATH = ROOT / "data" / "hasan_trigger_queue.json"
ARCHIVE_CHANNEL = "https://www.youtube.com/@HasanAbiArchivefan0/videos"

# How many most-recent videos to check each poll. Streams drop once per
# day, so checking the top 5 covers us against any short outage (up to
# ~5 missed days still gets caught). Larger numbers cost more but don't
# hurt correctness.
POLL_WINDOW = 5


def fetch_latest_videos(channel_url: str, n: int) -> list[dict]:
    """Return the n most-recent videos from a channel as dicts."""
    cmd = [
        "yt-dlp", "--flat-playlist", "--no-warnings",
        "--print", "%(id)s\t%(title)s\t%(duration)s\t%(upload_date)s",
        "--playlist-end", str(n),
        channel_url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        log.error("yt-dlp timed out after 90s")
        return []
    except FileNotFoundError:
        log.error("yt-dlp not installed on this box. `pip install yt-dlp`")
        return []

    if r.returncode != 0:
        log.error("yt-dlp exit %d. stderr: %s", r.returncode, r.stderr[:500])
        return []

    videos = []
    for line in r.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        videos.append({
            "id": parts[0],
            "title": parts[1],
            "duration_s": _safe_float(parts[2]) if len(parts) > 2 else 0.0,
            "upload_date": parts[3] if len(parts) > 3 else "",
        })
    return videos


def _safe_float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def load_json(path: Path, default):
    """Load JSON — return default on missing or malformed."""
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not read %s (%s) — using default", path, e)
        return default


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False)
    tmp.replace(path)


def main() -> int:
    log.info("Polling %s (latest %d videos)", ARCHIVE_CHANNEL, POLL_WINDOW)
    latest = fetch_latest_videos(ARCHIVE_CHANNEL, POLL_WINDOW)
    if not latest:
        log.error("No videos returned — treating as fetch failure")
        return 1

    # Load current state (set of already-seen IDs) and queue (pending list)
    state = load_json(STATE_PATH, {
        "seen_ids": [],
        "last_poll_at": None,
        "total_polls": 0,
        "total_new_detected": 0,
    })
    queue = load_json(QUEUE_PATH, {"pending": [], "processed": []})

    seen = set(state.get("seen_ids", []))
    pending_ids = {p["id"] for p in queue.get("pending", [])}
    processed_ids = {p["id"] for p in queue.get("processed", [])}

    new_entries = []
    for vid in latest:
        vid_id = vid["id"]
        if (
            vid_id not in seen
            and vid_id not in pending_ids
            and vid_id not in processed_ids
        ):
            new_entries.append(vid)

    now = datetime.now(timezone.utc).isoformat()
    state["last_poll_at"] = now
    state["total_polls"] = int(state.get("total_polls", 0)) + 1

    if new_entries:
        log.info("NEW STREAMS detected: %d", len(new_entries))
        for vid in new_entries:
            hrs = vid["duration_s"] / 3600 if vid["duration_s"] else 0
            log.info("  + %s  [%.1fh]  %s", vid["id"], hrs, vid["title"])
            queue["pending"].append({
                "id": vid["id"],
                "title": vid["title"],
                "duration_s": vid["duration_s"],
                "upload_date": vid["upload_date"],
                "url": f"https://youtube.com/watch?v={vid['id']}",
                "queued_at": now,
                "source": "watch_hasan_source",
            })
            seen.add(vid["id"])

        state["total_new_detected"] = (
            int(state.get("total_new_detected", 0)) + len(new_entries)
        )
    else:
        log.info("No new streams (all %d latest videos already seen)", len(latest))

    # Bounded growth: seen_ids can grow forever, but YouTube video IDs
    # are small and we only need the set for this one channel. Cap at
    # 500 most-recent additions to keep the file under a few KB.
    state["seen_ids"] = list(seen)[-500:]
    save_json(STATE_PATH, state)
    save_json(QUEUE_PATH, queue)

    # Print a summary line that the GitHub Actions workflow picks up
    # for the commit message: "hasan-watcher: N new streams"
    if new_entries:
        titles = ", ".join(v["title"][:40] for v in new_entries[:3])
        print(
            f"::set-output name=new_streams::{len(new_entries)}\n"
            f"::set-output name=new_titles::{titles}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
