"""Shared fixtures for peanut_reacts tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from peanut_reacts.analysis.comments import Comment, CommentHighlight


@pytest.fixture
def tmp_info_json(tmp_path: Path) -> Path:
    """Write a realistic info.json with comments and return its path."""
    data = {
        "id": "test_video",
        "title": "TEST VIDEO - Among Us",
        "duration": 600,
        "comments": [
            {"text": "3:42 this moment is hilarious", "author": "u1", "like_count": 200},
            {"text": "3:45 lmaooo", "author": "u2", "like_count": 80},
            {"text": "3:40 best part", "author": "u3", "like_count": 150},
            {"text": "1:22 so sus", "author": "u4", "like_count": 60},
            {"text": "great video!", "author": "u5", "like_count": 10},
            {"text": "5:10 the kill was insane", "author": "u6", "like_count": 190},
        ],
    }
    path = tmp_path / "test.info.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def sample_comments() -> list[Comment]:
    """A handful of Comment objects for testing compute_highlights."""
    return [
        Comment(text="3:42 hilarious", author="u1", likes=200, timestamp_refs=(222.0,)),
        Comment(text="3:45 lol", author="u2", likes=80, timestamp_refs=(225.0,)),
        Comment(text="1:22 sus", author="u3", likes=60, timestamp_refs=(82.0,)),
        Comment(text="no timestamp", author="u4", likes=10, timestamp_refs=()),
    ]


@pytest.fixture
def sample_transcript() -> list[dict]:
    return [
        {"start": 0.0, "end": 5.0, "text": "Welcome to Among Us everyone"},
        {"start": 10.0, "end": 15.0, "text": "Alright let's start the impostor game"},
        {"start": 80.0, "end": 85.0, "text": "Ethan is looking really suspicious"},
        {"start": 220.0, "end": 225.0, "text": "KSI just hid inside the vent"},
    ]


@pytest.fixture
def sample_highlights() -> list[CommentHighlight]:
    return [
        CommentHighlight(time=82.0, mention_count=1, total_likes=60, sample_comments=("1:22 sus",), score=5.1),
        CommentHighlight(time=222.0, mention_count=2, total_likes=280, sample_comments=("3:42 hilarious", "3:45 lol"), score=9.6),
    ]
