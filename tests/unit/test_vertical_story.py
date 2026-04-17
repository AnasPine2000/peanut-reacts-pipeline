"""Unit tests for peanut_reacts.compositing.vertical_story.

Mostly tests pure helpers (time formatting, subtitle chunking, drawtext escaping).
The end-to-end `compose_vertical_short` is covered in integration tests — it
shells out to ffmpeg which we don't run in unit CI.
"""
from __future__ import annotations

import pytest

from peanut_reacts.compositing.vertical_story import (
    VerticalLayoutConfig,
    _chunk_for_subtitles,
    _escape_drawtext,
)


# ─────────────────────────────────────────────────────────────────────
# _escape_drawtext
# ─────────────────────────────────────────────────────────────────────

def test_escape_drawtext_backslash():
    assert _escape_drawtext("a\\b") == "a\\\\b"


def test_escape_drawtext_colon():
    assert "\\:" in _escape_drawtext("time: 3:45")


def test_escape_drawtext_apostrophe_becomes_curly():
    out = _escape_drawtext("don't")
    assert "'" not in out


def test_escape_drawtext_percent():
    assert "\\%" in _escape_drawtext("10% discount")


def test_escape_drawtext_preserves_plain_text():
    assert _escape_drawtext("hello world") == "hello world"


def test_escape_drawtext_strips_newlines():
    assert "\n" not in _escape_drawtext("line1\nline2")


# ─────────────────────────────────────────────────────────────────────
# _chunk_for_subtitles
# ─────────────────────────────────────────────────────────────────────

def test_chunk_empty_text_returns_empty():
    assert _chunk_for_subtitles("", 10.0, 3) == []


def test_chunk_distributes_evenly_across_duration():
    text = "one two three four five six"
    chunks = _chunk_for_subtitles(text, 6.0, 3)
    assert len(chunks) == 2      # 2 lines of 3 words each
    # First chunk 0-3s, second 3-6s
    assert chunks[0][0] == 0.0
    assert chunks[0][1] == pytest.approx(3.0)
    assert chunks[1][0] == pytest.approx(3.0)
    assert chunks[1][1] == pytest.approx(6.0)


def test_chunk_3_words_per_line_groups_correctly():
    words = ["w" + str(i) for i in range(9)]
    text = " ".join(words)
    chunks = _chunk_for_subtitles(text, 9.0, 3)
    assert len(chunks) == 3
    for _, _, line in chunks:
        assert len(line.split()) == 3


def test_chunk_last_group_handles_leftover():
    # 7 words, 3 per chunk → 3 chunks of [3, 3, 1]
    text = "a b c d e f g"
    chunks = _chunk_for_subtitles(text, 7.0, 3)
    assert len(chunks) == 3
    assert len(chunks[-1][2].split()) == 1


def test_chunk_end_never_exceeds_duration():
    text = "one two three"
    chunks = _chunk_for_subtitles(text, 1.0, 3)
    for _, end, _ in chunks:
        assert end <= 1.0


# ─────────────────────────────────────────────────────────────────────
# VerticalLayoutConfig sanity
# ─────────────────────────────────────────────────────────────────────

def test_layout_config_defaults_are_sane():
    cfg = VerticalLayoutConfig()
    assert cfg.canvas_w == 1080
    assert cfg.canvas_h == 1920
    assert 0 < cfg.split_y < cfg.canvas_h
    assert cfg.subtitle_chunk_words > 0
    assert 0 <= cfg.bg_darken <= 1


def test_layout_config_override():
    cfg = VerticalLayoutConfig(canvas_w=720, canvas_h=1280, fps=24)
    assert cfg.canvas_w == 720
    assert cfg.canvas_h == 1280
    assert cfg.fps == 24
