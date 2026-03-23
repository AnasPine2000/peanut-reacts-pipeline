"""Tests for peanut_reacts.character.reaction_generator."""

from __future__ import annotations

import json

import pytest

from peanut_reacts.analysis.comments import CommentHighlight
from peanut_reacts.character.reaction_generator import (
    ReactionLine,
    ReactionScript,
    ReactionScriptGenerator,
    _build_user_message,
    _compress_transcript,
    _estimate_speech_duration,
    _parse_llm_response,
    script_from_dict,
    script_to_dict,
)


# ── _estimate_speech_duration ────────────────────────────────────────

class TestEstimateSpeechDuration:
    def test_short_text(self):
        assert _estimate_speech_duration("Wow!") == 1.0  # min 1.0s

    def test_longer_text(self):
        dur = _estimate_speech_duration("This is a longer sentence with many words in it")
        assert dur > 2.0

    def test_empty_text(self):
        assert _estimate_speech_duration("") == 1.0


# ── _parse_llm_response ─────────────────────────────────────────────

class TestParseLLMResponse:
    def test_valid_json_array(self):
        response = json.dumps([
            {"start": 10.0, "text": "That was wild!", "emotion": "shocked"},
            {"start": 30.0, "text": "LOL nice one", "emotion": "amused"},
        ])
        lines = _parse_llm_response(response, video_duration=120.0)
        assert len(lines) == 2
        assert lines[0].start == 10.0
        assert lines[0].text == "That was wild!"
        assert lines[0].emotion == "shocked"

    def test_markdown_fences(self):
        response = "```json\n" + json.dumps([
            {"start": 10.0, "text": "Wow", "emotion": "excited"}
        ]) + "\n```"
        lines = _parse_llm_response(response, video_duration=60.0)
        assert len(lines) == 1

    def test_invalid_emotion_falls_back(self):
        response = json.dumps([
            {"start": 5.0, "text": "test", "emotion": "nonsense_emotion"}
        ])
        lines = _parse_llm_response(response, video_duration=60.0)
        assert len(lines) == 1
        assert lines[0].emotion == "amused"  # fallback for truly invalid emotions

    def test_filters_beyond_duration(self):
        response = json.dumps([
            {"start": 10.0, "text": "ok", "emotion": "amused"},
            {"start": 200.0, "text": "too late", "emotion": "amused"},
        ])
        lines = _parse_llm_response(response, video_duration=60.0)
        assert len(lines) == 1

    def test_trailing_comma_handled(self):
        response = '[{"start": 10.0, "text": "test", "emotion": "amused"},]'
        lines = _parse_llm_response(response, video_duration=60.0)
        assert len(lines) == 1

    def test_removes_overlapping(self):
        response = json.dumps([
            {"start": 10.0, "text": "first", "emotion": "amused"},
            {"start": 11.0, "text": "too close", "emotion": "amused"},
            {"start": 30.0, "text": "ok", "emotion": "amused"},
        ])
        lines = _parse_llm_response(response, video_duration=120.0)
        assert len(lines) == 2  # 11.0 overlaps with 10.0's estimated end

    def test_empty_response(self):
        assert _parse_llm_response("", video_duration=60.0) == []

    def test_garbage_response(self):
        assert _parse_llm_response("I'm not sure what you mean", video_duration=60.0) == []

    def test_wrapped_in_object(self):
        response = json.dumps({
            "reactions": [
                {"start": 15.0, "text": "Nice", "emotion": "excited"}
            ]
        })
        lines = _parse_llm_response(response, video_duration=60.0)
        assert len(lines) == 1


# ── _compress_transcript ─────────────────────────────────────────────

class TestCompressTranscript:
    def test_includes_highlight_context(self, sample_transcript, sample_highlights):
        result = _compress_transcript(sample_transcript, sample_highlights)
        assert "KSI" in result or "220.0" in result

    def test_respects_max_chars(self, sample_transcript, sample_highlights):
        result = _compress_transcript(sample_transcript, sample_highlights, max_chars=50)
        assert len(result) <= 200  # some slack for the truncation message


# ── _build_user_message ──────────────────────────────────────────────

class TestBuildUserMessage:
    def test_contains_title(self, sample_transcript, sample_highlights):
        msg = _build_user_message(sample_transcript, sample_highlights, "Test Video", 600.0)
        assert "Test Video" in msg

    def test_contains_duration(self, sample_transcript, sample_highlights):
        msg = _build_user_message(sample_transcript, sample_highlights, "Test", 600.0)
        assert "600" in msg

    def test_contains_highlight_info(self, sample_transcript, sample_highlights):
        msg = _build_user_message(sample_transcript, sample_highlights, "Test", 600.0)
        assert "VIEWER HIGHLIGHTS" in msg

    def test_no_highlights_fallback(self, sample_transcript):
        msg = _build_user_message(sample_transcript, [], "Test", 600.0)
        assert "interesting transcript moments" in msg


# ── serialization ────────────────────────────────────────────────────

class TestScriptSerialization:
    def test_roundtrip(self):
        script = ReactionScript(
            lines=[
                ReactionLine(start=10.0, end=12.0, text="Wow!", emotion="excited"),
                ReactionLine(start=30.0, end=33.0, text="That was funny", emotion="amused"),
            ],
            video_title="Test",
            generated_by="test-model",
        )
        d = script_to_dict(script)
        restored = script_from_dict(d)
        assert len(restored.lines) == 2
        assert restored.lines[0].text == "Wow!"
        assert restored.video_title == "Test"

    def test_json_serializable(self):
        script = ReactionScript(lines=[], video_title="T", generated_by="m")
        json_str = json.dumps(script_to_dict(script))
        assert isinstance(json_str, str)


# ── ReactionScriptGenerator with mock LLM ────────────────────────────

class MockLLM:
    """Mock LLM that returns a pre-set response."""
    def __init__(self, response: str) -> None:
        self._response = response
        self._model = "mock-model"

    def complete(self, messages: list[dict[str, str]], **kwargs) -> str:
        return self._response


class TestReactionScriptGenerator:
    def test_generates_script(self, sample_transcript, sample_highlights):
        mock_response = json.dumps([
            {"start": 10.0, "text": "This is cool!", "emotion": "excited"},
            {"start": 82.0, "text": "So suspicious!", "emotion": "sarcastic"},
        ])
        gen = ReactionScriptGenerator(MockLLM(mock_response))
        script = gen.generate(
            sample_transcript,
            sample_highlights,
            video_title="Test",
            video_duration=600.0,
        )
        assert len(script.lines) == 2
        assert script.video_title == "Test"

    def test_retries_on_bad_json(self, sample_transcript):
        # First call returns garbage, second returns valid JSON
        call_count = 0
        class RetryLLM:
            _model = "retry-model"
            def complete(self, messages, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return "Not valid JSON at all"
                return json.dumps([{"start": 10.0, "text": "ok", "emotion": "amused"}])

        gen = ReactionScriptGenerator(RetryLLM())
        script = gen.generate(sample_transcript, video_title="T", video_duration=60.0)
        assert call_count == 2
        assert len(script.lines) == 1

    def test_max_reactions_limit(self, sample_transcript):
        many = [{"start": float(i * 20), "text": f"React {i}", "emotion": "amused"} for i in range(20)]
        gen = ReactionScriptGenerator(MockLLM(json.dumps(many)))
        script = gen.generate(sample_transcript, video_duration=500.0, max_reactions=3)
        assert len(script.lines) <= 3
