"""Unit tests for peanut_reacts.reddit.db (StoryDB)."""
from __future__ import annotations

import pytest

from peanut_reacts.reddit.db import Story, StoryDB


@pytest.fixture
def db(tmp_path):
    d = StoryDB(tmp_path / "test.db")
    yield d
    d.close()


def _mk(id="s1", subreddit="r", title="t", body="body", score=100,
        num_comments=10, char_count=500) -> Story:
    return Story(
        id=id, subreddit=subreddit, title=title, body=body,
        score=score, num_comments=num_comments, author="u",
        url=f"https://reddit.com/{id}", created_utc=0,
        char_count=char_count,
    )


def test_insert_new_returns_true(db):
    assert db.insert_new(_mk("a")) is True


def test_insert_dedupe(db):
    assert db.insert_new(_mk("a")) is True
    assert db.insert_new(_mk("a")) is False
    counts = db.count_by_status()
    assert counts["new"] == 1


def test_has_id(db):
    assert not db.has_id("zzz")
    db.insert_new(_mk("real"))
    assert db.has_id("real")


def test_get_returns_matching_story(db):
    db.insert_new(_mk("lookup", title="Specific title"))
    s = db.get("lookup")
    assert s is not None
    assert s.title == "Specific title"


def test_get_returns_none_for_missing(db):
    assert db.get("missing") is None


def test_iter_by_status_sorted_by_score(db):
    db.insert_new(_mk("low", score=100))
    db.insert_new(_mk("high", score=5000))
    db.insert_new(_mk("mid", score=1000))
    ids = [s.id for s in db.iter_by_status("new")]
    assert ids == ["high", "mid", "low"]


def test_iter_by_status_limits(db):
    for i in range(5):
        db.insert_new(_mk(f"s{i}", score=i * 100))
    result = list(db.iter_by_status("new", limit=2))
    assert len(result) == 2


def test_set_status_transitions(db):
    db.insert_new(_mk("x"))
    db.set_status("x", "processing")
    assert db.get("x").status == "processing"
    db.set_status("x", "ready", video_path="/out/x.mp4")
    got = db.get("x")
    assert got.status == "ready"
    assert got.video_path == "/out/x.mp4"


def test_set_status_records_error(db):
    db.insert_new(_mk("x"))
    db.set_status("x", "failed", error_msg="boom")
    assert db.get("x").error_msg == "boom"


def test_count_by_status_empty_db(db):
    assert db.count_by_status() == {}


def test_count_by_status_multiple_statuses(db):
    db.insert_new(_mk("a"))
    db.insert_new(_mk("b"))
    db.insert_new(_mk("c"))
    db.set_status("a", "ready")
    db.set_status("b", "failed")
    counts = db.count_by_status()
    assert counts == {"new": 1, "ready": 1, "failed": 1}


def test_story_full_text_combines_title_and_body():
    s = _mk(title="Hello", body="world")
    assert s.full_text == "Hello. world"


def test_story_full_text_title_only_when_body_empty():
    s = _mk(title="Hello", body="")
    assert s.full_text == "Hello"
