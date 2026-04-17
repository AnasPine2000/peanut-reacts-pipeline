"""Integration test: scraper → db → story_processor end-to-end.

Uses mocked Reddit HTTP responses so we exercise the whole read→filter→clean
→dedupe→render-ready flow without touching real network or running ffmpeg.

Not marked `integration` in the pytest sense because it's still fast — we
save that marker for tests that actually run ffmpeg, PRAW, or YouTube OAuth.
This test sits in `integration/` to cover the module-to-module contract.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from peanut_reacts.reddit.db import StoryDB
from peanut_reacts.reddit.scraper import ScraperConfig, scrape_subreddits
from peanut_reacts.reddit.story_processor import process_story


@pytest.fixture
def db(tmp_path):
    d = StoryDB(tmp_path / "test.db")
    yield d
    d.close()


def _fake_reddit_response(posts):
    return json.dumps(
        {"data": {"children": [{"kind": "t3", "data": p} for p in posts]}}
    ).encode()


def _post(id: str, score: int, title: str, body: str, **extra) -> dict:
    base = {
        "id": id,
        "title": title,
        "selftext": body,
        "score": score,
        "num_comments": 100,
        "author": "u",
        "permalink": f"/r/test/{id}/",
        "created_utc": 1700000000,
    }
    base.update(extra)
    return base


@patch("peanut_reacts.reddit.scraper.urllib.request.urlopen")
def test_full_flow_scrape_then_process(mock_urlopen, db):
    """Scrape 3 stories, only 1 should pass all filters, then process it."""
    posts = [
        _post("keep", 5000, "AITA for eating cake",
              "So my MIL came over and ate all the cake I made. " * 40),
        _post("toolow", 50, "AITA low score",
              "Body content long enough to pass length filter. " * 30),
        _post("tooshort", 5000, "AITA short", "Too short."),  # fails min_chars
    ]
    resp = MagicMock()
    resp.read.return_value = _fake_reddit_response(posts)
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    mock_urlopen.return_value = resp

    cfg = ScraperConfig(
        subreddits=["AmItheAsshole"],
        min_score=500,
        min_chars=100,
        max_chars=20_000,
        request_delay=0,
    )
    n_new = scrape_subreddits(cfg, db)
    assert n_new == 1, "Only the high-score, long-enough story should be kept"
    assert db.has_id("keep")
    assert not db.has_id("toolow")
    assert not db.has_id("tooshort")

    # Now process the stored story as if we were about to render it.
    story = db.get("keep")
    processed = process_story(story, target_format="shorts",
                              max_chars_shorts=850, expand_abbrev=True)

    # Validate end-to-end properties
    assert processed.story_id == "keep"
    assert processed.final_chars <= 1000     # truncated
    assert processed.truncated is True
    # Abbreviation expansion should have run
    assert "Am I the asshole" in processed.tts_text
    assert "mother in law" in processed.tts_text
    # Hook was prepended
    assert "AITA" in processed.hook or "Reddit" in processed.hook
    # No markdown leaked
    assert "**" not in processed.tts_text
    # Estimated duration is plausible for a 60s Short
    assert 30 <= processed.estimated_duration <= 120


@patch("peanut_reacts.reddit.scraper.urllib.request.urlopen")
def test_dedupe_across_runs(mock_urlopen, db):
    """Running the scraper twice with the same posts shouldn't duplicate."""
    posts = [_post(f"s{i}", 5000, f"title{i}", "body content " * 50)
             for i in range(3)]
    resp = MagicMock()
    resp.read.return_value = _fake_reddit_response(posts)
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    mock_urlopen.return_value = resp

    cfg = ScraperConfig(subreddits=["test"], min_score=100, min_chars=50,
                        request_delay=0)

    n1 = scrape_subreddits(cfg, db)
    n2 = scrape_subreddits(cfg, db)
    assert n1 == 3
    assert n2 == 0
    assert db.count_by_status()["new"] == 3


@patch("peanut_reacts.reddit.scraper.urllib.request.urlopen")
def test_status_machine_progresses_through_states(mock_urlopen, db):
    """Scrape → processing → ready is the happy path; failed is the sad path."""
    resp = MagicMock()
    resp.read.return_value = _fake_reddit_response([_post("x", 5000, "t", "b " * 60)])
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    mock_urlopen.return_value = resp

    cfg = ScraperConfig(subreddits=["t"], min_score=100, min_chars=50, request_delay=0)
    scrape_subreddits(cfg, db)

    assert db.count_by_status() == {"new": 1}
    db.set_status("x", "processing")
    assert db.count_by_status() == {"processing": 1}
    db.set_status("x", "ready", video_path="/out/x.mp4")
    assert db.get("x").video_path == "/out/x.mp4"
    assert db.count_by_status() == {"ready": 1}
