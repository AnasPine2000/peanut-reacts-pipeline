"""Tests for peanut_reacts.compositing.layout."""

from __future__ import annotations

import pytest

from peanut_reacts.compositing.layout import FacecamPosition, LayoutConfig


class TestFacecamXY:
    """Test facecam_xy() returns correct ffmpeg overlay expressions."""

    def test_bottom_right(self):
        layout = LayoutConfig(facecam_position=FacecamPosition.BOTTOM_RIGHT, facecam_margin=16)
        x, y = layout.facecam_xy()
        assert x == "W-w-16"
        assert y == "H-h-16"

    def test_top_right(self):
        layout = LayoutConfig(facecam_position=FacecamPosition.TOP_RIGHT, facecam_margin=10)
        x, y = layout.facecam_xy()
        assert x == "W-w-10"
        assert y == "10"

    def test_top_left(self):
        layout = LayoutConfig(facecam_position=FacecamPosition.TOP_LEFT, facecam_margin=20)
        x, y = layout.facecam_xy()
        assert x == "20"
        assert y == "20"

    def test_bottom_left(self):
        layout = LayoutConfig(facecam_position=FacecamPosition.BOTTOM_LEFT, facecam_margin=5)
        x, y = layout.facecam_xy()
        assert x == "5"
        assert y == "H-h-5"


class TestLayoutDefaults:
    """Test default values are sensible."""

    def test_default_position(self):
        layout = LayoutConfig()
        assert layout.facecam_position == FacecamPosition.BOTTOM_RIGHT

    def test_default_scale_reasonable(self):
        layout = LayoutConfig()
        assert 0.1 <= layout.facecam_scale <= 0.5

    def test_default_border(self):
        layout = LayoutConfig()
        assert layout.facecam_border_width > 0
        assert layout.facecam_border_color == "white"

    def test_default_name_tag(self):
        layout = LayoutConfig()
        assert layout.name_tag_enabled is True
        assert layout.name_tag_text == "PEANUT"

    def test_default_speech(self):
        layout = LayoutConfig()
        assert layout.speech_font_size > 0
        assert layout.speech_bg_enabled is True


class TestLayoutFrozen:
    """LayoutConfig is a frozen dataclass — values should not be mutable."""

    def test_immutable(self):
        layout = LayoutConfig()
        with pytest.raises(AttributeError):
            layout.facecam_scale = 0.5  # type: ignore[misc]


class TestSpeechXY:
    def test_bottom_center(self):
        layout = LayoutConfig(speech_position="bottom_center")
        x, y = layout.speech_xy()
        assert "tw" in x  # centered
        assert "60" in y

    def test_below_facecam_top_right(self):
        layout = LayoutConfig(
            facecam_position=FacecamPosition.TOP_RIGHT,
            speech_position="below_facecam",
        )
        x, y = layout.speech_xy()
        # Should position below facecam (y includes oh + offset)
        assert "oh" in y or "40" in y


class TestNameTagXY:
    def test_returns_expressions(self):
        layout = LayoutConfig()
        x, y = layout.name_tag_xy("100", "200")
        assert isinstance(x, str)
        assert isinstance(y, str)


class TestFacecamPositionEnum:
    def test_all_positions_exist(self):
        assert len(FacecamPosition) == 4
        assert FacecamPosition.TOP_RIGHT.value == "top_right"
        assert FacecamPosition.BOTTOM_LEFT.value == "bottom_left"
