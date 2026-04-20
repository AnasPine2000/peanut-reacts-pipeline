"""Live pipeline status tracker — surfaces what's running RIGHT NOW.

Writes a small JSON file (data/pipeline_status.json) that the dashboard
refresh reads and embeds into docs/data.json. The dashboard HTML then
shows a "Live Pipeline" card with:

  - Active run (channel, format, current step, elapsed time, % progress)
  - Recent uploads (last 10 with timestamps + YouTube URLs)

Design goals:
  - Zero deps beyond stdlib (must not break imports even if called from
    a minimal env)
  - Atomic writes (temp + rename) so a concurrent dashboard refresh
    never reads a half-written JSON
  - Graceful — any write failure is logged, never raised (pipeline must
    not crash because telemetry is broken)
  - File-based, not DB — easier to read from any language / script /
    dashboard build
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATUS_PATH = PROJECT_ROOT / "data" / "pipeline_status.json"

# Cap recent uploads list so the JSON stays small
MAX_RECENT = 10


@dataclass
class ActiveRun:
    """Snapshot of what's happening in-flight right now."""
    run_id: str
    channel_id: str
    channel_name: str = ""
    format: str = ""                    # "live_reaction_compilation", "tntl", etc.
    source_title: str = ""              # e.g. "3 Sidemen AU games concatenated"
    started_at: str = ""                # ISO 8601 UTC
    current_step: str = ""              # human label e.g. "Compositing final episode"
    step_progress: str = ""             # sub-progress e.g. "clip 45 / 75"
    progress_percent: int = 0           # 0-100
    elapsed_seconds: int = 0
    steps_completed: list[str] = field(default_factory=list)
    steps_remaining: list[str] = field(default_factory=list)


@dataclass
class RecentUpload:
    channel_id: str
    title: str
    youtube_url: str
    uploaded_at: str
    duration_seconds: Optional[float] = None


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically — temp file then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent),
        prefix=".pipeline_status_", suffix=".tmp", delete=False,
    )
    try:
        json.dump(payload, tmp, indent=2, default=str)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, str(path))


def _load_state() -> dict:
    """Load existing state, return empty on any failure."""
    try:
        if STATUS_PATH.exists():
            with open(STATUS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("[STATUS] Failed to load existing state: %s — starting fresh", e)
    return {"active": None, "recent": [], "updated_at": None}


def _save_state(state: dict) -> None:
    """Persist state, swallow failures."""
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _atomic_write_json(STATUS_PATH, state)
    except Exception as e:
        log.warning("[STATUS] write failed: %s", e)


# ── Public API ─────────────────────────────────────────────────────────────

class RunTracker:
    """Context-managed tracker for a single pipeline run.

    Usage:
        with RunTracker(channel_id="uk_clips", format="live_reaction_compilation",
                        steps=["download","concat","verdicts","tts","facecam",
                               "composite","upload"]) as run:
            run.begin_step("download", "Downloading 3 videos")
            ...
            run.begin_step("concat")
            run.update_progress(45, sub="45 min of 88 min")
            ...
            run.finish(youtube_url=..., title=...)
    """

    def __init__(
        self,
        channel_id: str,
        channel_name: str = "",
        format: str = "",
        source_title: str = "",
        steps: Optional[list[str]] = None,
    ):
        run_id = f"{channel_id}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self._run = ActiveRun(
            run_id=run_id,
            channel_id=channel_id,
            channel_name=channel_name,
            format=format,
            source_title=source_title,
            started_at=datetime.now(timezone.utc).isoformat(),
            steps_remaining=list(steps or []),
        )
        self._start_ts = time.time()
        self._current_step_name = ""

    # Enter/exit let this be used as a `with` block for guaranteed cleanup
    def __enter__(self):
        self._write()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # If finish() wasn't called, record as crashed
        state = _load_state()
        if state.get("active") and state["active"].get("run_id") == self._run.run_id:
            self.finish(success=False, error=f"{exc_type.__name__}: {exc_val}" if exc_type else "incomplete")
        return False  # don't suppress exceptions

    def begin_step(self, step_name: str, human_label: str = "", progress_percent: Optional[int] = None) -> None:
        """Mark the start of a named phase. Moves it out of `remaining`."""
        self._current_step_name = step_name
        self._run.current_step = human_label or step_name
        self._run.step_progress = ""
        if progress_percent is not None:
            self._run.progress_percent = max(0, min(100, progress_percent))
        # Remove from remaining (if present)
        if step_name in self._run.steps_remaining:
            self._run.steps_remaining.remove(step_name)
        self._write()

    def update_progress(self, percent: Optional[int] = None, sub: str = "") -> None:
        """Update % or sub-progress string without changing the step."""
        if percent is not None:
            self._run.progress_percent = max(0, min(100, int(percent)))
        if sub:
            self._run.step_progress = sub
        self._write()

    def complete_step(self, step_name: Optional[str] = None) -> None:
        """Mark a step done (moves into `completed`)."""
        name = step_name or self._current_step_name
        if name and name not in self._run.steps_completed:
            self._run.steps_completed.append(name)
        self._write()

    def finish(
        self,
        success: bool = True,
        youtube_url: Optional[str] = None,
        title: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Mark run complete. Archives into `recent` and clears active."""
        state = _load_state()

        if success:
            self._run.current_step = "done"
            self._run.progress_percent = 100
        else:
            self._run.current_step = "failed"

        # Archive into recent list if it was a real upload
        if success and youtube_url:
            upload = RecentUpload(
                channel_id=self._run.channel_id,
                title=title or self._run.source_title or "(untitled)",
                youtube_url=youtube_url,
                uploaded_at=datetime.now(timezone.utc).isoformat(),
                duration_seconds=time.time() - self._start_ts,
            )
            recent = state.get("recent") or []
            recent.insert(0, asdict(upload))
            state["recent"] = recent[:MAX_RECENT]

        state["active"] = None
        if error:
            state["last_error"] = {
                "run_id": self._run.run_id,
                "channel_id": self._run.channel_id,
                "error": error[:500],
                "at": datetime.now(timezone.utc).isoformat(),
            }
        _save_state(state)
        log.info("[STATUS] finish run %s (success=%s)", self._run.run_id, success)

    # Internal: write the active-run snapshot
    def _write(self) -> None:
        self._run.elapsed_seconds = int(time.time() - self._start_ts)
        state = _load_state()
        state["active"] = asdict(self._run)
        _save_state(state)


# ── Helpers for callers that don't want a `with` block ─────────────────────

def read_status() -> dict:
    """Read the current status dict (for dashboard builders)."""
    return _load_state()


def clear_status() -> None:
    """Wipe the status file (admin/debug). Keeps recent uploads."""
    state = _load_state()
    state["active"] = None
    _save_state(state)
