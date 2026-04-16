"""Discord webhook notifications for pipeline events."""
from __future__ import annotations

import logging
from datetime import datetime

import httpx

log = logging.getLogger(__name__)

COLORS = {"success": 0x2ECC71, "error": 0xE74C3C, "info": 0x3498DB, "warning": 0xF39C12}


def send_notification(
    webhook_url: str,
    title: str,
    message: str,
    color: str = "info",
    url: str = "",
    fields: list[dict] | None = None,
) -> bool:
    if not webhook_url:
        log.debug("No Discord webhook configured, skipping notification")
        return False

    embed = {
        "title": title,
        "description": message,
        "color": COLORS.get(color, COLORS["info"]),
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": "Peanut Pipeline"},
    }
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = fields

    try:
        resp = httpx.post(webhook_url, json={"embeds": [embed]}, timeout=10)
        resp.raise_for_status()
        log.info("Discord notification sent: %s", title)
        return True
    except Exception as e:
        log.warning("Discord notification failed: %s", e)
        return False


def notify_job_complete(webhook_url: str, channel: str, title: str, youtube_url: str = ""):
    send_notification(
        webhook_url,
        f"Upload Complete: {channel}",
        f"**{title}**\n{youtube_url}" if youtube_url else f"**{title}**",
        color="success",
        url=youtube_url,
    )


def notify_job_failed(webhook_url: str, channel: str, error: str, step: str = ""):
    send_notification(
        webhook_url,
        f"Job Failed: {channel}",
        f"**Step:** {step}\n**Error:** {error[:500]}",
        color="error",
    )


def notify_daily_summary(webhook_url: str, stats: dict):
    send_notification(
        webhook_url,
        "Daily Pipeline Summary",
        f"**Completed:** {stats.get('done', 0)}\n"
        f"**Failed:** {stats.get('failed', 0)}\n"
        f"**Running:** {stats.get('running', 0)}",
        color="info",
        fields=[
            {"name": "Total Jobs", "value": str(stats.get("total", 0)), "inline": True},
        ],
    )
