"""
Progress tracking for processed videos.

Maintains a JSON file that records which videos have been processed,
their status, timestamps, and output paths. Enables:
- Skip already-processed videos in batch mode
- Resume failed runs from the last successful step
- Track history of all generated reaction videos
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class VideoProgress:
    """Progress record for a single video."""
    video_id: str
    title: str = ""
    url: str = ""
    status: str = "pending"  # pending | downloading | processing | completed | failed
    step: str = ""           # current/last step name
    error: str = ""          # error message if failed
    output_path: str = ""
    comments_fetched: int = 0
    reactions_generated: int = 0
    started_at: str = ""
    completed_at: str = ""


class ProgressTracker:
    """Track which videos have been processed."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._data: dict[str, VideoProgress] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for vid, entry in raw.get("videos", {}).items():
                    # Gracefully handle missing fields from older versions
                    safe_entry = {
                        f.name: entry.get(f.name, f.default if f.default is not dataclasses.MISSING else "")
                        for f in dataclasses.fields(VideoProgress)
                    }
                    self._data[vid] = VideoProgress(**safe_entry)
                logger.debug("Loaded progress: %d videos", len(self._data))
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning("Could not load progress file: %s", e)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "videos": {vid: asdict(p) for vid, p in self._data.items()},
        }
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self, video_id: str) -> Optional[VideoProgress]:
        return self._data.get(video_id)

    def is_completed(self, video_id: str) -> bool:
        p = self._data.get(video_id)
        return p is not None and p.status == "completed"

    def start(self, video_id: str, title: str = "", url: str = "") -> VideoProgress:
        """Mark a video as started."""
        p = self._data.get(video_id, VideoProgress(video_id=video_id))
        p.title = title or p.title
        p.url = url or p.url
        p.status = "processing"
        p.step = "started"
        p.error = ""
        p.started_at = datetime.now(timezone.utc).isoformat()
        self._data[video_id] = p
        self._save()
        return p

    def update_step(self, video_id: str, step: str, **kwargs) -> None:
        """Update the current step and any extra fields."""
        p = self._data.get(video_id)
        if not p:
            return
        p.step = step
        for k, v in kwargs.items():
            if hasattr(p, k):
                setattr(p, k, v)
        self._save()

    def complete(self, video_id: str, output_path: str, reactions: int = 0) -> None:
        """Mark a video as successfully completed."""
        p = self._data.get(video_id)
        if not p:
            return
        p.status = "completed"
        p.step = "done"
        p.output_path = output_path
        p.reactions_generated = reactions
        p.completed_at = datetime.now(timezone.utc).isoformat()
        self._save()
        logger.info("Progress: %s marked complete → %s", video_id, output_path)

    def fail(self, video_id: str, error: str, step: str = "") -> None:
        """Mark a video as failed."""
        p = self._data.get(video_id)
        if not p:
            return
        p.status = "failed"
        p.error = error
        if step:
            p.step = step
        self._save()
        logger.warning("Progress: %s failed at %s: %s", video_id, p.step, error)

    def list_all(self) -> list[VideoProgress]:
        return list(self._data.values())

    def list_completed(self) -> list[VideoProgress]:
        return [p for p in self._data.values() if p.status == "completed"]

    def list_pending(self) -> list[VideoProgress]:
        return [p for p in self._data.values() if p.status in ("pending", "failed")]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self._data.values():
            counts[p.status] = counts.get(p.status, 0) + 1
        counts["total"] = len(self._data)
        return counts
