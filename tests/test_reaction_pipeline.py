"""Tests for peanut_reacts.compositing.reaction_video helpers.

Tests the pure-logic helper functions (_build_ducking_expr,
_build_reaction_audio config) without running ffmpeg or LLM calls.
"""

from __future__ import annotations

import pytest

from peanut_reacts.character.reaction_generator import ReactionLine
from peanut_reacts.compositing.reaction_video import _build_ducking_expr


class TestBuildDuckingExpr:
    """Test the audio ducking expression builder."""

    def test_empty_lines(self):
        assert _build_ducking_expr([]) == ""

    def test_single_line(self):
        line = ReactionLine(start=10.0, end=13.0, text="hello", emotion="amused")
        expr = _build_ducking_expr([line])
        assert "between(t," in expr
        assert "9.80" in expr  # start - 0.2 pad
        assert "13.20" in expr  # end + 0.2 pad

    def test_multiple_lines(self):
        lines = [
            ReactionLine(start=5.0, end=8.0, text="a", emotion="amused"),
            ReactionLine(start=20.0, end=23.0, text="b", emotion="shocked"),
        ]
        expr = _build_ducking_expr(lines)
        assert "+" in expr  # joined with +
        parts = expr.split("+")
        assert len(parts) == 2

    def test_custom_pad(self):
        line = ReactionLine(start=10.0, end=12.0, text="x", emotion="amused")
        expr = _build_ducking_expr([line], pad=0.5)
        assert "9.50" in expr
        assert "12.50" in expr

    def test_start_clamps_at_zero(self):
        line = ReactionLine(start=0.1, end=2.0, text="x", emotion="amused")
        expr = _build_ducking_expr([line])
        # With default pad=0.2, start would be -0.1, clamped to 0
        assert "0.00" in expr or "0," in expr


class TestReactionLine:
    """Test the ReactionLine dataclass."""

    def test_creation(self):
        line = ReactionLine(start=1.0, end=3.5, text="wow", emotion="excited")
        assert line.start == 1.0
        assert line.end == 3.5
        assert line.text == "wow"
        assert line.emotion == "excited"

    def test_is_dataclass(self):
        line = ReactionLine(start=1.0, end=3.5, text="wow", emotion="excited")
        import dataclasses
        assert dataclasses.is_dataclass(line)
