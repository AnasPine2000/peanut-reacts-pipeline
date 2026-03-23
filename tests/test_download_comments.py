"""Tests for peanut_reacts.download.comments."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from peanut_reacts.download.comments import CommentDownloadJob, CommentDownloader


class TestCommentDownloadJob:
    """Test the CommentDownloadJob configuration dataclass."""

    def test_defaults(self, tmp_path: Path):
        job = CommentDownloadJob(url="https://youtube.com/watch?v=abc", output_dir=tmp_path)
        assert job.cookies_file is None
        assert job.cookies_from_browser is None

    def test_with_cookies(self, tmp_path: Path):
        cookies = tmp_path / "cookies.txt"
        cookies.touch()
        job = CommentDownloadJob(
            url="https://youtube.com/watch?v=abc",
            output_dir=tmp_path,
            cookies_file=cookies,
        )
        assert job.cookies_file == cookies

    def test_frozen(self, tmp_path: Path):
        job = CommentDownloadJob(url="https://youtube.com/watch?v=abc", output_dir=tmp_path)
        with pytest.raises(AttributeError):
            job.url = "other"  # type: ignore[misc]


class TestCommentDownloader:
    """Test the CommentDownloader instantiation."""

    def test_instantiation(self):
        log = logging.getLogger("test")
        downloader = CommentDownloader(log)
        assert downloader._log is log
