"""APScheduler-based autonomous pipeline engine."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .channel_config import PipelineConfig, ChannelConfig, GlobalSettings, load_channels
from .db import PipelineDB
from .notify import notify_job_complete, notify_job_failed, notify_daily_summary
from .pipelines import run_channel_pipeline

log = logging.getLogger(__name__)


class PipelineEngine:
    """Manages scheduled pipeline jobs for all channels."""

    def __init__(self, config: PipelineConfig, db: PipelineDB):
        self.config = config
        self.db = db
        self.scheduler = BackgroundScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
        )
        self._webhook = config.settings.discord_webhook_url

    def setup(self):
        """Register all channel jobs with the scheduler.

        Channels whose `execution_env` doesn't match this box's
        PEANUT_SCHEDULER_ENV are registered in the DB but skipped from
        the live scheduler. That way the same channels.yaml can run on
        both the VPS ("vps") and the user's residential PC ("local")
        without double-firing — each side only fires its own.
        """
        import os
        env = os.environ.get("PEANUT_SCHEDULER_ENV", "vps").strip().lower()
        log.info("Scheduler env: %s — will only fire channels with execution_env=%s", env, env)

        for ch in self.config.channels:
            self.db.upsert_channel(ch.id, ch.name)

            if not getattr(ch, "enabled", True):
                log.info("Channel [%s] enabled=false, skipping", ch.id)
                continue

            if not ch.schedule:
                log.info("Channel [%s] has no schedule, skipping", ch.id)
                continue

            if ch.execution_env != env:
                log.info("Channel [%s] execution_env=%s (this box: %s) — skipping",
                         ch.id, ch.execution_env, env)
                continue

            try:
                trigger = CronTrigger.from_crontab(ch.schedule)
                self.scheduler.add_job(
                    self._run_channel,
                    trigger=trigger,
                    args=[ch],
                    id=f"pipeline_{ch.id}",
                    name=f"Pipeline: {ch.name}",
                    replace_existing=True,
                )
                log.info("Scheduled [%s]: %s", ch.id, ch.schedule)
            except Exception as e:
                log.error("Failed to schedule [%s]: %s", ch.id, e)

        # Daily summary at 11 PM
        self.scheduler.add_job(
            self._daily_summary,
            CronTrigger(hour=23, minute=0),
            id="daily_summary",
            name="Daily Summary",
            replace_existing=True,
        )

        # Status export every 5 minutes
        interval = self.config.settings.status_push_interval_minutes
        self.scheduler.add_job(
            self._export_status,
            "interval",
            minutes=interval,
            id="status_export",
            name="Status Export",
            replace_existing=True,
        )

        # Learning loop runs daily at 3 AM (feedback from yesterday's performance)
        self.scheduler.add_job(
            self._run_learning_loop,
            CronTrigger(hour=3, minute=17),
            id="learning_loop",
            name="Daily Learning Loop",
            replace_existing=True,
        )

        # Shorts posting — 5 times per day at optimal times
        for hour in [7, 12, 18, 21, 23]:
            self.scheduler.add_job(
                self._post_shorts,
                CronTrigger(hour=hour, minute=3),
                id=f"shorts_post_{hour:02d}",
                name=f"Post Shorts ({hour:02d}:03)",
                replace_existing=True,
            )

    def _run_channel(self, channel: ChannelConfig):
        """Execute the pipeline for a single channel."""
        log.info("Starting scheduled run for [%s]", channel.id)
        try:
            videos, errors = run_channel_pipeline(channel, self.config.settings, self.db)

            if videos > 0:
                notify_job_complete(
                    self._webhook, channel.name,
                    f"Processed {videos} videos ({errors} errors)",
                )
            if errors > 0:
                notify_job_failed(
                    self._webhook, channel.name,
                    f"{errors} errors during pipeline run",
                )

        except Exception as e:
            log.error("Channel [%s] pipeline crashed: %s", channel.id, e)
            notify_job_failed(self._webhook, channel.name, str(e))

    def _daily_summary(self):
        stats = self.db.get_stats()
        notify_daily_summary(self._webhook, stats)
        log.info("Daily summary: %s", stats)

    def _export_status(self):
        """Export pipeline status to dashboard/status.json."""
        import json
        status = self.db.export_status()
        status_path = Path(__file__).resolve().parents[3] / "dashboard" / "status.json"
        status_path.parent.mkdir(exist_ok=True)
        status_path.write_text(json.dumps(status, indent=2, default=str))

    def _post_shorts(self):
        """Post due Shorts from the queue to YouTube + TikTok."""
        log.info("Posting scheduled Shorts...")
        try:
            from peanut_reacts.scheduler.shorts_pipeline import run_shorts_posting_for_all
            results = run_shorts_posting_for_all(self.config, self.config.settings)
            total_yt = sum(r.get("posted_yt", 0) for r in results.values())
            total_tt = sum(r.get("posted_tt", 0) for r in results.values())
            total_err = sum(r.get("errors", 0) for r in results.values())
            if total_yt + total_tt > 0:
                log.info("Shorts posted: YT=%d, TT=%d, errors=%d", total_yt, total_tt, total_err)
            else:
                log.debug("No Shorts due for posting right now")
        except Exception as e:
            log.error("Shorts posting failed: %s", e)

    def _run_learning_loop(self):
        """Run the learning feedback loop for all channels with credentials."""
        log.info("Running daily learning loop for all channels...")
        try:
            from peanut_reacts.learning.loop import run_learning_for_all_channels
            dashboard_dir = Path(__file__).resolve().parents[3] / "dashboard"
            results = run_learning_for_all_channels(
                self.config, self.config.settings,
                learning_db_path="learning.db",
                dashboard_dir=dashboard_dir,
            )
            total_insights = sum(r.get("insights_generated", 0) for r in results.values())
            total_recs = sum(r.get("recommendations_generated", 0) for r in results.values())
            log.info("Learning loop complete: %d insights, %d recommendations",
                     total_insights, total_recs)

            # Notify
            if self._webhook and total_insights > 0:
                from .notify import send_notification
                send_notification(
                    self._webhook,
                    "Learning Loop Complete",
                    f"Analyzed {sum(r.get('videos_tracked', 0) for r in results.values())} videos, "
                    f"generated {total_insights} insights and {total_recs} recommendations.",
                    color="info",
                )
        except Exception as e:
            log.error("Learning loop failed: %s", e)

    def run_once(self, channel_id: str):
        """Run a single channel pipeline immediately (for testing)."""
        from .channel_config import get_channel
        ch = get_channel(self.config, channel_id)
        return self._run_channel(ch)

    def start(self):
        """Start the scheduler. Blocks until interrupted."""
        self.setup()
        self.scheduler.start()
        log.info("=" * 60)
        log.info("Pipeline scheduler started. %d channels registered.", len(self.config.channels))
        log.info("Press Ctrl+C to stop.")
        log.info("=" * 60)

        # Print scheduled jobs
        for job in self.scheduler.get_jobs():
            log.info("  Job: %s | Next: %s", job.name, job.next_run_time)

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
        self.db.close()
        log.info("Scheduler stopped.")

    def list_jobs(self) -> list[dict]:
        jobs = []
        for j in self.scheduler.get_jobs():
            nrt = getattr(j, "next_run_time", None)
            jobs.append({"id": j.id, "name": j.name, "next_run": str(nrt or "pending"), "trigger": str(j.trigger)})
        return jobs
