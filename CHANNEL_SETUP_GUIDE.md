# Channel Setup Guide — YouTube + TikTok + Instagram

**Last Updated:** 2026-04-14
**Purpose:** Step-by-step guide for creating all needed accounts and connecting them to the autonomous pipeline.

---

## Overview

The autonomous pipeline can post to channels automatically **after** they exist, but creating the initial accounts requires manual steps because platforms require phone verification, CAPTCHAs, and identity checks that can't be safely automated.

**What's manual (one-time setup):**
- Creating Google accounts
- Creating YouTube channels (branding, handle, description)
- Creating TikTok accounts (phone verification)
- Creating Instagram accounts (phone/email verification)
- First-time login on each platform

**What's automated (ongoing):**
- Content creation
- Video uploads
- Descriptions, titles, hashtags
- Scheduling
- Monitoring and analytics
- Community replies (optional)

---

## Planned Channel Network

| # | Platform | Channel Name | Character | Status |
|---|----------|-------------|-----------|--------|
| 1 | YouTube | UK CLIPS | Peanut | EXISTS (2,650 subs) |
| 2 | TikTok | @peanutreacts | Peanut | TO CREATE |
| 3 | Instagram | @peanutreacts | Peanut | TO CREATE |
| 4 | YouTube | HasanAbi Archive (name TBD) | Chilli | TO CREATE |
| 5 | TikTok | @chilli_hasan | Chilli | TO CREATE |
| 6 | YouTube | Lofi Vibes | Marshmallow | TO CREATE |
| 7 | YouTube | Peanut Stories | Cashew | TO CREATE |
| 8 | YouTube | Peanut Reacts KSI | Pistachio | TO CREATE |

---

## Part 1: YouTube Channel Creation

### Step 1: Create or Use a Google Account
Each YouTube channel needs a separate Google account OR you can use **brand accounts** (recommended — one Google account can own multiple YouTube channels).

**Option A — Brand Accounts (Recommended):**
1. Go to https://www.youtube.com/account
2. Click "Add or manage your channel(s)"
3. Click "Create a channel"
4. Enter channel name (e.g., "Chilli Reacts")
5. This creates a NEW channel under your existing Google account
6. Repeat for each channel

**Benefits:**
- Single login across all channels
- Easy switching between channels in YouTube Studio
- No need to create new email addresses
- All channels under your ownership legally

**Option B — Separate Google Accounts:**
- Only use this if you want completely separate channel identities
- Requires unique phone number per account (or SMS verification bypass)
- More complex to manage

### Step 2: Channel Setup Per Channel
After creating each channel, do this setup ONCE per channel:

1. **Profile picture** — Upload character avatar (Peanut, Chilli, etc.)
2. **Banner** — 2560x1440 banner with channel branding
3. **Description** — 3-4 sentence channel description
4. **Handle** — Set `@channel_name` (e.g., @peanutreacts)
5. **Links** — Add links to other social platforms
6. **Categories** — Set to appropriate category (Gaming, Entertainment, Music)
7. **Keywords** — Add relevant SEO keywords

### Step 3: Enable API Access
The pipeline uses YouTube Data API v3 for uploads.

1. Go to https://console.cloud.google.com/
2. Create a new project (or use existing)
3. Enable "YouTube Data API v3"
4. Create OAuth 2.0 credentials (Desktop app type)
5. Download `client_secret.json`
6. Save to a path you'll reference in `channels.yaml`

### Step 4: First-Time Authentication
For each channel, run:

```bash
PYTHONPATH=src python -c "
from pathlib import Path
from peanut_reacts.upload.youtube_auth import get_authenticated_service
service = get_authenticated_service(
    Path('path/to/client_secret.json'),
    token_path=Path('~/.peanut_reacts/tokens/CHANNEL_ID.json').expanduser(),
)
print('Authenticated!')
"
```

This opens a browser for OAuth consent. **Critical:** Select the correct Google account AND the correct brand channel during consent. The token file saves the channel-specific credentials.

Repeat for each channel, changing `CHANNEL_ID.json` to a unique name per channel.

### Step 5: Update channels.yaml
```yaml
channels:
  - id: peanut_uk_clips
    name: "UK CLIPS"
    character: peanut
    client_secrets: "C:/path/to/client_secret.json"
    oauth_token: "~/.peanut_reacts/tokens/uk_clips.json"
    # ... other fields
```

---

## Part 2: TikTok Channel Creation

### Step 1: Create TikTok Accounts

**Desktop method (recommended):**
1. Go to https://www.tiktok.com/signup
2. Click "Use phone or email"
3. Use a unique email (or phone number) per channel
4. Set username: `@peanutreacts`, `@chilli_hasan`, etc.
5. Complete phone verification

**Important:** TikTok has strict anti-spam. Don't create more than 1-2 accounts from the same IP/device per day.

