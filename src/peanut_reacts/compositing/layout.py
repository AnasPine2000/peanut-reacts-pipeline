"""
Reaction video layout configuration.

Defines how the peanut character, speech text, and live chat are
positioned in the final composite — mimicking popular reaction video
formats (picture-in-picture facecam with bordered frame).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FacecamPosition(Enum):
    TOP_RIGHT = "top_right"
    TOP_LEFT = "top_left"
    BOTTOM_RIGHT = "bottom_right"
    BOTTOM_LEFT = "bottom_left"


@dataclass(frozen=True)
class LayoutConfig:
    """Layout parameters for the reaction video composite."""

    # Facecam frame
    facecam_position: FacecamPosition = FacecamPosition.BOTTOM_RIGHT
    facecam_scale: float = 0.22         # width as fraction of video width
    facecam_margin: int = 16            # pixels from edge
    facecam_border_width: int = 3       # frame border thickness
    facecam_border_color: str = "white"
    facecam_bg_color: str = "0x1a1a2e"  # dark background behind character
    facecam_corner_radius: int = 8      # rounded corners (0 = square)

    # Speech text (subtitle bar near facecam)
    speech_font_size: int = 24
    speech_font_color: str = "white"
    speech_border_width: int = 2
    speech_border_color: str = "black"
    speech_bg_enabled: bool = True
    speech_bg_color: str = "black@0.6"  # semi-transparent background
    speech_position: str = "below_facecam"  # "below_facecam" or "bottom_center"

    # Name tag under facecam (like the "JANITOR" card in the reference)
    name_tag_enabled: bool = True
    name_tag_text: str = "PEANUT"
    name_tag_font_size: int = 18
    name_tag_bg_color: str = "0x1a1a2e@0.85"
    name_tag_font_color: str = "white"

    def facecam_xy(self, video_w: str = "W", video_h: str = "H") -> tuple[str, str]:
        """Return (x, y) ffmpeg overlay expressions for the facecam position."""
        m = self.facecam_margin
        pos = self.facecam_position

        if pos == FacecamPosition.TOP_RIGHT:
            return f"{video_w}-w-{m}", str(m)
        elif pos == FacecamPosition.TOP_LEFT:
            return str(m), str(m)
        elif pos == FacecamPosition.BOTTOM_LEFT:
            return str(m), f"{video_h}-h-{m}"
        else:  # BOTTOM_RIGHT
            return f"{video_w}-w-{m}", f"{video_h}-h-{m}"

    def speech_xy(self, video_w: str = "W", video_h: str = "H") -> tuple[str, str]:
        """Return (x, y) expressions for the speech text position."""
        m = self.facecam_margin
        pos = self.facecam_position

        if self.speech_position == "bottom_center":
            return f"({video_w}-tw)/2", f"{video_h}-60"

        # "below_facecam": place text below the facecam frame
        if pos in (FacecamPosition.BOTTOM_RIGHT, FacecamPosition.BOTTOM_LEFT):
            # Facecam is at bottom — put speech ABOVE the facecam
            # We calculate dynamically using overlay_h
            if pos == FacecamPosition.BOTTOM_RIGHT:
                return f"{video_w}-tw-{m}", f"{video_h}-oh-{m + 40}"
            else:
                return str(m), f"{video_h}-oh-{m + 40}"
        else:
            # Facecam is at top — put speech below it
            if pos == FacecamPosition.TOP_RIGHT:
                return f"{video_w}-tw-{m}", "oh+40"
            else:
                return str(m), "oh+40"

    def name_tag_xy(self, facecam_x: str, facecam_y: str) -> tuple[str, str]:
        """Return (x, y) for the name tag, centered below the facecam."""
        # Positioned at the bottom of the facecam frame
        x = f"{facecam_x}+(ow-tw)/2"
        y = f"{facecam_y}+oh-{self.name_tag_font_size + 8}"
        return x, y
