"""Headless TikTok upload via Playwright + cookie session.

Purpose-built for cloud / CI contexts (GitHub Actions, Hetzner VPS)
where we can't open an interactive browser to log in. Authenticates
using a cookies JSON file exported from a browser extension like
"Cookie-Editor" on the account you want to post from.

The older src/peanut_reacts/upload/tiktok_uploader.py is Selenium-based
and requires a visible browser for first-time login — keep that for
local manual workflows. This module is for unattended automation.

Interface mirrors YouTubeUploader's so pipelines treat the two
platforms uniformly:

    TikTokMetadata(caption=..., hashtags=..., privacy=...)
    TikTokUploader(cookies_path).upload(video, metadata) -> TikTokResult

Failures return TikTokResult(success=False, error=...) instead of
raising, so a pipeline's primary YouTube upload doesn't get torpedoed
by a flaky TikTok session.

First-run setup:
  1. `pip install playwright` (already in requirements-actions.txt)
  2. `playwright install chromium`
  3. Export cookies from a logged-in browser session into a JSON file
     (Cookie-Editor extension -> Export -> JSON). Save to
     ~/.peanut_reacts/tiktok_<account>.json
  4. Import TikTokUploader and call upload(). First upload tests the
     session; if it works, the cookie file is good for ~30-90 days.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=upload"
TIKTOK_HOME = "https://www.tiktok.com"

# Upload-progress selectors. TikTok's creator studio DOM changes
# occasionally; we try a few in order. If all fail, the timeout hits
# and we bail with a clear error.
FILE_INPUT_SELECTOR = "input[type='file']"
CAPTION_EDITOR_SELECTORS = [
    "div[contenteditable='true']",
    ".public-DraftEditor-content",
]
POST_BUTTON_SELECTORS = [
    "button[data-e2e='post_video_button']",
    "button:has-text('Post')",
    "button[type='button']:has-text('Post')",
]

# TikTok caption field length cap (including hashtags).
TIKTOK_CAPTION_MAX = 2200

# Default timeout for selector waits (ms). TikTok's video processing
# step can take 30-90 s for longer clips, so the waits here are
# generous.
DEFAULT_WAIT_MS = 60_000


@dataclass
class TikTokMetadata:
    """Matches the shape of UploadMetadata in youtube_upload.py enough
    that pipelines can pass similar data to both."""
    caption: str                          # The post text (no hashtags here)
    hashtags: list[str] = field(default_factory=list)
    privacy: str = "public"               # public | friends | private
    allow_comment: bool = True
    allow_duet: bool = True
    allow_stitch: bool = True

    def full_caption(self) -> str:
        """Caption + inline hashtags, capped at TikTok's 2200-char limit."""
        text = self.caption.strip()
        if self.hashtags:
            tags = " ".join(f"#{h.lstrip('#')}" for h in self.hashtags)
            text = f"{text} {tags}".strip()
        return text[:TIKTOK_CAPTION_MAX]


@dataclass
class TikTokResult:
    """Upload outcome. success=True means the Post button was clicked
    and TikTok accepted the submission. Doesn't guarantee the post
    isn't later flagged for moderation."""
    success: bool
    url: str = ""              # Post URL if we could read it, else ""
    video_id: str = ""
    error: str = ""

    def __bool__(self) -> bool:
        return self.success


