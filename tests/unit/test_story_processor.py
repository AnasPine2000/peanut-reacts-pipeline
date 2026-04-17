"""Unit tests for peanut_reacts.reddit.story_processor."""
from __future__ import annotations

import pytest

from peanut_reacts.reddit.db import Story
from peanut_reacts.reddit.story_processor import (
    _hook_for,
    _truncate_at_sentence,
    clean_reddit_text,
    expand_abbreviations,
    process_story,
)


# ─────────────────────────────────────────────────────────────────────
# clean_reddit_text
# ─────────────────────────────────────────────────────────────────────

def test_clean_strips_markdown_bold():
    assert clean_reddit_text("**bold** text") == "bold text"
    assert clean_reddit_text("***very bold***") == "very bold"


def test_clean_strips_markdown_links():
    assert clean_reddit_text("check [this link](https://example.com)") == "check this link"


def test_clean_strips_bare_urls():
    out = clean_reddit_text("go to https://example.com for more")
    assert "https://example.com" not in out
    assert "go to" in out


def test_clean_strips_headings():
    assert clean_reddit_text("## Section title") == "Section title"


def test_clean_strips_reddit_edit_blocks():
    text = (
        "Original story body here and more content.\n\n"
        "EDIT: I changed my mind."
    )
    out = clean_reddit_text(text)
    assert "Original story body" in out
    assert "EDIT:" not in out.upper() or "changed my mind" not in out


def test_clean_strips_tldr():
    text = "Story body.\n\nTL;DR: Story summary."
    out = clean_reddit_text(text)
    assert "TL;DR" not in out


def test_clean_collapses_whitespace():
    assert clean_reddit_text("multiple     spaces    here") == "multiple spaces here"


def test_clean_handles_empty_string():
    assert clean_reddit_text("") == ""
    assert clean_reddit_text(None) == ""  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────
# expand_abbreviations
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("inp,expected_sub", [
    ("AITA for being rude", "Am I the asshole"),
    ("My MIL is awful",      "mother in law"),
    ("TIFU by sleeping in",  "Today I messed up"),
    ("NTA, obviously",       "not the asshole"),
    ("OP never replied",     "the original poster"),
])
def test_expand_abbreviations_replaces_standalone_words(inp, expected_sub):
    out = expand_abbreviations(inp)
    assert expected_sub in out


def test_expand_abbreviations_preserves_case_sensitivity():
    # "aita" lowercase should NOT be expanded (we only match \bAITA\b)
    assert "Am I the asshole" not in expand_abbreviations("aita")


def test_expand_abbreviations_word_boundary():
    # "MAITAIKO" contains AITA but shouldn't be expanded
    out = expand_abbreviations("MAITAIKO")
    assert "MAITAIKO" in out
    assert "Am I the asshole" not in out


# ─────────────────────────────────────────────────────────────────────
# _truncate_at_sentence
# ─────────────────────────────────────────────────────────────────────

def test_truncate_shorter_than_max_returns_unchanged():
    text = "Short text."
    out, truncated = _truncate_at_sentence(text, 100)
    assert out == text
    assert truncated is False


def test_truncate_prefers_sentence_boundary():
    text = "First sentence. Second sentence is longer. Third sentence closes it out."
    out, truncated = _truncate_at_sentence(text, 35)
    assert truncated is True
    # Should cut at a period — never in the middle of a sentence
    assert out.endswith(".")


def test_truncate_falls_back_to_word_boundary_if_no_sentence_punctuation():
    text = "word " * 50
    out, truncated = _truncate_at_sentence(text, 30)
    assert truncated is True
    # Either ends at word boundary or has ellipsis
    assert out.endswith("...") or not out.endswith(" ")


# ─────────────────────────────────────────────────────────────────────
# _hook_for
# ─────────────────────────────────────────────────────────────────────

def test_hook_known_subreddit():
    assert "AITA" in _hook_for("AmItheAsshole")


def test_hook_case_insensitive():
    assert _hook_for("amitheasshole") == _hook_for("AmItheAsshole")


def test_hook_fallback_for_unknown_subreddit():
    hook = _hook_for("weirdsubreddit")
    assert isinstance(hook, str) and len(hook) > 0


# ─────────────────────────────────────────────────────────────────────
# process_story
# ─────────────────────────────────────────────────────────────────────

def _make_story(**kw):
    defaults = dict(
        id="x",
        subreddit="AmItheAsshole",
        title="AITA for something minor",
        body="**EDIT**: I updated this. " + "Body content here. " * 200,
        score=5000,
        num_comments=100,
        author="u1",
        url="https://reddit.com/r/AITA/x/",
        created_utc=0,
        char_count=5000,
    )
    defaults.update(kw)
    return Story(**defaults)


def test_process_story_shorts_truncates_to_target():
    s = _make_story()
    processed = process_story(s, target_format="shorts", max_chars_shorts=800)
    assert processed.final_chars <= 900   # allow small padding from hook
    assert processed.truncated is True


def test_process_story_longform_keeps_more():
    s = _make_story()
    shorts = process_story(s, target_format="shorts")
    longform = process_story(s, target_format="longform")
    assert longform.final_chars > shorts.final_chars


def test_process_story_strips_edit_block():
    s = _make_story(body="**EDIT**: removed info. Main story body. " * 100)
    out = process_story(s, target_format="shorts")
    assert "EDIT" not in out.tts_text.upper() or "edit" not in out.tts_text.lower()


def test_process_story_expands_abbreviations_when_enabled():
    s = _make_story(title="AITA for eating my MIL's food",
                    body="My MIL came over. " * 50)
    out = process_story(s, target_format="shorts", expand_abbrev=True)
    assert "Am I the asshole" in out.tts_text
    assert "mother in law" in out.tts_text


def test_process_story_hook_applied():
    s = _make_story()
    out = process_story(s, target_format="shorts")
    assert out.tts_text.startswith(out.hook)


def test_process_story_estimated_duration_positive():
    s = _make_story()
    out = process_story(s, target_format="shorts")
    assert out.estimated_duration > 0


def test_process_story_rejects_unknown_format():
    s = _make_story()
    with pytest.raises(ValueError, match="Unknown target_format"):
        process_story(s, target_format="vr_headset")  # type: ignore[arg-type]
