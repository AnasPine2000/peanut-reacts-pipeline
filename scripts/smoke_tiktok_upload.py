"""End-to-end smoke test for the Playwright TikTok uploader.

Purpose: verify the cookie file is valid + Playwright can drive the
TikTok upload flow on this machine, BEFORE we wire it into any
production pipeline.

Flow:
  1. Pick a small test video (default: any mp4 in production/ or C:/tmp)
  2. Load cookies from --cookies path
  3. Try a dry run (DRY=1 env var): launches the browser, navigates
     to upload page, checks that the file input appears — but does
     NOT click Post. Verifies auth is working.
  4. Or full run: actually uploads and clicks Post.

Defaults to DRY mode so you can't accidentally ship garbage to TikTok.

Usage:
  # Dry run — just verify auth + DOM navigation
  PYTHONPATH=src python scripts/smoke_tiktok_upload.py \\
      --cookies ~/.peanut_reacts/tiktok_hasan.json \\
      --video C:/tmp/some_short.mp4 \\
      --caption "Testing the pipeline #test #ignore"

  # Live — actually upload (remove DRY=1 or pass --live)
  PYTHONPATH=src python scripts/smoke_tiktok_upload.py \\
      --cookies ~/.peanut_reacts/tiktok_hasan.json \\
      --video C:/tmp/some_short.mp4 \\
      --caption "Real upload" --live

Exit codes:
  0  upload succeeded (or dry-run auth check passed)
  1  cookies missing / unreadable
  2  video file missing
  3  Playwright / TikTok rejected the upload
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("smoke_tiktok")

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--cookies",
        type=Path,
        default=Path("~/.peanut_reacts/tiktok_hasan.json").expanduser(),
        help="Cookies JSON file exported from a logged-in browser",
    )
    ap.add_argument(
        "--video",
        type=Path,
        required=True,
        help="Video file to upload",
    )
    ap.add_argument(
        "--caption",
        default="Smoke test — ignore",
        help="Post caption",
    )
    ap.add_argument(
        "--hashtags",
        nargs="*",
        default=["test", "smoke", "ignore"],
        help="Hashtags (no # prefix)",
    )
    ap.add_argument(
        "--headful",
        action="store_true",
        help="Show the browser (debug)",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="Actually post (dry-run is default)",
    )
    args = ap.parse_args()

    # Guards
    if not args.cookies.exists():
        log.error("cookies file not found: %s", args.cookies)
        log.error(
            "Export via Cookie-Editor extension on tiktok.com:\n"
            "  install extension -> log in to TikTok in that browser -> "
            "open extension -> Export -> JSON -> save to the path above"
        )
        return 1

    if not args.video.exists():
        log.error("video not found: %s", args.video)
        return 2

    from peanut_reacts.upload.tiktok_upload import TikTokUploader, TikTokMetadata

    if not args.live:
        log.warning("DRY RUN — browser will drive the upload flow but Post will NOT be clicked.")
        log.warning("Add --live to actually submit.")

    uploader = TikTokUploader(
        cookies_path=args.cookies,
        headless=not args.headful,
    )
    metadata = TikTokMetadata(
        caption=args.caption,
        hashtags=args.hashtags,
        privacy="public",
    )

    if args.live:
        result = uploader.upload(args.video, metadata)
        log.info("Result: success=%s url=%s error=%s",
                 result.success, result.url, result.error)
        return 0 if result.success else 3

    # Dry mode: spin up everything up to and including file input, then
    # bail. This verifies auth + that the upload page loads properly.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error(
            "Playwright is not installed. Run:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
        return 3

    cookies = uploader._load_cookies_for_playwright()
    log.info("Loaded %d cookies from %s", len(cookies), args.cookies.name)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        try:
            context = browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=uploader.user_agent,
            )
            context.add_cookies(cookies)
            page = context.new_page()
            log.info("navigating to TikTok upload page...")
            page.goto(
                "https://www.tiktok.com/tiktokstudio/upload",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            log.info("page loaded: %s", page.url)

            if "login" in page.url.lower():
                log.error("redirected to login — cookies likely expired. Re-export.")
                return 1

            from peanut_reacts.upload.tiktok_upload import FILE_INPUT_SELECTOR
            log.info("waiting for file input...")
            file_input = page.wait_for_selector(
                FILE_INPUT_SELECTOR, state="attached", timeout=30_000,
            )
            if file_input is None:
                log.error("file input not found — DOM may have changed or not logged in")
                return 3

            log.info("file input present. Auth + DOM look OK.")
            log.info("Dry-run success. Re-run with --live to actually upload.")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
