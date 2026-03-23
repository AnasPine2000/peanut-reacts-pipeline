"""Tests for peanut_reacts.core.ffmpeg helper functions.

Tests that don't require actual video files or ffmpeg binary.
Integration tests for ffprobe-based functions are skipped if ffprobe
is not available.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from peanut_reacts.core.ffmpeg import get_video_dimensions, get_video_duration


class TestGetVideoDimensions:
    """Test get_video_dimensions with mocked ffprobe output."""

    def test_parses_dimensions(self, tmp_path: Path):
        fake_video = tmp_path / "test.mp4"
        fake_video.touch()

        with patch("peanut_reacts.core.ffmpeg.subprocess.check_output") as mock:
            mock.return_value = b"1920x1080\n"
            w, h = get_video_dimensions(fake_video)
            assert w == 1920
            assert h == 1080

    def test_parses_non_standard(self, tmp_path: Path):
        fake_video = tmp_path / "test.mp4"
        fake_video.touch()

        with patch("peanut_reacts.core.ffmpeg.subprocess.check_output") as mock:
            mock.return_value = b"3840x2160\n"
            w, h = get_video_dimensions(fake_video)
            assert w == 3840
            assert h == 2160

    def test_parses_small_video(self, tmp_path: Path):
        fake_video = tmp_path / "test.mp4"
        fake_video.touch()

        with patch("peanut_reacts.core.ffmpeg.subprocess.check_output") as mock:
            mock.return_value = b"640x480\n"
            w, h = get_video_dimensions(fake_video)
            assert w == 640
            assert h == 480


class TestGetVideoDuration:
    """Test get_video_duration with mocked ffprobe output."""

    def test_parses_duration(self, tmp_path: Path):
        fake_video = tmp_path / "test.mp4"
        fake_video.touch()

        with patch("peanut_reacts.core.ffmpeg.subprocess.check_output") as mock:
            mock.return_value = b"123.456\n"
            d = get_video_duration(fake_video)
            assert abs(d - 123.456) < 0.001

    def test_integer_duration(self, tmp_path: Path):
        fake_video = tmp_path / "test.mp4"
        fake_video.touch()

        with patch("peanut_reacts.core.ffmpeg.subprocess.check_output") as mock:
            mock.return_value = b"60\n"
            d = get_video_duration(fake_video)
            assert d == 60.0
