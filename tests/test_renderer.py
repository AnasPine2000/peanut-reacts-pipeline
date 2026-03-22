"""Tests for peanut_reacts.character.renderer."""

from __future__ import annotations

import pytest
from PIL import Image

from peanut_reacts.character.renderer import (
    _clamp,
    _lerp,
    draw_peanut_character,
)


# ── math helpers ─────────────────────────────────────────────────────

class TestClamp:
    def test_within_range(self):
        assert _clamp(5.0, 0.0, 10.0) == 5.0

    def test_below_min(self):
        assert _clamp(-1.0, 0.0, 10.0) == 0.0

    def test_above_max(self):
        assert _clamp(15.0, 0.0, 10.0) == 10.0

    def test_at_boundary(self):
        assert _clamp(0.0, 0.0, 10.0) == 0.0
        assert _clamp(10.0, 0.0, 10.0) == 10.0


class TestLerp:
    def test_zero(self):
        assert _lerp(0.0, 10.0, 0.0) == 0.0

    def test_one(self):
        assert _lerp(0.0, 10.0, 1.0) == 10.0

    def test_half(self):
        assert _lerp(0.0, 10.0, 0.5) == 5.0


# ── draw_peanut_character ────────────────────────────────────────────

class TestDrawPeanutCharacter:
    def test_returns_rgba_image(self):
        img = draw_peanut_character(t=0.0)
        assert isinstance(img, Image.Image)
        assert img.mode == "RGBA"

    def test_default_size(self):
        img = draw_peanut_character(t=0.0)
        assert img.size == (512, 512)

    def test_custom_size(self):
        img = draw_peanut_character(t=0.0, size=256)
        assert img.size == (256, 256)

    def test_transparent_corners(self):
        img = draw_peanut_character(t=0.0, size=512)
        # Top-left corner should be transparent (alpha = 0)
        assert img.getpixel((0, 0))[3] == 0

    def test_different_times_produce_different_frames(self):
        img1 = draw_peanut_character(t=0.0, size=128)
        img2 = draw_peanut_character(t=0.5, size=128)
        # At least some pixels should differ due to animation
        assert img1.tobytes() != img2.tobytes()

    def test_mouth_open_override(self):
        closed = draw_peanut_character(t=0.0, size=128, mouth_open_override=0.0)
        wide_open = draw_peanut_character(t=0.0, size=128, mouth_open_override=1.0)
        # Different mouth states should produce different frames
        assert closed.tobytes() != wide_open.tobytes()

    def test_deterministic_with_seed(self):
        img1 = draw_peanut_character(t=1.0, size=128, seed=42)
        img2 = draw_peanut_character(t=1.0, size=128, seed=42)
        assert img1.tobytes() == img2.tobytes()