### Step 2: Profile Setup Per Account
1. **Profile picture** — Same character avatar as YouTube
2. **Bio** — Include: tagline + "YouTube: [channel]" + link
3. **Link** — YouTube channel URL (or Linktree)
4. **Category** — Set creator category
5. **Privacy** — Public
6. **Two-factor auth** — Enable for security (won't block automation)

### Step 3: First Manual Uploads
**Critical: Do 5-10 manual uploads before automating.** TikTok flags brand-new accounts that immediately start automated posting.

- Post manually for 1 week
- Follow 20-30 similar accounts
- Like/comment on trending content (warms up the algorithm)
- This establishes "human" patterns

### Step 4: Set Up Selenium Login
The pipeline uses cookie-based authentication via Selenium:

```bash
PYTHONPATH=src python -c "
from peanut_reacts.upload.tiktok_uploader import TikTokUploader
t = TikTokUploader(cookies_file='~/.peanut_reacts/tiktok_peanut.json')
t.login_if_needed()
print('Logged in, cookies saved')
t.close()
"
```

This opens a browser. Log in manually, then press Enter. The script saves cookies for future automated sessions.

**Repeat per TikTok account** with different cookie file names.

### Step 5: TikTok Rate Limits to Respect
- Max 5-10 uploads per day per account (stay safe)
- Min 2 hours between uploads
- Random delays between actions (already built into our uploader)
- If shadow-banned: pause automation for 3-7 days, post manually

---

## Part 3: Instagram Channel Creation (Optional)

Same process as TikTok but for Instagram Reels. Use the same character branding.

### Steps
1. Create Instagram account via app (easier than web)
2. Switch to Creator/Business account in settings
3. Link to Facebook Page (required for API access OR Meta Business Suite)
4. Manual posting first week
5. Set up Selenium with cookies

**Alternative: Instagram Graph API**
- Requires Facebook Business verification
- Official upload API
- Rate limits: 25 posts/day per account
- More reliable than Selenium but harder to set up initially

---

## Part 4: Cross-Linking Strategy

Once all accounts exist, link them together for maximum cross-promotion:

### YouTube Channel
- **Description:** "Follow @peanutreacts on TikTok + Instagram"
- **Community tab:** Post weekly TikTok highlights
- **Video descriptions:** Always include TikTok link
- **Cards/end screens:** Link to TikTok (within YouTube allowed links)
- **Channel trailer:** Feature character + socials

### TikTok Bio
- **Link:** YouTube channel or Linktree
- **Tagline:** "[Character] Reacts | Full videos on YouTube"
- **Pinned videos:** Best 3 peak clips

### Instagram Bio
- Same as TikTok
- Stories: Link to latest YouTube video daily

---

## Part 5: Credential Storage

**File structure for credentials:**
```
~/.peanut_reacts/
├── tokens/
│   ├── uk_clips.json           # YouTube OAuth token
│   ├── hasanabi_archive.json
│   ├── lofi_vibes.json
│   ├── peanut_stories.json
│   └── peanut_ksi.json
├── tiktok_cookies/
│   ├── peanut.json             # TikTok cookies
│   ├── chilli.json
│   └── marshmallow.json
└── instagram_cookies/
    ├── peanut.json
    └── chilli.json
```

**Security:**
- Never commit these files to git
- Add `tokens/`, `*_cookies/`, `*.json` to `.gitignore`
- Back up weekly to encrypted drive

---

## Part 6: Automation Enablement Checklist

Before running the autonomous pipeline for a channel:

- [ ] Google account created (or using existing)
- [ ] YouTube brand channel created
- [ ] Channel art uploaded (banner + profile pic)
- [ ] YouTube Data API v3 enabled
- [ ] `client_secret.json` downloaded
- [ ] First OAuth run completed (token cached)
- [ ] Channel appears in `channels.yaml`
- [ ] First manual upload to test pipeline (not required but recommended)

For TikTok:
- [ ] TikTok account created
- [ ] 5-10 manual posts done (warm-up)
- [ ] Selenium login completed
- [ ] Cookies saved to file
- [ ] `channels.yaml` references the cookies file

---

## Part 7: Phase 1 Priority (This Week)

**Focus on one channel at a time.** Don't try to launch 5 channels simultaneously.

### Week 1: UK CLIPS (Existing Channel)
1. [x] YouTube channel exists (2,650 subs)
2. [x] YouTube API configured
3. [ ] Create TikTok `@peanutreacts`
4. [ ] Warm up TikTok (5-10 manual posts)
5. [ ] Set up Selenium cookies
6. [ ] Enable autonomous Shorts extraction + TikTok posting

### Week 2: HasanAbi Channel (NEW)
1. [ ] Create Google brand channel
2. [ ] Design channel art (Chilli character)
3. [ ] First 3 manual uploads (segments from recent VOD)
4. [ ] Create TikTok `@chilli_reacts`
5. [ ] Warm up TikTok
6. [ ] Enable automation

### Week 3-4: Scale
- [ ] Lofi Vibes channel
- [ ] Peanut Stories channel
- [ ] KSI channel

---

## Troubleshooting

### "OAuth consent screen shows wrong channel"
- The Google account has multiple channels
- When consenting, explicitly select the right channel
- Delete the cached token file and re-authenticate if needed

### "TikTok says 'Something went wrong' on upload"
- TikTok is rate-limiting or suspicious activity detection
- Stop automation for 24 hours
- Post manually to re-establish human patterns
- Reduce upload frequency to 2-3/day

### "YouTube API quota exceeded"
- Default quota: 10,000 units/day
- Each upload uses 1600 units
- Max ~6 uploads per day on free tier
- Apply for quota increase if needed (usually approved)

### "Selenium Chrome driver mismatch"
- Update Chrome to latest
- Run: `pip install --upgrade selenium webdriver-manager`
- Delete the chromedriver cache

---

## Related Documents
- `BUSINESS_PLAN.md` — Overall business strategy
- `SHORTS_TIKTOK_STRATEGY.md` — Peak clip extraction strategy
- `channels.yaml` — Channel configurations
- `EXECUTION_PLAN.xlsx` — Day-by-day rollout plan
