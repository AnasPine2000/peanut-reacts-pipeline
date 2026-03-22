"""
Cross-platform path sanitization.

Merges the regex approach from playlist_download.py with the Unicode
smart-quote handling from new_opt.py into a single implementation.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable


@runtime_checkable
class IPathSanitizer(Protocol):
    """Abstraction for filename/folder sanitization."""

    def sanitize(self, raw: str) -> str: ...


class DefaultPathSanitizer:
    """Cross-platform filename sanitizer (Windows/macOS/Linux safe).

    - Normalises Unicode smart quotes to ASCII equivalents
    - Removes Windows-reserved characters and control chars
    - Collapses whitespace
    - Avoids empty or dot-only names
    """

    _SMART_QUOTE_MAP = str.maketrans({
        "\u2018": "'",   # left single
        "\u2019": "'",   # right single
        "\u201c": '"',   # left double
        "\u201d": '"',   # right double
        "\uff1a": "_",   # fullwidth colon
    })

    _ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
    _WHITESPACE = re.compile(r"\s+")

    def sanitize(self, raw: str) -> str:
        name = raw.strip()
        # Unicode normalisation
        name = name.translate(self._SMART_QUOTE_MAP)
        # Remove illegal characters
        name = self._ILLEGAL.sub(" ", name)
        # Collapse whitespace
        name = self._WHITESPACE.sub(" ", name).strip()
        # Remove trailing dots/spaces (Windows quirk)
        name = name.rstrip(". ").strip()
        return name or "untitled"
