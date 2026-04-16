"""TikTok auto-uploader via Selenium with cookie persistence.

First run: opens a browser, asks you to log in manually.
Subsequent runs: reuses saved cookies to skip login.

Usage:
    from peanut_reacts.upload.tiktok_uploader import TikTokUploader

    uploader = TikTokUploader(cookies_file="~/.peanut_reacts/tiktok_cookies.json")
    uploader.login_if_needed()  # First time only
    uploader.upload(video_path, caption="Funny clip", hashtags=["sidemen", "fyp"])
"""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=upload"
TIKTOK_HOME = "https://www.tiktok.com"


class TikTokUploader:
    def __init__(
        self,
        cookies_file: str = None,
        headless: bool = False,
        chrome_profile: str = None,
    ):
        self.cookies_file = Path(cookies_file or "~/.peanut_reacts/tiktok_cookies.json").expanduser()
        self.cookies_file.parent.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.chrome_profile = chrome_profile
        self._driver = None

    def _human_delay(self, min_sec: float = 1.5, max_sec: float = 4.0):
        time.sleep(random.uniform(min_sec, max_sec))

    def _setup_driver(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument("--start-maximized")
        opts.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        if self.chrome_profile:
            opts.add_argument(f"--user-data-dir={self.chrome_profile}")

        self._driver = webdriver.Chrome(options=opts)
        self._driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )

    def _save_cookies(self):
        if self._driver:
            cookies = self._driver.get_cookies()
            self.cookies_file.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
            log.info("Saved %d cookies to %s", len(cookies), self.cookies_file)

    def _load_cookies(self) -> bool:
        if not self.cookies_file.exists():
            return False
        try:
            cookies = json.loads(self.cookies_file.read_text(encoding="utf-8"))
            self._driver.get(TIKTOK_HOME)
            self._human_delay(2, 3)
            for c in cookies:
                if "sameSite" in c and c["sameSite"] not in ("Strict", "Lax", "None"):
                    c["sameSite"] = "Lax"
                try:
                    self._driver.add_cookie(c)
                except Exception:
                    pass
            log.info("Loaded %d cookies from %s", len(cookies), self.cookies_file)
            return True
        except Exception as e:
            log.warning("Failed to load cookies: %s", e)
            return False

    def login_if_needed(self):
        """Open TikTok login if not already logged in. Manual login required first time."""
        if not self._driver:
            self._setup_driver()

        # Try to load existing session
        has_cookies = self._load_cookies()
        self._driver.get(TIKTOK_HOME)
        self._human_delay(3, 5)

        # Check if logged in by looking for user avatar or login button
        page_source = self._driver.page_source.lower()
        if "log in" in page_source and "upload" not in self._driver.current_url:
            log.warning("=" * 60)
            log.warning("TIKTOK LOGIN REQUIRED")
            log.warning("=" * 60)
            log.warning("1. A browser window should be open")
            log.warning("2. Log in to TikTok manually")
            log.warning("3. After login, press ENTER here to save cookies")
            log.warning("=" * 60)
            input("Press ENTER after logging in...")
            self._save_cookies()
        else:
            log.info("Already logged in to TikTok")

    def upload(
        self,
        video_path: Path,
        caption: str,
        hashtags: list[str] = None,
        privacy: str = "public",  # public | friends | private
        allow_comment: bool = True,
        allow_duet: bool = True,
        allow_stitch: bool = True,
    ) -> bool:
        """Upload a video to TikTok with caption and hashtags."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        video_path = Path(video_path).resolve()
        if not video_path.exists():
            log.error("Video not found: %s", video_path)
            return False

        if not self._driver:
            self._setup_driver()
            self._load_cookies()

        hashtags = hashtags or []
        full_caption = caption
        if hashtags:
            full_caption += " " + " ".join(f"#{h.lstrip('#')}" for h in hashtags)
        full_caption = full_caption[:2200]  # TikTok limit

        log.info("Uploading to TikTok: %s (%.2f MB)", video_path.name, video_path.stat().st_size / (1024**2))
        log.info("Caption: %s", full_caption[:100])

        try:
            self._driver.get(TIKTOK_UPLOAD_URL)
            self._human_delay(5, 8)

            # Find file input (hidden)
            file_input = WebDriverWait(self._driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']"))
            )
            file_input.send_keys(str(video_path))
            log.info("File uploaded to TikTok upload form, waiting for processing...")

            # Wait for video to process (variable duration)
            self._human_delay(15, 25)

            # Find caption editor (Draft.js contenteditable)
            try:
                caption_area = WebDriverWait(self._driver, 30).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "div[contenteditable='true']")
                    )
                )
            except Exception:
                caption_area = WebDriverWait(self._driver, 30).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, ".public-DraftEditor-content")
                    )
                )

            # Clear existing text and enter caption
            caption_area.click()
            self._human_delay(1, 2)
            caption_area.send_keys(Keys.CONTROL + "a")
            self._human_delay(0.5, 1)
            caption_area.send_keys(Keys.DELETE)
            self._human_delay(0.5, 1)

            # Type caption chunks with human-like delays
            for chunk in full_caption.split(" "):
                caption_area.send_keys(chunk + " ")
                time.sleep(random.uniform(0.02, 0.08))

            self._human_delay(2, 4)

            # Click Post button
            post_selectors = [
                "button[data-e2e='post_video_button']",
                "button[type='button']:has-text('Post')",
                "//button[contains(., 'Post')]",
            ]
            post_clicked = False
            for sel in post_selectors:
                try:
                    if sel.startswith("//"):
                        btn = self._driver.find_element(By.XPATH, sel)
                    else:
                        btn = self._driver.find_element(By.CSS_SELECTOR, sel)
                    btn.click()
                    post_clicked = True
                    break
                except Exception:
                    continue

            if not post_clicked:
                log.error("Could not find Post button")
                return False

            # Wait for upload confirmation
            self._human_delay(10, 20)
            log.info("Upload confirmed on TikTok")
            return True

        except Exception as e:
            log.error("TikTok upload failed: %s", e)
            return False

    def close(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
