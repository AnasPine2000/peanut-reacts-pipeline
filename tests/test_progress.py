"""Tests for peanut_reacts.core.progress."""

from __future__ import annotations

import json
from pathlib import Path

from peanut_reacts.core.progress import ProgressTracker, VideoProgress


class TestProgressTracker:
    def test_start_and_complete(self, tmp_path: Path):
        tracker = ProgressTracker(tmp_path / "progress.json")
        tracker.start("vid1", title="Test Video", url="https://example.com")
        assert not tracker.is_completed("vid1")

        tracker.complete("vid1", output_path="/out/video.mp4", reactions=5)
        assert tracker.is_completed("vid1")

        p = tracker.get("vid1")
        assert p is not None
        assert p.output_path == "/out/video.mp4"
        assert p.reactions_generated == 5

    def test_fail(self, tmp_path: Path):
        tracker = ProgressTracker(tmp_path / "progress.json")
        tracker.start("vid2")
        tracker.fail("vid2", "TTS error", step="tts")

        p = tracker.get("vid2")
        assert p is not None
        assert p.status == "failed"
        assert p.error == "TTS error"
        assert p.step == "tts"

    def test_persistence(self, tmp_path: Path):
        path = tmp_path / "progress.json"
        t1 = ProgressTracker(path)
        t1.start("vid3", title="Persistent")
        t1.complete("vid3", "/out.mp4")

        # Reload from disk
        t2 = ProgressTracker(path)
        assert t2.is_completed("vid3")
        assert t2.get("vid3").title == "Persistent"

    def test_summary(self, tmp_path: Path):
        tracker = ProgressTracker(tmp_path / "progress.json")
        tracker.start("a")
        tracker.complete("a", "/a.mp4")
        tracker.start("b")
        tracker.fail("b", "err")
        tracker.start("c")

        s = tracker.summary()
        assert s["completed"] == 1
        assert s["failed"] == 1
        assert s["processing"] == 1
        assert s["total"] == 3

    def test_list_methods(self, tmp_path: Path):
        tracker = ProgressTracker(tmp_path / "progress.json")
        tracker.start("x")
        tracker.complete("x", "/x.mp4")
        tracker.start("y")
        tracker.fail("y", "err")

        assert len(tracker.list_completed()) == 1
        assert len(tracker.list_pending()) == 1  # failed counts as pending
        assert len(tracker.list_all()) == 2

    def test_update_step(self, tmp_path: Path):
        tracker = ProgressTracker(tmp_path / "progress.json")
        tracker.start("vid4")
        tracker.update_step("vid4", "downloading", comments_fetched=42)

        p = tracker.get("vid4")
        assert p.step == "downloading"
        assert p.comments_fetched == 42

    def test_empty_file_no_crash(self, tmp_path: Path):
        path = tmp_path / "progress.json"
        path.write_text("{}", encoding="utf-8")
        tracker = ProgressTracker(path)
        assert tracker.list_all() == []
