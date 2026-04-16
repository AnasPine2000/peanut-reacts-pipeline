"""Main entry point for the autonomous pipeline scheduler.

Usage:
    python -m peanut_reacts.scheduler.runner                # Start scheduler daemon
    python -m peanut_reacts.scheduler.runner --dry-run      # Show config + scheduled jobs
    python -m peanut_reacts.scheduler.runner --run-once uk_clips  # Run one channel now
    python -m peanut_reacts.scheduler.runner --list         # List all channels
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from peanut_reacts.scheduler.channel_config import load_channels
from peanut_reacts.scheduler.db import PipelineDB
from peanut_reacts.scheduler.engine import PipelineEngine


def setup_logging(log_dir: str = "logs"):
    Path(log_dir).mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(f"{log_dir}/scheduler.log", encoding="utf-8"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(description="Autonomous YouTube Pipeline Scheduler")
    parser.add_argument("--config", default="channels.yaml", help="Path to channels.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Show config and exit")
    parser.add_argument("--run-once", metavar="CHANNEL_ID", help="Run one channel immediately")
    parser.add_argument("--list", action="store_true", help="List all configured channels")
    args = parser.parse_args()

    config = load_channels(Path(args.config))
    setup_logging(config.settings.log_dir)
    log = logging.getLogger("runner")

    db = PipelineDB(config.settings.db_path)
    engine = PipelineEngine(config, db)

    if args.list:
        print(f"\n{'ID':<20} {'Name':<30} {'Type':<15} {'Schedule':<20}")
        print("-" * 85)
        for ch in config.channels:
            print(f"{ch.id:<20} {ch.name:<30} {ch.source_type:<15} {ch.schedule:<20}")
        return

    if args.dry_run:
        engine.setup()
        print("\n=== DRY RUN ===")
        print(f"Channels: {len(config.channels)}")
        print(f"Database: {config.settings.db_path}")
        print(f"Discord: {'configured' if config.settings.discord_webhook_url else 'not set'}")
        print(f"\nScheduled jobs:")
        for job in engine.list_jobs():
            print(f"  {job['name']:<30} Next: {job['next_run']}")
        engine.stop()
        return

    if args.run_once:
        log.info("Running single channel: %s", args.run_once)
        engine.run_once(args.run_once)
        db.close()
        return

    # Start the full scheduler daemon
    engine.start()

    def shutdown(sig, frame):
        log.info("Shutdown signal received")
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep main thread alive
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        engine.stop()


if __name__ == "__main__":
    main()
