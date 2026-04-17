#!/usr/bin/env python3
"""Download a royalty-free satisfying-clip library from Pexels.

Populates `assets/backgrounds/` with vertical-oriented stock footage that
the vertical story compositor loops behind the talking peanut. Uses the
Pexels Videos API (free tier: 200 requests/hr, 20k/month) with a key set
via the PEXELS_API_KEY env var.

Get a key (2 minutes): https://www.pexels.com/api/

Usage:
    export PEXELS_API_KEY=xxx
    python scripts/download_backgrounds.py
    python scripts/download_backgrounds.py --per-query 5 --queries "slime,paint"

Without a key the pipeline still works — the compositor falls back to a
generated gradient. Backgrounds only improve visuals, not correctness.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("download_bg")

# Curated starter queries. Tuned for satisfying-clip TikTok aesthetics.
DEFAULT_QUERIES = [
    "satisfying slime",
    "paint mixing",
    "kinetic sand cutting",
    "abstract liquid",
    "soap cutting",
    "hydraulic press",
    "marble run",
    "cake pouring",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
BG_DIR = REPO_ROOT / "assets" / "backgrounds"
METADATA_PATH = BG_DIR / "_metadata.json"

PEXELS_API = "https://api.pexels.com/videos/search"


def _load_metadata() -> dict:
    if METADATA_PATH.exists():
        return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "clips": {}}


def _save_metadata(metadata: dict) -> None:
    BG_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )


def _search_pexels(api_key: str, query: str, per_page: int) -> list[dict]:
    """Search Pexels, prefer portrait/square orientation + 1080p+."""
    # Pexels supports `orientation=portrait` and `size=medium|large`.
    # We ask for portrait first (9:16 is our target) and fall back if empty.
    for orientation in ("portrait", "square"):
        url = (
            f"{PEXELS_API}?query={urllib.parse.quote(query)}"
            f"&per_page={per_page}&orientation={orientation}&size=large"
        )
        req = urllib.request.Request(url, headers={"Authorization": api_key})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log.warning("rate-limited on %r; waiting 10s", query)
                time.sleep(10)
                continue
            raise
        videos = data.get("videos", [])
        if videos:
            return videos
    return []


def _best_video_file(video: dict) -> dict | None:
    """Pick the best-fit video file from a Pexels response video.

    Prefer 1080p MP4 in the target orientation, skip anything 4K (too big).
    """
    files = video.get("video_files", [])
    # Sort by (is_mp4_desc, height_asc) — we want smallest that's ≥1080
    def _score(f: dict) -> tuple:
        is_mp4 = 1 if f.get("file_type") == "video/mp4" else 0
        h = f.get("height") or 0
        # Prefer 1080p (or closest-above), penalize 4K for size
        target_dist = 0 if h == 1080 else abs(h - 1080) + (1000 if h > 1440 else 0)
        return (-is_mp4, target_dist)

    files = sorted(files, key=_score)
    return files[0] if files else None


def _download(url: str, dest: Path) -> bool:
    """Download a URL to disk, returning True on success."""
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            dest.write_bytes(r.read())
        return True
    except Exception as e:
        log.error("download failed (%s): %s", dest.name, e)
        if dest.exists():
            dest.unlink(missing_ok=True)
        return False


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--queries", default=",".join(DEFAULT_QUERIES),
                   help="Comma-separated Pexels search terms")
    p.add_argument("--per-query", type=int, default=3,
                   help="Clips to download per query (default: 3)")
    p.add_argument("--max-duration", type=float, default=60.0,
                   help="Skip clips longer than this (seconds)")
    args = p.parse_args()

    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        print(
            "PEXELS_API_KEY not set. Get a free key at https://www.pexels.com/api/\n"
            "then:  export PEXELS_API_KEY=xxx && python scripts/download_backgrounds.py\n\n"
            "Skipping download — the compositor will use a generated gradient fallback.",
            file=sys.stderr,
        )
        return 0  # not an error; compositor works without backgrounds

    BG_DIR.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata()
    known_ids = set(metadata["clips"].keys())

    queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    log.info("Downloading up to %d clips from %d queries into %s",
             args.per_query * len(queries), len(queries), BG_DIR)

    n_new = 0
    n_skipped = 0
    n_failed = 0

    for query in queries:
        log.info("searching: %r", query)
        try:
            videos = _search_pexels(api_key, query, args.per_query * 2)  # oversample
        except Exception as e:
            log.error("search %r failed: %s", query, e)
            continue

        kept = 0
        for v in videos:
            if kept >= args.per_query:
                break
            vid_id = str(v.get("id"))
            if vid_id in known_ids:
                n_skipped += 1
                continue
            duration = v.get("duration", 0)
            if duration > args.max_duration:
                continue
            vf = _best_video_file(v)
            if not vf:
                continue

            filename = f"bg_{vid_id}.mp4"
            dest = BG_DIR / filename
            log.info("  + %s (%ds, %dx%d, %.1fMB est)", filename,
                     duration, vf.get("width", 0), vf.get("height", 0),
                     (vf.get("file_size") or 0) / 1e6)

            if not _download(vf["link"], dest):
                n_failed += 1
                continue

            metadata["clips"][vid_id] = {
                "filename": filename,
                "query": query,
                "duration": duration,
                "width": vf.get("width"),
                "height": vf.get("height"),
                "pexels_url": v.get("url"),
                "photographer": v.get("user", {}).get("name"),
                "photographer_url": v.get("user", {}).get("url"),
            }
            n_new += 1
            kept += 1
            time.sleep(0.5)  # polite pacing

        _save_metadata(metadata)  # checkpoint after each query

    log.info("Done: %d new, %d already-had, %d failed. Library: %d clips in %s",
             n_new, n_skipped, n_failed, len(metadata["clips"]), BG_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
