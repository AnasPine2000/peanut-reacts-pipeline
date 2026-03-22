"""
Browser cookie specification parsing for yt-dlp.

yt-dlp's Python API expects ``cookiesfrombrowser`` as a tuple, not a string.
This module converts CLI-style specs like ``"chrome"`` or
``"chrome:Profile 1"`` into the tuple format yt-dlp expects.
"""

from __future__ import annotations


def parse_cookies_from_browser(spec: str) -> tuple[str, ...]:
    """Parse a cookies-from-browser spec into a yt-dlp tuple.

    Examples::

        "chrome"            -> ("chrome",)
        "chrome:Profile 1"  -> ("chrome", "Profile 1")
    """
    spec = spec.strip().strip('"').strip("'")
    if not spec:
        raise ValueError("cookies-from-browser spec is empty")

    if ":" in spec:
        browser, profile = spec.split(":", 1)
        browser = browser.strip()
        profile = profile.strip()
        if profile:
            return (browser, profile)
        return (browser,)
    return (spec,)
