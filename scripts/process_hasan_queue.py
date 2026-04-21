"""Drain the HasanAbi trigger queue and fire the pipeline for each pending stream.

Intended usage on the local (residential PC) side:
  1. Windows Task Scheduler fires this every 15 min (or on logon).
  2. First we `git pull` so we get the latest queue entries that the
     GitHub Actions watcher has committed.
  3. For each pending video_id that is NOT already in the pipeline DB,
     we fire the HasanAbi pipeline. The pipeline's own discovery logic
     still runs — but since we now know a video_id is pending, we seed
     the DB with a job so `discover_latest_archive` will pick it up.
  4. On success, move the entry from `pending` to `processed` in the
     queue file, then commit + push so the GitHub-side state is in sync.

Concurrency: only one invocation of the pipeline runs at a time. If a
pipeline is already running when this fires, we bail fast — the next
Task Scheduler firing will pick up where we left off.

Usage:
  PYTHONPATH=src python scripts/process_hasan_queue.py [--dry-run] [--max N]

Flags:
  --dry-run    Print what WOULD run, don't actually start pipelines
  --max N      Process at most N pending entries (default: 1 — one per
               scheduled firing so we don't stack up hours of work)
  --no-pull    Skip the git pull (useful for local testing)
  --no-push    Skip the git commit/push of queue state after processing
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

QUEUE_PATH = ROOT / "data" / "hasan_trigger_queue.json"
LOCK_PATH = ROOT / "data" / ".hasan_processor.lock"


def load_queue() -> dict:
    if not QUEUE_PATH.exists():
        return {"pending": [], "processed": []}
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("Queue file corrupt: %s — aborting", e)
        raise


def save_queue(q: dict) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, indent=2)
    tmp.replace(QUEUE_PATH)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(ROOT),
        capture_output=True, text=True, check=check, timeout=120,
    )


def acquire_lock() -> bool:
    """Simple PID-file lock. Returns True if we got it, False otherwise."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            pid_text = LOCK_PATH.read_text().strip()
            pid = int(pid_text) if pid_text else 0
        except (OSError, ValueError):
            pid = 0
        if pid and _pid_alive(pid):
            log.info("Another processor (PID %d) is running — exiting.", pid)
            return False
        # Stale lock — nuke it
        log.info("Stale lock (PID %s) — removing", pid)
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass
    LOCK_PATH.write_text(str(os.getpid()))
    return True


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except (OSError, TypeError):
        pass


def _pid_alive(pid: int) -> bool:
    """Best-effort PID liveness check. Windows-friendly."""
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True, timeout=10,
            )
            return str(pid) in r.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def fire_pipeline() -> tuple[int, int]:
    """Run the HasanAbi pipeline once. It picks the newest unprocessed
    video automatically (that's how the existing pipeline works — we
    don't need to hand it a specific video_id).

    Returns (uploaded, errors).
    """
    os.environ.setdefault("PEANUT_SCHEDULER_ENV", "local")

    from peanut_reacts.scheduler.channel_config import load_channels
    from peanut_reacts.scheduler.db import PipelineDB
    from peanut_reacts.scheduler.pipelines import run_channel_pipeline
    from peanut_reacts.scheduler.runner import _load_dotenv

    _load_dotenv()

    cfg = load_channels(ROOT / "channels.yaml")
    ch = next((c for c in cfg.channels if c.id == "hasanabi_archive"), None)
    if not ch:
        log.error("hasanabi_archive channel missing from channels.yaml")
        return 0, 1

    db = PipelineDB(ROOT / "pipeline.db")
    log.info("=== Firing HasanAbi pipeline ===")
    return run_channel_pipeline(ch, cfg.settings, db)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=1)
    ap.add_argument("--no-pull", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    # Step 1: pull the latest watcher state from GitHub
    if not args.no_pull:
        log.info("Pulling latest queue state from GitHub...")
        try:
            r = git("pull", "--rebase", "--autostash")
            log.info(r.stdout.strip() or "pull ok")
        except subprocess.CalledProcessError as e:
            log.warning("git pull failed: %s — continuing with local state",
                        e.stderr[:200] if e.stderr else e)

    queue = load_queue()
    pending = queue.get("pending", [])

    # Pre-filter against pipeline.db — move anything already done/running
    # straight to "processed" without firing the pipeline. This catches
    # the case where the watcher rediscovers streams we already processed
    # (e.g. after a state file wipe) and keeps the queue realistic.
    try:
        from peanut_reacts.scheduler.db import PipelineDB
        db = PipelineDB(ROOT / "pipeline.db")
        done_ids = {
            j["video_id"]
            for j in db.get_channel_jobs("hasanabi_archive", limit=500)
            if j.get("video_id") and j.get("status") in ("done", "running")
        }
    except Exception as e:
        log.warning("Could not load pipeline.db for pre-filter: %s", e)
        done_ids = set()

    if done_ids:
        already_done = [p for p in pending if p.get("id") in done_ids]
        if already_done:
            log.info("Removing %d already-done entries from pending",
                     len(already_done))
            for e in already_done:
                e["completed_at"] = datetime.now(timezone.utc).isoformat()
                e["note"] = "already in pipeline.db (skipped fire)"
            queue["processed"] = (queue.get("processed") or []) + already_done
            queue["pending"] = [p for p in pending if p.get("id") not in done_ids]
            save_queue(queue)
            pending = queue["pending"]

    if not pending:
        log.info("Queue is empty — nothing to do.")
        return 0

    log.info("Queue has %d pending entries", len(pending))
    for entry in pending[: args.max + 3]:    # log first few regardless
        log.info("  %s  %s", entry.get("id"), entry.get("title", "")[:60])

    if args.dry_run:
        log.info("DRY RUN — not firing pipeline. Exiting.")
        return 0

    if not acquire_lock():
        return 0

    try:
        # Step 2: fire the pipeline — it will pick up whichever video is
        # newest and unprocessed (driven by pipeline.db, not the queue).
        # This lines up naturally: the watcher adds to queue, the pipeline
        # sees the same video as newest-unprocessed, processes it.
        uploaded, errors = fire_pipeline()
        log.info("Pipeline done: %d uploaded, %d errors", uploaded, errors)

        if uploaded > 0:
            # Move the corresponding entry from pending → processed. We
            # move the NEWEST pending entry since the pipeline always
            # processes newest-first.
            pending.sort(key=lambda e: e.get("upload_date", ""), reverse=True)
            moved = pending.pop(0)
            moved["completed_at"] = datetime.now(timezone.utc).isoformat()
            moved["uploaded_count"] = uploaded
            queue["processed"] = (queue.get("processed") or []) + [moved]
            queue["pending"] = pending
            save_queue(queue)
            log.info("Moved '%s' to processed.", moved.get("title", "")[:60])

            # Step 3: commit+push so GitHub state matches local
            if not args.no_push:
                try:
                    git("add", str(QUEUE_PATH))
                    git("commit", "-m",
                        f"hasan-processor: completed {moved.get('id')} "
                        f"({uploaded} uploads)")
                    git("push")
                    log.info("Pushed queue update to origin.")
                except subprocess.CalledProcessError as e:
                    log.warning("git push failed: %s", (e.stderr or "")[:200])
        else:
            log.info("No uploads this run — leaving queue unchanged.")

    finally:
        release_lock()

    return 0


if __name__ == "__main__":
    sys.exit(main())
