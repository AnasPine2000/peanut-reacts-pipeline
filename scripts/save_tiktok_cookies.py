"""Save TikTok cookies from an already-logged-in Selenium session.

Run this script directly — it opens Chrome, loads TikTok,
waits for you to confirm you're logged in, then saves cookies.
"""
import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

COOKIES_PATH = Path.home() / ".peanut_reacts" / "tiktok_hasan.json"
COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)

print("Opening Chrome to TikTok...")
opts = Options()
opts.add_argument("--disable-blink-features=AutomationControlled")
opts.add_experimental_option("excludeSwitches", ["enable-automation"])
opts.add_argument("--start-maximized")

driver = webdriver.Chrome(options=opts)
driver.get("https://www.tiktok.com/@youtubers.archive")

print()
print("=" * 50)
print("LOG IN to TikTok if not already logged in.")
print("Once you see your @youtubers.archive profile,")
print("press ENTER here to save cookies.")
print("=" * 50)
input()

cookies = driver.get_cookies()
COOKIES_PATH.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
print(f"Saved {len(cookies)} cookies to {COOKIES_PATH}")

auth = [c for c in cookies if "session" in c["name"].lower() or "sid" in c["name"].lower()]
print(f"Auth cookies: {len(auth)}")

driver.quit()
print("Done!")
