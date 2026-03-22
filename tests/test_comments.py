"""Tests for peanut_reacts.analysis.comments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from peanut_reacts.analysis.comments import (
    Comment,
    CommentHighlight,
    compute_highlights,
    extract_comment_highlights,
    highlights_from_dicts,
    highlights_to_dicts,
    load_comments,
    parse_timestamps,
)


# ── parse_timestamps ─────────────────────────────────────────────────

class TestParseTimestamps:
    def test_mm_ss(self):
        assert parse_timestamps("check 3:42 dude") == [222.0]

    def test_h_mm_ss(self):
        assert parse_timestamps("at 1:05:30 lol") == [3930.0]

    def test_multiple(self):
        assert parse_timestamps("3:42 and 1:05:30") == [222.0, 3930.0]

    def test_no_timestamps(self):
        assert parse_timestamps("great video!!!") == []

    def test_zero_timestamp(self):
        assert parse_timestamps("0:00 the start") == [0.0]

    def test_leading_zero(self):
        assert parse_timestamps("at 03:42") == [222.0]

    def test_embedded_in_text(self):
        assert parse_timestamps("OMG 10:30 was crazy") == [630.0]


# ── load_comments ────────────────────────────────────────────────────

class TestLoadComments:
    def test_load_from_info_json(self, tmp_info_json: Path):
        comments = load_comments(tmp_info_json)
        assert len(comments) == 6
        assert all(isinstance(c, Comment) for c in comments)

    def test_timestamps_parsed(self, tmp_info_json: Path):
        comments = load_comments(tmp_info_json)
        ts_comments = [c for c in comments if c.timestamp_refs]
        assert len(ts_comments) == 5  # 5 of 6 have timestamps

    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert load_comments(tmp_path / "nonexistent.json") == []

    def test_no_comments_key(self, tmp_path: Path):
        p = tmp_path / "empty.info.json"
        p.write_text(json.dumps({"id": "test"}), encoding="utf-8")
        assert load_comments(p) == []


# ── compute_highlights ───────────────────────────────────────────────

class TestComputeHighlights:
    def test_clusters_nearby_timestamps(self, sample_comments):
        highlights = compute_highlights(sample_comments, cluster_window=15.0)
        # 222.0 and 225.0 should cluster; 82.0 is separate
        assert len(highlights) == 2

    def test_no_timestamped_comments(self):
        comments = [Comment(text="no ts", author="u", likes=10, timestamp_refs=())]
        assert compute_highlights(comments) == []

    def test_max_highlights_limit(self, sample_comments):
        highlights = compute_highlights(sample_comments, max_highlights=1)
        assert len(highlights) == 1

    def test_sorted_chronologically(self, sample_comments):
        highlights = compute_highlights(sample_comments)
        times = [h.time for h in highlights]
        assert times == sorted(times)

    def test_score_formula(self, sample_comments):
        highlights = compute_highlights(sample_comments)
        for h in highlights:
            import math
            expected = h.mention_count * 2 + math.log(h.total_likes + 1)
            assert abs(h.score - expected) < 0.01

    def test_cluster_window_zero_no_merge(self):
        comments = [
            Comment(text="t1", author="u", likes=10, timestamp_refs=(100.0,)),
            Comment(text="t2", author="u", likes=10, timestamp_refs=(101.0,)),
        ]
        highlights = compute_highlights(comments, cluster_window=0.0)
        assert len(highlights) == 2


# ── extract_comment_highlights (integration) ─────────────────────────

class TestExtractCommentHighlights:
    def test_end_to_end(self, tmp_info_json: Path):
        highlights = extract_comment_highlights(tmp_info_json)
        assert len(highlights) > 0
        assert all(isinstance(h, CommentHighlight) for h in highlights)

    def test_cluster_window_affects_results(self, tmp_info_json: Path):
        narrow = extract_comment_highlights(tmp_info_json, cluster_window=1.0)
        wide = extract_comment_highlights(tmp_info_json, cluster_window=60.0)
        assert len(narrow) >= len(wide)


# ── serialization roundtrip ──────────────────────────────────────────

class TestHighlightSerialization:
    def test_roundtrip(self, sample_highlights):
        dicts = highlights_to_dicts(sample_highlights)
        restored = highlights_from_dicts(dicts)
        assert len(restored) == len(sample_highlights)
        for orig, rest in zip(sample_highlights, restored):
            assert orig.time == rest.time
            assert orig.mention_count == rest.mention_count
            assert orig.total_likes == rest.total_likes
            assert orig.sample_comments == rest.sample_comments

    def test_serializes_to_json(self, sample_highlights):
        dicts = highlights_to_dicts(sample_highlights)
        json_str = json.dumps(dicts)
        assert isinstance(json_str, str)
        assert json.loads(json_str) == dicts
