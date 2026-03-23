"""Tests for peanut_reacts.download.youtube_comments."""

from __future__ import annotations

import pytest

from peanut_reacts.download.youtube_comments import (
    YouTubeComment,
    _parse_iso_duration,
    extract_video_id,
)


class TestExtractVideoId:
    def test_full_url(self):
        assert extract_video_id("https://www.youtube.com/watch?v=DfG-_zOhXIE") == "DfG-_zOhXIE"

    def test_short_url(self):
        assert extract_video_id("https://youtu.be/DfG-_zOhXIE") == "DfG-_zOhXIE"

    def test_bare_id(self):
        assert extract_video_id("DfG-_zOhXIE") == "DfG-_zOhXIE"

    def test_url_with_params(self):
        assert extract_video_id("https://www.youtube.com/watch?v=abc12345678&t=120") == "abc12345678"

    def test_shorts_url(self):
        assert extract_video_id("https://www.youtube.com/shorts/abc12345678") == "abc12345678"

    def test_whitespace_stripped(self):
        assert extract_video_id("  DfG-_zOhXIE  ") == "DfG-_zOhXIE"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            extract_video_id("not-a-valid-url-or-id-at-all")


class TestParseIsoDuration:
    def test_hours_minutes_seconds(self):
        assert _parse_iso_duration("PT1H2M3S") == 3723.0

    def test_minutes_seconds(self):
        assert _parse_iso_duration("PT15M30S") == 930.0

    def test_seconds_only(self):
        assert _parse_iso_duration("PT45S") == 45.0

    def test_hours_only(self):
        assert _parse_iso_duration("PT2H") == 7200.0

    def test_empty_string(self):
        assert _parse_iso_duration("") == 0.0

    def test_invalid_format(self):
        assert _parse_iso_duration("not-a-duration") == 0.0


class TestYouTubeComment:
    def test_creation(self):
        c = YouTubeComment(
            text="Great video!",
            author="user1",
            like_count=42,
            published_at="2024-01-01T00:00:00Z",
        )
        assert c.text == "Great video!"
        assert c.like_count == 42
        assert c.is_reply is False

    def test_reply(self):
        c = YouTubeComment(
            text="I agree",
            author="user2",
            like_count=5,
            published_at="2024-01-02T00:00:00Z",
            is_reply=True,
        )
        assert c.is_reply is True
