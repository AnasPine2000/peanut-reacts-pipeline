# TikTok Upload Setup (C3)

Enables headless TikTok upload from the VPS + GitHub Actions runners
via Playwright + cookie session. No phone VM required for v1; we only
escalate to that if cookie-session uploads start getting rate-limited
or captcha-gated.

## Why Playwright + cookies (and not the official API)

TikTok's official Content Posting API requires:
- A registered business account
- App review (2-8 weeks typical)
- Rate-limited heavily on approval

Until we have an approved API app, Playwright driving
`tiktok.com/tiktokstudio/upload` with an already-logged-in session is
the working path. This is how most creator-automation tools operate
in 2026 (`tiktok-uploader` npm/pypi, "auto-uploader" SaaS offerings).

Success rate at our scale: ~85-95%. Most failures are stale cookies
(expire every 30-90 days — re-export) or DOM updates (patch
selectors, push new code). We plan around both.

## One-time install

### Local / VPS

```bash
pip install playwright
playwright install chromium
```

First command adds the Python package (~15 MB), second downloads the
Chromium binary Playwright drives (~350 MB, one-time). Safe to run on
both your local machine and the VPS.

### GitHub Actions

Add these steps to any workflow that uploads to TikTok:

```yaml
- name: Install Playwright
  run: |
    pip install playwright
    playwright install --with-deps chromium
```

`--with-deps` installs the system libraries Chromium needs on Ubuntu.
Cold cache: ~90 s. With `actions/cache` on the `~/.cache/ms-playwright`
directory, subsequent runs are ~15 s.

## Export your TikTok session cookies

TikTok has no "download my session cookies" button; use a browser
extension:

1. Install **Cookie-Editor** in Chrome/Brave/Edge/Firefox
   ([cookie-editor.com](https://cookie-editor.com/))
2. In that same browser, log in to TikTok on the account you want to
   post from (ideally a dedicated burner account tied to the channel —
   TikTok frowns on cross-account automation from shared IPs)
3. Open Cookie-Editor while on `tiktok.com` → click **Export** → choose
   **JSON** → the file is copied to clipboard
4. Save the JSON to `~/.peanut_reacts/tiktok_<channel>.json`
   (e.g. `tiktok_peanut.json`, `tiktok_cashew.json`)

The file should look like:

```json
[
  {"name": "sessionid", "value": "...", "domain": ".tiktok.com", ...},
  {"name": "sid_tt", "value": "...", "domain": ".tiktok.com", ...},
  ...
]
```

Typically 30-60 cookie entries. The critical ones are `sessionid`,
`sid_tt`, `sid_guard`, `uid_tt`, `msToken`.

## Verify with the smoke test (dry mode)

Before wiring TikTok into any pipeline, dry-run against a real video
to confirm auth + the flow works on your machine:

```powershell
PYTHONPATH=src python scripts/smoke_tiktok_upload.py `
    --cookies $env:USERPROFILE\.peanut_reacts\tiktok_hasan.json `
    --video C:\tmp\live_rx_uk_v5\reaction_clips\reaction_005.mp4 `
    --caption "Smoke test — ignore" `
    --headful
```

Expected output:

```
Loaded 42 cookies from tiktok_hasan.json
navigating to TikTok upload page...
page loaded: https://www.tiktok.com/tiktokstudio/upload
waiting for file input...
file input present. Auth + DOM look OK.
Dry-run success. Re-run with --live to actually upload.
```

`--headful` shows the browser window so you can watch what happens
and debug if needed. Drop the flag once things work for headless
(production) mode.

If you see **redirected to login — cookies likely expired**, your
session is stale — re-export from the browser and retry.

## Live upload (test)

Only after a successful dry-run:

```powershell
PYTHONPATH=src python scripts/smoke_tiktok_upload.py `
    --cookies $env:USERPROFILE\.peanut_reacts\tiktok_hasan.json `
    --video C:\tmp\live_rx_uk_v5\reaction_clips\reaction_005.mp4 `
    --caption "Testing cross-post" `
    --hashtags peanut reacts test `
    --live
```

Takes ~30-60 s. Watch the TikTok account after; the post should
appear within a minute. If it shows up under "Drafts" instead of the
main feed, the auto-Post click didn't land — file an issue.

## Wiring into a pipeline (manual, v1)

We're shipping the uploader as a library first, not auto-wiring it
into every pipeline, so you can enable per-channel when you're ready.
Minimal integration pattern, to drop into (for example) the reddit
pipeline after the YouTube upload succeeds:

```python
from peanut_reacts.upload.tiktok_upload import TikTokUploader, TikTokMetadata
from pathlib import Path

# After the YouTube upload block:
tiktok_cookies = Path(channel.tiktok_cookies or "").expanduser()
if tiktok_cookies.exists():
    try:
        tt = TikTokUploader(cookies_path=tiktok_cookies)
        tt_meta = TikTokMetadata(
            caption=meta.description[:300],        # short form
            hashtags=meta.tags[:5],                # TikTok likes <5 tags
            privacy="public",
        )
        tt_res = tt.upload(clip_path, tt_meta)
        if tt_res.success:
            log.info("[TikTok] cross-posted OK")
        else:
            log.warning("[TikTok] cross-post failed: %s", tt_res.error)
    except Exception as e:
        # Never let TikTok failure kill a YouTube-success pipeline run.
        log.warning("[TikTok] skipped due to error: %s", e)
```

Full auto-wiring into every pipeline is a follow-up PR once we've
confirmed the cookie session is stable on your VPS.

## For GitHub Actions

Two additional repo secrets beyond the C4 basics:

| Secret name | What it is |
|---|---|
| `TIKTOK_COOKIES_B64` | Base64-encoded JSON cookie file |

```bash
base64 -w0 ~/.peanut_reacts/tiktok_hasan.json
```

The runner extends `scripts/actions_shorts_runner.py`'s secret
materialization (same base64 pattern as the YouTube tokens) to write
`tiktok_peanut.json` into `~/.peanut_reacts/` before the pipeline
runs. That wiring lives in a follow-up commit once dry-run passes
locally.

## Session expiry & rotation

TikTok cookies last 30-90 days typical. Symptoms of expiry:

- `smoke_tiktok_upload.py` dry run reports "redirected to login"
- `TikTokResult.error == "file-input selector never appeared"`

Fix: re-export from the browser, save to the same path. No code change.

Pro-tip: calendar reminder to re-export every 60 days, before cookies
silently break a scheduled upload at 3 AM.

## When to escalate to phone-VM

Stay on Playwright-cookies until at least one of these happens twice
in a week:

- TikTok presents a captcha challenge the headless browser can't solve
- Account gets temp-banned for automation detection
- Upload succeeds but video immediately gets removed for "violating guidelines"

At that point the cost of stepping up to Waydroid / redroid / Genymotion
Cloud is justified. Until then, Playwright is the low-friction path.