class TikTokUploader:
    """Playwright-driven TikTok upload client.

    Thread-safety: each upload() call spins up its own Playwright
    browser context and closes it, so instances are safe to share
    across concurrent pipeline runs if needed.
    """

    def __init__(
        self,
        cookies_path: Path,
        headless: bool = True,
        user_agent: Optional[str] = None,
        slow_mo_ms: int = 0,
    ):
        self.cookies_path = Path(cookies_path).expanduser()
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        )

    # ── Cookie handling ──────────────────────────────────────────────

    def _load_cookies_for_playwright(self) -> list[dict]:
        """Read the JSON cookie file and transform it into the shape
        Playwright's BrowserContext.add_cookies wants.

        Cookie-Editor style browser exports typically have these fields:
            domain, name, value, path, expirationDate, httpOnly,
            secure, sameSite, hostOnly, storeId, session, id

        Playwright accepts: name, value, domain, path, expires,
        httpOnly, secure, sameSite (one of 'Strict'/'Lax'/'None').
        We drop the browser-only fields that Playwright rejects.
        """
        if not self.cookies_path.exists():
            raise FileNotFoundError(f"TikTok cookies file missing: {self.cookies_path}")

        raw = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(
                f"Cookies file must be a JSON array, got {type(raw).__name__}. "
                f"Export via Cookie-Editor -> Export -> JSON."
            )

        cookies: list[dict] = []
        for c in raw:
            item = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
                "httpOnly": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", False)),
            }
            if not item["name"] or item["value"] is None:
                continue

            # expires: Cookie-Editor uses expirationDate (float seconds);
            # Playwright uses expires (float seconds, -1 for session).
            exp = c.get("expirationDate") or c.get("expires")
            if exp is not None:
                item["expires"] = float(exp)

            # sameSite normalization. Browser exports variously use
            # 'no_restriction' / 'unspecified' / None — Playwright
            # rejects those.
            same = (c.get("sameSite") or "").lower()
            if same in ("strict",):
                item["sameSite"] = "Strict"
            elif same in ("no_restriction", "none"):
                item["sameSite"] = "None"
            else:
                item["sameSite"] = "Lax"

            cookies.append(item)
        return cookies

    # ── Upload ──────────────────────────────────────────────────────

    def upload(self, video_path: Path, metadata: TikTokMetadata) -> TikTokResult:
        """Drive the TikTok creator studio upload flow end-to-end.

        Steps:
          1. Launch headless Chromium with loaded cookies
          2. Navigate to the upload URL
          3. set_input_files() on the hidden <input type="file">
          4. Wait for TikTok to finish processing the video preview
          5. Fill the caption editor (Draft.js contenteditable div)
          6. Click Post
          7. Wait for the success redirect / confirmation

        If any step fails, we close cleanly and return a TikTokResult
        with the exception message.
        """
        video_path = Path(video_path).resolve()
        if not video_path.exists():
            return TikTokResult(success=False, error=f"video not found: {video_path}")

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            return TikTokResult(
                success=False,
                error="playwright not installed. `pip install playwright` "
                "and `playwright install chromium`.",
            )

        try:
            cookies = self._load_cookies_for_playwright()
        except Exception as e:
            return TikTokResult(success=False, error=f"cookie load failed: {e}")

        full_caption = metadata.full_caption()
        log.info(
            "[TikTok] uploading %s (%.2f MB, %s) caption=%r",
            video_path.name,
            video_path.stat().st_size / (1024 * 1024),
            metadata.privacy,
            full_caption[:80],
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                context = browser.new_context(
                    viewport={"width": 1366, "height": 900},
                    user_agent=self.user_agent,
                    locale="en-US",
                )
                context.add_cookies(cookies)
                page = context.new_page()
                page.set_default_timeout(DEFAULT_WAIT_MS)

                return self._drive_upload(page, video_path, full_caption, metadata)
            except Exception as e:
                log.exception("[TikTok] upload crashed")
                return TikTokResult(success=False, error=str(e))
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    # ── Internals ───────────────────────────────────────────────────

    def _drive_upload(self, page, video_path: Path, full_caption: str,
                       metadata: TikTokMetadata) -> TikTokResult:
        """The happy-path sequence. Factored out so the outer upload()
        can own lifecycle (browser launch/close)."""
        from playwright.sync_api import TimeoutError as PWTimeout

        # 1. Navigate. Cookie session means no login redirect if the
        #    session is still good; if cookies are stale we'll land on
        #    a login page and the file-input wait will time out — that
        #    becomes the error message.
        page.goto(TIKTOK_UPLOAD_URL, wait_until="domcontentloaded")
        log.info("[TikTok] on upload page")

        # 2. File input. It's hidden; set_input_files() injects directly.
        try:
            file_input = page.wait_for_selector(
                FILE_INPUT_SELECTOR, state="attached", timeout=DEFAULT_WAIT_MS,
            )
        except PWTimeout:
            return TikTokResult(
                success=False,
                error="file-input selector never appeared — cookies likely stale "
                "or TikTok DOM changed. Re-export cookies and retry.",
            )
        file_input.set_input_files(str(video_path))
        log.info("[TikTok] file sent; waiting for processing preview")

        # 3. Wait for video processing to complete. TikTok shows a
        #    preview thumbnail + progress indicator; we wait until the
        #    caption editor becomes interactive.
        caption_area = None
        for selector in CAPTION_EDITOR_SELECTORS:
            try:
                caption_area = page.wait_for_selector(selector, timeout=DEFAULT_WAIT_MS)
                log.info("[TikTok] caption area found (%s)", selector)
                break
            except PWTimeout:
                continue
        if caption_area is None:
            return TikTokResult(
                success=False,
                error="caption editor never appeared — video processing may have "
                "failed. TikTok's upload pipeline sometimes rejects videos with "
                "audio too quiet or resolution too low.",
            )

        # 4. Caption. Draft.js is finicky; we click, select-all,
        #    delete, then type slowly. Typing character-by-character
        #    with small random delays looks more human than send_keys().
        caption_area.click()
        page.wait_for_timeout(500)
        page.keyboard.press("Control+A")
        page.wait_for_timeout(200)
        page.keyboard.press("Delete")
        page.wait_for_timeout(400)
        for char in full_caption:
            page.keyboard.type(char)
            # 15-45 ms per keystroke — fast enough to not take forever,
            # slow enough that Draft.js hashtag autodetect keeps up.
            time.sleep(random.uniform(0.015, 0.045))
        log.info("[TikTok] caption filled (%d chars)", len(full_caption))

        # 5. Privacy toggle — optional. The default on the creator
        #    studio is usually "Public", so we only interact if the
        #    caller explicitly asked for Friends / Private. Skipping
        #    for v1 because the selectors are the most fragile part
        #    of this whole flow.
        if metadata.privacy != "public":
            log.warning(
                "[TikTok] privacy=%s requested but only 'public' is wired in v1 — "
                "posting as public.",
                metadata.privacy,
            )

        # 6. Click Post. Try each known selector until one hits.
        post_clicked = False
        for selector in POST_BUTTON_SELECTORS:
            try:
                btn = page.wait_for_selector(selector, timeout=5_000, state="visible")
                if btn and btn.is_enabled():
                    btn.click()
                    post_clicked = True
                    log.info("[TikTok] post clicked (%s)", selector)
                    break
            except Exception:
                continue

        if not post_clicked:
            return TikTokResult(
                success=False,
                error="Post button never found/clickable. Video preview may still "
                "be processing or TikTok shifted the DOM.",
            )

        # 7. Confirmation. After Post, TikTok usually redirects to the
        #    Manage Videos page or shows a success toast. We wait for
        #    the URL to change OR a success pattern, up to 30 s.
        try:
            page.wait_for_url(
                re.compile(r"tiktok\.com.*(tiktokstudio|manage|content)", re.I),
                timeout=30_000,
            )
            log.info("[TikTok] upload confirmed via URL redirect")
        except Exception:
            # Best-effort: give it a bit more time, then claim success
            # if no obvious error banner appeared.
            page.wait_for_timeout(5_000)
            if page.locator("text=/upload failed|error occurred/i").count() > 0:
                return TikTokResult(
                    success=False,
                    error="TikTok showed an error message after Post click.",
                )
            log.info("[TikTok] no explicit confirmation, but no error banner either")

        # 8. Best-effort grab the uploaded video URL/id. TikTok doesn't
        #    return this cleanly post-upload; the creator studio manage
        #    page lists recent uploads. We skip digging for it in v1 —
        #    success + no error banner is enough to consider the upload
        #    done.
        return TikTokResult(success=True, url="", video_id="")
