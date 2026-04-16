"""Dashboard sync — fetches all live data and pushes to HF Space every 5 minutes.

Runs as a detached background process. Collects:
- YouTube channel stats (subs, views, latest videos)
- Pipeline job history from SQLite
- Learning insights + recommendations
- Shorts queue status
- Experiment tracking
- Active build status from log files

Pushes everything to Hugging Face Space as status.json + learning JSON.
"""
from __future__ import annotations

import io
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).resolve().parents[1] / "logs" / "dashboard_sync.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("dashboard_sync")

PROJECT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = PROJECT / "dashboard"
HF_REPO = "Ananas4444/peanut-reacts-dashboard"
INTERVAL_SECONDS = 300  # 5 minutes


def collect_status() -> dict:
    """Collect all live data into a single status dict."""
    from peanut_reacts.scheduler.channel_config import load_channels
    from peanut_reacts.scheduler.db import PipelineDB
    from peanut_reacts.scheduler.status_exporter import build_status

    config = load_channels(PROJECT / "channels.yaml")
    db = PipelineDB(str(PROJECT / "pipeline.db"))

    status = build_status(
        pipeline_config=config,
        db=db,
        fetch_youtube_stats=True,
        livestream_url="https://youtube.com/watch?v=YcY8kRw-8_U",
        hf_space_url=f"https://huggingface.co/spaces/{HF_REPO}",
        output_path=DASHBOARD_DIR / "status.json",
    )

    db.close()
    return status


def collect_shorts_stats() -> dict:
    """Collect Shorts queue statistics."""
    try:
        from peanut_reacts.clips.shorts_queue import ShortsQueue
        q = ShortsQueue(str(PROJECT / "shorts_queue.db"))
        stats = q.get_queue_stats()
        recent = q.get_recent(limit=10)
        q.close()
        return {"stats": stats, "recent": recent}
    except Exception as e:
        log.debug("Shorts queue not available: %s", e)
        return {"stats": {}, "recent": []}


def collect_learning() -> dict:
    """Collect learning insights if available."""
    learning_file = DASHBOARD_DIR / "learning_uk_clips.json"
    if learning_file.exists():
        try:
            return json.loads(learning_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def collect_active_builds() -> list[dict]:
    """Check log files for active builds."""
    builds = []
    log_dir = PROJECT / "logs"
    if not log_dir.exists():
        return builds

    for log_file in log_dir.glob("*.log"):
        try:
            # Read last 5 lines
            lines = log_file.read_text(encoding="utf-8", errors="replace").strip().split("\n")
            if not lines:
                continue
            last_line = lines[-1]
            # Check if it's recent (within last 10 min)
            if "2026-04" in last_line:  # Has a timestamp
                timestamp_str = last_line[:19]
                try:
                    ts = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    age_minutes = (datetime.now() - ts).total_seconds() / 60
                    if age_minutes < 15:
                        # Extract what step it's on
                        step = "running"
                        for keyword in ["Converting", "Concatenat", "reactions", "TTS", "Audio batch", "Facecam", "Composite", "Upload", "Downloading", "Segment"]:
                            if keyword.lower() in last_line.lower():
                                step = keyword
                                break
                        builds.append({
                            "name": log_file.stem,
                            "last_activity": timestamp_str,
                            "age_minutes": round(age_minutes, 1),
                            "current_step": step,
                            "last_line": last_line[20:120].strip(),
                        })
                except Exception:
                    pass
        except Exception:
            pass

    return builds


def collect_experiments() -> dict:
    """Collect experiment tracking data."""
    try:
        from peanut_reacts.learning.experiments import summarize_experiments, get_recent_experiments
        summary = summarize_experiments(str(PROJECT / "learning.db"))
        experiments = get_recent_experiments(str(PROJECT / "learning.db"), limit=20)
        return {"summary": summary, "experiments": experiments}
    except Exception as e:
        log.debug("Experiments not available: %s", e)
        return {"summary": {}, "experiments": []}


def push_to_hf(files: dict[str, Path]) -> bool:
    """Push multiple files to HF Space."""
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        for remote_name, local_path in files.items():
            if local_path.exists():
                api.upload_file(
                    path_or_fileobj=str(local_path),
                    path_in_repo=remote_name,
                    repo_id=HF_REPO,
                    repo_type="space",
                )
        log.info("Pushed %d files to HF Space", len(files))
        return True
    except Exception as e:
        log.warning("HF push failed: %s", e)
        return False


def run_sync_cycle():
    """Run one full sync cycle."""
    log.info("=" * 40)
    log.info("Sync cycle starting...")

    DASHBOARD_DIR.mkdir(exist_ok=True)

    # 1. Main status (channels + pipeline jobs + YouTube stats)
    try:
        status = collect_status()
        log.info("Status: %d channels, %d jobs", len(status.get("channels", [])), status.get("stats", {}).get("total", 0))
    except Exception as e:
        log.error("Status collection failed: %s", e)
        status = {}

    # 2. Shorts queue
    try:
        shorts = collect_shorts_stats()
        shorts_path = DASHBOARD_DIR / "shorts_stats.json"
        shorts_path.write_text(json.dumps(shorts, indent=2, default=str), encoding="utf-8")
        log.info("Shorts: %s", shorts.get("stats", {}))
    except Exception as e:
        log.warning("Shorts collection failed: %s", e)

    # 3. Active builds
    try:
        builds = collect_active_builds()
        if builds:
            # Inject into status
            if status:
                status["active_builds"] = builds
                (DASHBOARD_DIR / "status.json").write_text(
                    json.dumps(status, indent=2, default=str), encoding="utf-8"
                )
            log.info("Active builds: %d", len(builds))
            for b in builds:
                log.info("  %s: %s (%.0f min ago)", b["name"], b["current_step"], b["age_minutes"])
    except Exception as e:
        log.warning("Build status failed: %s", e)

    # 4. Experiments
    try:
        exp = collect_experiments()
        exp_path = DASHBOARD_DIR / "experiments.json"
        exp_path.write_text(json.dumps(exp, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        log.debug("Experiments: %s", e)

    # 5. Push to HF Space
    files_to_push = {
        "status.json": DASHBOARD_DIR / "status.json",
        "shorts_stats.json": DASHBOARD_DIR / "shorts_stats.json",
        "experiments.json": DASHBOARD_DIR / "experiments.json",
    }
    # Also push learning data if it exists
    learning_file = DASHBOARD_DIR / "learning_uk_clips.json"
    if learning_file.exists():
        files_to_push["learning_uk_clips.json"] = learning_file

    push_to_hf(files_to_push)
    log.info("Sync cycle complete.")


def main():
    log.info("Dashboard sync started (interval: %ds)", INTERVAL_SECONDS)
    (PROJECT / "logs").mkdir(exist_ok=True)

    cycle = 0
    while True:
        try:
            run_sync_cycle()
            cycle += 1
        except Exception as e:
            log.error("Sync cycle %d failed: %s", cycle, e)

        log.info("Next sync in %d seconds...", INTERVAL_SECONDS)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
