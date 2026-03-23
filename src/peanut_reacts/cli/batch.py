#!/usr/bin/env python3
"""
Batch reaction video generator.

Processes all videos from a YouTube playlist or channel, skipping
already-completed videos and retrying failures.

Usage:
    peanut-batch "https://www.youtube.com/playlist?list=PLID" --max-videos 20
    peanut-batch "https://www.youtube.com/@ChannelName" --max-videos 10
    peanut-batch videos.txt  (one URL per line)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from peanut_reacts.core.config import load_config
from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.core.progress import ProgressTracker


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Batch-generate peanut reaction videos for a playlist, channel, or URL list.",
    )
    p.add_argument("source", help="YouTube playlist/channel URL, or a .txt file with one URL per line")
    p.add_argument("--max-videos", type=int, default=0, help="Max videos to process (0 = all)")
    p.add_argument("--skip-existing", action="store_true", default=True,
                   help="Skip already-completed videos (default: true)")
    p.add_argument("--retry-failed", action="store_true", help="Retry previously failed videos")
    p.add_argument("--delay", type=int, default=5, help="Seconds between videos (default: 5)")

    # Pass-through to peanut-react
    p.add_argument("--provider", default="", help="LLM provider")
    p.add_argument("--max-reactions", type=int, default=0)
    p.add_argument("--max-comments", type=int, default=0)
    p.add_argument("--facecam-scale", type=float, default=0)
    p.add_argument("--work-dir", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def _extract_video_urls_from_playlist(source_url: str, max_videos: int, log) -> list[dict]:
    """Use yt-dlp to extract video URLs from a playlist or channel (no download)."""
    import yt_dlp

    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "ignoreerrors": True,
    }

    log.info("Extracting video list from %s ...", source_url)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(source_url, download=False)

    if not info:
        return []

    entries = info.get("entries", [])
    if not entries:
        # Single video URL
        return [{"id": info.get("id", ""), "title": info.get("title", ""), "url": source_url}]

    videos = []
    for entry in entries:
        if not entry:
            continue
        vid = entry.get("id", "")
        title = entry.get("title", "")
        url = entry.get("url") or f"https://www.youtube.com/watch?v={vid}"
        videos.append({"id": vid, "title": title, "url": url})

    if max_videos > 0:
        videos = videos[:max_videos]

    log.info("Found %d video(s)", len(videos))
    return videos


def _load_urls_from_file(path: Path, max_videos: int, log) -> list[dict]:
    """Load URLs from a text file (one per line)."""
    from peanut_reacts.download.youtube_comments import extract_video_id

    lines = path.read_text(encoding="utf-8").splitlines()
    videos = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            vid = extract_video_id(line)
        except ValueError:
            vid = line[:20]
        videos.append({"id": vid, "title": "", "url": line})

    if max_videos > 0:
        videos = videos[:max_videos]

    log.info("Loaded %d URL(s) from %s", len(videos), path.name)
    return videos


def _run_single_video(url: str, work_dir: Path, extra_args: list[str], log) -> int:
    """Run peanut-react for a single video as a subprocess."""
    import subprocess

    cmd = [
        sys.executable, "-m", "peanut_reacts.cli.react",
        url,
        "--work-dir", str(work_dir),
    ] + extra_args

    log.info("Running: peanut-react %s", url)
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=1800,  # 30-minute timeout per video
    )

    if result.returncode == 0:
        log.info("  ✓ Success")
    else:
        # Show last few lines of output for debugging
        last_lines = result.stdout.strip().splitlines()[-5:]
        for line in last_lines:
            log.error("  │ %s", line)
        log.error("  ✗ Failed (exit %d)", result.returncode)

    return result.returncode


def main() -> int:
    args = _parse_args()
    log = build_logger("peanut_batch", args.verbose)
    cfg = load_config()

    source = args.source.strip()
    work_dir = Path(args.work_dir or cfg.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    tracker = ProgressTracker(work_dir / cfg.progress_file)

    # ── Resolve video list ───────────────────────────────────────────
    source_path = Path(source)
    if source_path.exists() and source_path.suffix in (".txt", ".csv"):
        videos = _load_urls_from_file(source_path, args.max_videos, log)
    else:
        videos = _extract_video_urls_from_playlist(source, args.max_videos, log)

    if not videos:
        log.error("No videos found.")
        return 1

    # ── Filter based on progress ─────────────────────────────────────
    to_process = []
    skipped = 0
    for v in videos:
        vid = v["id"]
        if tracker.is_completed(vid) and not args.retry_failed:
            skipped += 1
            continue
        prev = tracker.get(vid)
        if prev and prev.status == "failed" and not args.retry_failed:
            skipped += 1
            continue
        to_process.append(v)

    if skipped:
        log.info("Skipping %d already-processed video(s)", skipped)
    log.info("Will process %d video(s)", len(to_process))

    if not to_process:
        log.info("Nothing to process. Use --retry-failed to reprocess failures.")
        return 0

    # ── Build extra args for peanut-react ────────────────────────────
    extra_args: list[str] = []
    if args.verbose:
        extra_args.append("-v")
    if args.provider:
        extra_args.extend(["--provider", args.provider])
    if args.max_reactions:
        extra_args.extend(["--max-reactions", str(args.max_reactions)])
    if args.max_comments:
        extra_args.extend(["--max-comments", str(args.max_comments)])
    if args.facecam_scale:
        extra_args.extend(["--facecam-scale", str(args.facecam_scale)])

    # ── Process each video ───────────────────────────────────────────
    results = {"success": 0, "failed": 0, "skipped": skipped}

    for i, video in enumerate(to_process, 1):
        log.info("━" * 60)
        log.info("[%d/%d] %s", i, len(to_process), video["title"] or video["url"])

        video_work = work_dir / f"video_{video['id']}"

        try:
            rc = _run_single_video(video["url"], video_work, extra_args, log)
            if rc == 0:
                results["success"] += 1
            else:
                results["failed"] += 1
        except Exception as e:
            log.error("  ✗ Exception: %s", e)
            results["failed"] += 1

        # Delay between videos to avoid rate limiting
        if i < len(to_process) and args.delay > 0:
            log.info("Waiting %ds before next video ...", args.delay)
            time.sleep(args.delay)

    # ── Summary ──────────────────────────────────────────────────────
    log.info("━" * 60)
    log.info("BATCH COMPLETE")
    log.info("  ✓ Success:  %d", results["success"])
    log.info("  ✗ Failed:   %d", results["failed"])
    log.info("  ⊘ Skipped:  %d", results["skipped"])
    log.info("  Total:      %d", sum(results.values()))

    progress = tracker.summary()
    log.info("  Progress:   %s", progress)

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
