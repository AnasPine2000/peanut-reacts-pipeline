"""Tests for peanut_reacts.core.srt and peanut_reacts.core.text_match."""

from __future__ import annotations

from pathlib import Path

import pytest

from peanut_reacts.core.srt import parse_srt, srt_timestamp_to_seconds
from peanut_reacts.core.text_match import find_keyword_matches, fuzzy_contains, fuzzy_match


# ── SRT timestamp parsing ────────────────────────────────────────────

class TestSrtTimestamp:
    def test_basic(self):
        assert srt_timestamp_to_seconds("00:01:30,000") == 90.0

    def test_with_millis(self):
        assert srt_timestamp_to_seconds("00:00:05,500") == 5.5

    def test_hours(self):
        assert srt_timestamp_to_seconds("01:00:00,000") == 3600.0

    def test_all_parts(self):
        result = srt_timestamp_to_seconds("02:30:15,250")
        assert result == 2 * 3600 + 30 * 60 + 15 + 0.25


class TestParseSrt:
    def test_parse_valid_srt(self, tmp_path: Path):
        srt_content = """\
1
00:00:01,000 --> 00:00:05,000
Hello world

2
00:00:06,000 --> 00:00:10,500
This is a test
"""
        srt_file = tmp_path / "test.srt"
        srt_file.write_text(srt_content, encoding="utf-8")
        result = parse_srt(srt_file)

        assert len(result) == 2
        assert result[0]["start"] == 1.0
        assert result[0]["end"] == 5.0
        assert result[0]["text"] == "Hello world"
        assert result[1]["start"] == 6.0
        assert result[1]["end"] == 10.5
        assert result[1]["text"] == "This is a test"

    def test_multiline_text(self, tmp_path: Path):
        srt_content = """\
1
00:00:01,000 --> 00:00:05,000
Line one
Line two
"""
        srt_file = tmp_path / "multi.srt"
        srt_file.write_text(srt_content, encoding="utf-8")
        result = parse_srt(srt_file)

        assert len(result) == 1
        assert result[0]["text"] == "Line one Line two"

    def test_missing_file(self, tmp_path: Path):
        assert parse_srt(tmp_path / "nonexistent.srt") == []

    def test_empty_file(self, tmp_path: Path):
        srt_file = tmp_path / "empty.srt"
        srt_file.write_text("", encoding="utf-8")
        assert parse_srt(srt_file) == []


# ── fuzzy_match ──────────────────────────────────────────────────────

class TestFuzzyMatch:
    def test_identical(self):
        assert fuzzy_match("hello", "hello") is True

    def test_similar(self):
        assert fuzzy_match("hello", "helo", threshold=0.5) is True

    def test_different(self):
        assert fuzzy_match("hello", "world", threshold=0.5) is False

    def test_empty_strings(self):
        assert fuzzy_match("", "", threshold=0.0) is True


# ── fuzzy_contains ───────────────────────────────────────────────────

class TestFuzzyContains:
    def test_exact_word(self):
        assert fuzzy_contains("the impostor killed someone", "impostor") is True

    def test_close_misspelling(self):
        assert fuzzy_contains("the imposter killed someone", "impostor", threshold=0.7) is True

    def test_no_match(self):
        assert fuzzy_contains("hello world", "python", threshold=0.8) is False

    def test_case_insensitive(self):
        assert fuzzy_contains("KSI is funny", "ksi") is True


# ── find_keyword_matches ─────────────────────────────────────────────

class TestFindKeywordMatches:
    def test_exact_mode(self, sample_transcript):
        matches = find_keyword_matches(sample_transcript, "impostor", mode="exact")
        assert len(matches) == 1
        assert "impostor" in matches[0]["text"].lower()

    def test_fuzzy_mode(self, sample_transcript):
        # "suspicious" should fuzzy-match "sus" at lower threshold
        matches = find_keyword_matches(sample_transcript, "suspicious", mode="fuzzy", threshold=0.5)
        assert len(matches) >= 1

    def test_both_mode(self, sample_transcript):
        matches = find_keyword_matches(sample_transcript, "Among", mode="both")
        assert len(matches) >= 1

    def test_no_matches(self, sample_transcript):
        matches = find_keyword_matches(sample_transcript, "xyzzyzzyx", mode="exact")
        assert matches == []
