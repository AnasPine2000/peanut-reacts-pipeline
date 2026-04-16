# Peak Moment Clips — Shorts + TikTok Marketing Strategy

**Last Updated:** 2026-04-14
**Status:** Planning -> Implementation
**Goal:** Extract the most viral moments from long-form content and use them to drive cross-platform growth

---

## Table of Contents
1. [Core Insight](#core-insight)
2. [Why This Matters](#why-this-matters)
3. [Three Methods to Find Peak Moments](#three-methods-to-find-peak-moments)
4. [Clip Extraction Pipeline](#clip-extraction-pipeline)
5. [Character Reactions to Compilations](#character-reactions-to-compilations)
6. [TikTok Posting Strategy](#tiktok-posting-strategy)
7. [Cross-Platform Growth Loop](#cross-platform-growth-loop)
8. [Technical Implementation](#technical-implementation)
9. [Content Calendar](#content-calendar)
10. [KPIs & Success Metrics](#kpis--success-metrics)

---

## 1. Core Insight

**Every long video has 5-10 "peak moments" where viewers replay, laugh, or share.** These are the most viral-ready clips in your entire library. Most creators guess which moments work — we can use **actual data** to find them:

- YouTube's "most replayed" heatmap shows the exact seconds where viewers rewatch
- Viewer comments cluster around funny/shocking moments with timestamps
- Our own reaction density peaks when the character gets excited
- Audio loudness spikes correlate with key events

**Combining these 3-4 signals gives us a ranked list of the best 30-60 second clips in every video we produce.**

Each peak moment becomes:
1. A YouTube Short (vertical 9:16, under 60s)
2. A TikTok post
3. An Instagram Reel
4. Embedded back into future long-form "best of" compilations

This is a **zero-cost content multiplier** — we already have the long-form videos.

---

## 2. Why This Matters

### The Math
A single 10-hour compilation typically has **15-30 peak moments** worth clipping. At 3-4 moments per minute of "interesting" content:

| Asset | Clips Generated | Platforms | Total Posts |
|-------|----------------|-----------|-------------|
| 1 long video | 20 clips | 3 platforms | 60 posts |
| 10 long videos | 200 clips | 3 platforms | **600 posts** |

Each clip takes under 1 minute to generate once the pipeline is built. The library compounds forever.

### The Growth Loop
```
Long video (YouTube) 
    -> 20 peak clips extracted
    -> 20 TikToks posted (with "Full video on YT" caption)
    -> TikTok viewers subscribe to YouTube
    -> More YouTube watch hours
    -> More peak clips to extract
```

### Industry Data (from research)
- 74% of TikTok/Shorts views come from non-subscribers (highest discovery rate)
- Channels using Shorts + long-form grow 41% faster
- "Full video on my YouTube" captions convert ~2-5% of TikTok viewers to YouTube
- TikTok viral clips can hit 1M+ views in 48 hours
- Conversion rate from TikTok follower to YouTube subscriber: ~3-8%

---

## 3. Three Methods to Find Peak Moments

We combine three data sources, score each moment, and pick the top N clips per video.

### Method 1: YouTube Most-Replayed Heatmap
YouTube embeds a heatmap in every video showing the exact seconds where viewers rewatch. This is the strongest signal of "this is the best part."

**Access:** Not in the official YouTube Data API, but available via:
- `videos.list` endpoint with `parts=mostReplayed` (sometimes exposed)
- Scraping the YouTube video page's embedded player data (public JSON blob)
- Third-party libraries like `youtube-dl`/`yt-dlp` output `heatmap` field when available

**Output:** List of `{start_time, intensity_0_1}` pairs. Higher intensity = more replays.

### Method 2: Comment Timestamp Clustering
Viewers post timestamps in comments when they find moments funny/shocking/memorable. These are gold — they're literally viewers telling us where the best moments are.

**Extraction:**
- We already fetch YouTube comments via `download/youtube_comments.py`
- Regex match timestamps: `(\d+):(\d+)` or `(\d+):(\d+):(\d+)`
- Cluster timestamps within 5-10 seconds of each other
- Score = number of comments mentioning that timestamp + like count

**Output:** List of `{timestamp, comment_count, total_likes, sample_comments}` clusters.

### Method 3: Reaction Density / Emotion Peaks
Our own pipeline generates reaction scripts with emotion labels. Peak moments are where:
- Multiple high-intensity reactions cluster (e.g., 3+ reactions in 10 seconds)
- Emotion labels are "shocked", "laughing", "mind_blown", "screaming"
- The peanut character's intensity score is maxed

**Access:** Already stored in our `reactions.json` files for every generated video.

**Output:** List of `{timestamp, reaction_density, peak_emotion, intensity_score}`.

### Combined Scoring Formula
```python
score = (
    replay_intensity * 0.4 +       # Most objective signal
    comment_cluster_size * 0.3 +   # Social proof
    reaction_density * 0.2 +       # Our character's peak
    loudness_spike * 0.1           # Audio cue
)
```

Pick top 10-20 moments per long video. Enforce minimum 60 seconds between peaks to avoid overlap.

---

## 4. Clip Extraction Pipeline

### Architecture

```
[Long video]
    |
    v
[Peak Detection]  (YouTube heatmap + comments + reactions)
    |
    v
[Rank & Filter]  (top N, min spacing)
    |
    v
[Clip Extraction]  (ffmpeg, 15-60s each, with padding)
    |
    v
[Format for Vertical]  (crop/blur to 9:16, 1080x1920)
    |
    v
[Add Context]  (intro card, hook text, outro CTA, captions)
    |
    v
[Save per platform]  (YT Shorts, TikTok, Instagram)
```

### Per-Clip Components
1. **Hook (first 1-2 seconds)** — The most attention-grabbing frame, optionally frozen with text overlay
2. **Context card (0-3 seconds)** — "The moment KSI LOST IT" or "Wait for Simon's face..."
3. **The clip itself (10-50 seconds)** — The actual peak moment
4. **Peanut reaction overlay** — Character appears in corner with a reaction specific to the clip
5. **Captions** — Burnt-in auto-captions (essential for muted viewing on TikTok)
6. **Outro CTA (last 2 seconds)** — "Full video on YouTube: [Channel Name]" with subscribe animation
7. **Background music** — Low-volume trending audio (TikTok algorithm boosts trending sounds)

### Vertical Format Conversion
Source videos are 16:9 (1920x1080). TikTok/Shorts need 9:16 (1080x1920):

**Option A — Center crop + blurred background:**
- Main clip centered, cropped to 9:16
- Blurred version of same frame as background
- Works for any video

**Option B — Face tracking (for reaction content):**
- Detect the Sidemen webcam position
- Zoom in on their faces
- Fill empty space with peanut character

**Option C — Split screen:**
- Top: Sidemen gameplay/reaction (16:9 letterboxed)
- Bottom: Peanut character reacting (full height)

We'll start with Option A (simplest, universal), add Option C for character-led clips.

---

## 5. Character Reactions to Compilations

This is the **second format** for Shorts/TikTok content — not just extracting peak moments from existing videos, but **creating new character-led clips**.

### Format: "Peanut Reacts to X"
Short videos where the character watches and comments on:
- Top 10 moments from a week of Sidemen content
- "This clip is INSANE" style single-clip reactions
- Comparison videos: "KSI vs Simon: Who's the better imposter?"
- Trending clips from Twitter/X reposted to TikTok with peanut commentary
- Meme reactions

### Character Personality per Format
| Content Type | Character | Voice Style |
|-------------|-----------|-------------|
| Sidemen peak moments | Peanut | Hype, excited, loyal fan |
| HasanAbi clips | Chilli | Spicy takes, political edge |
| Lofi/chill content | Marshmallow | Soft, dreamy commentary |
| Reddit drama clips | Cashew | Sarcastic narrator |
| KSI clips | Pistachio | Chaotic, unhinged |
| Data/analysis | Walnut | Smart, analytical |
| True crime | Almond | Serious, gripping |

### Why This Works on TikTok
- Characters have personality = branding = recognition = follows
- Short attention span viewers love "react to X" format
- Low production cost (same pipeline, just 30-60s output)
- Can post 5-10 per day per character per platform
- Creates a character universe viewers collect/follow

---

## 6. TikTok Posting Strategy

### Posting Cadence
| Account | Platform | Daily Posts | Weekly Total |
|---------|----------|-------------|--------------|
| UK CLIPS (YT) | YouTube Shorts | 3 | 21 |
| UK CLIPS | TikTok | 5 | 35 |
| UK CLIPS | Instagram Reels | 3 | 21 |
| HasanAbi Archive | YouTube Shorts | 3 | 21 |
| HasanAbi | TikTok | 5 | 35 |
| **Total** | | **19** | **133 per week** |

With automation, this is achievable. Without automation, impossible.

### Title & Caption Formula
**YouTube Shorts:**
- Title: "[HOOK] #sidemen #amongus #peanutreacts"
- Hook examples: "This is why KSI is DONE", "Simon's FUNNIEST moment EVER", "Wait for the ending..."
- Max 100 chars
- Include 2-3 hashtags

**TikTok:**
- No title, just caption (150-200 char sweet spot)
- First line is the hook
- Include call-to-action: "Full video on YT (link in bio)"
- 3-5 hashtags: #sidemen #amongus #fyp #viral #foryou
- Tag creators: @sidemenofficial (they won't sue if you credit them)

**Instagram Reels:**
- Similar to TikTok but more emoji
- Include location tags and creator tags
- Use trending audio overlaid under original audio

### Best Posting Times (UK + US audience)
| Day | UK CLIPS | HasanAbi | Notes |
|-----|----------|----------|-------|
| Mon | 7am, 12pm, 6pm, 9pm, 11pm | 9am, 2pm, 6pm, 9pm, 11pm | Monday starts the week |
| Tue-Thu | Same | Same | Consistency matters |
| Fri | 5pm, 7pm, 9pm, 11pm, 1am | 5pm, 7pm, 9pm, 11pm, 1am | Peak weekend lead-in |
| Sat | 10am, 2pm, 5pm, 8pm, 11pm | Same | Highest viewing day |
| Sun | 12pm, 3pm, 6pm, 9pm, 11pm | Same | Sunday night binge |

### Hashtag Strategy
- **Always:** #fyp #foryou #viral (TikTok algorithm bait)
- **Content-specific:** #sidemen #amongus #ksi #miniminter #hasanabi
- **Character-specific:** #peanutreacts #chillireacts
- **Trending piggyback:** Check TikTok trending every morning, use 1-2 trending tags

### Music Strategy
TikTok heavily boosts videos using **trending audio**. Two approaches:
1. **Keep original audio** (for comedic/reaction moments where the audio IS the content)
2. **Overlay trending music** at low volume under the original (get algorithm boost + keep content)

Tool: `ffmpeg -i clip.mp4 -i trending.mp3 -filter_complex "[1:a]volume=0.15[bg];[0:a][bg]amix=inputs=2" -c:v copy output.mp4`

---

## 7. Cross-Platform Growth Loop

### The Flywheel
```
                    TikTok Viral
                    (1M views)
                   /            \
                  v              v
         Link in bio      Character recognition
         to YouTube              |
               |                 v
               v          Follow character brand
        YouTube Short              |
         3M views                  v
               |           Subscribe to all
               v          character channels
        YouTube Long            |
         (10hr video)           |
               |                |
               v                v
        Watch hours grow   Cross-promotion
               |           in bio/descriptions
               v
        YPP monetization
               |
               v
        Memberships & ads
```

### Specific Cross-Promotion Tactics
1. **Every TikTok ends with:** "Full [topic] compilation on my YouTube - Peanut Reacts"
2. **YouTube Shorts description includes:** "More on TikTok: @peanutreacts"
3. **Weekly "best TikTok comments" video** on YouTube: pure community building
4. **Character crossover events:** "Peanut + Chilli react together" on both channels
5. **YouTube comment pinning:** Pin a comment linking to TikTok on every long-form video

### Link in Bio Structure (Linktree or similar)
- YouTube Main Channel
- YouTube Shorts Playlist
- TikTok Account
- Instagram Reels
- Discord Server (community building)
- Channel Memberships signup (direct revenue link)
- Merch store (future)

---

## 8. Technical Implementation

### New Files to Create

| File | Purpose | Est. Lines |
|------|---------|-----------|
| `src/peanut_reacts/clips/peak_detector.py` | YouTube heatmap scraper + comment timestamp extraction | ~200 |
| `src/peanut_reacts/clips/moment_scorer.py` | Combined scoring across 3 signals | ~100 |
| `src/peanut_reacts/clips/clip_extractor.py` | FFmpeg extraction with padding and vertical crop | ~150 |
| `src/peanut_reacts/clips/context_adder.py` | Intro cards, captions, outro CTAs, character overlay | ~200 |
| `src/peanut_reacts/clips/captioner.py` | Auto-caption generation (burn-in) | ~100 |
| `src/peanut_reacts/clips/vertical_formatter.py` | 9:16 crop with blurred background | ~80 |
| `src/peanut_reacts/upload/tiktok_uploader.py` | TikTok upload via Selenium (reuse Youtubers_Aid pattern) | ~250 |
| `src/peanut_reacts/upload/instagram_uploader.py` | Instagram Reels upload | ~200 |
| `src/peanut_reacts/scheduler/shorts_pipeline.py` | Orchestrator: detect peaks -> extract -> format -> multi-platform post | ~200 |
| `scripts/process_peak_clips.py` | CLI entry point for manual runs | ~100 |

**Total: ~1,580 lines of new code.** Can be built incrementally.

### Reusable Existing Modules
| Existing Module | Use For |
|----------------|---------|
| `download/youtube_comments.py` | Method 2 (comment timestamps) |
| `core/ffmpeg.py` | Clip cutting, format conversion |
| `core/srt.py` | Transcript parsing for captions |
| `analysis/loudness.py` | Method 4 (audio spikes) |
| `character/reaction_generator.py` | Generate new reaction commentary |
| `character/tts.py` | Voice the character reactions |
| `compositing/reaction_video.py` | Composite character over clip |
| `upload/youtube_upload.py` | YouTube Shorts upload (same API) |
| `Youtubers_Aid/script/poster.py` | Selenium TikTok uploader (template) |

### Peak Detection Code Skeleton

```python
# src/peanut_reacts/clips/peak_detector.py

def get_youtube_heatmap(video_id: str) -> list[dict]:
    """Fetch most-replayed heatmap from YouTube page.
    
    Uses yt-dlp's internal heatmap extraction.
    Returns: [{start_time: 45.2, intensity: 0.87}, ...]
    """
    import yt_dlp
    ydl = yt_dlp.YoutubeDL({"quiet": True})
    info = ydl.extract_info(f"https://youtube.com/watch?v={video_id}", download=False)
    return info.get("heatmap", [])

def extract_comment_timestamps(comments: list[dict]) -> list[dict]:
    """Find timestamps mentioned in comments and cluster them."""
    import re
    from collections import defaultdict
    
    timestamps = defaultdict(lambda: {"count": 0, "likes": 0, "samples": []})
    pattern = re.compile(r"(\d+):(\d+)(?::(\d+))?")
    
    for comment in comments:
        for match in pattern.finditer(comment.get("text", "")):
            parts = [int(x) for x in match.groups() if x]
            seconds = parts[-1] + parts[-2] * 60 + (parts[-3] * 3600 if len(parts) == 3 else 0)
            # Bucket to nearest 10 seconds
            bucket = (seconds // 10) * 10
            timestamps[bucket]["count"] += 1
            timestamps[bucket]["likes"] += comment.get("like_count", 0)
            if len(timestamps[bucket]["samples"]) < 3:
                timestamps[bucket]["samples"].append(comment.get("text", "")[:100])
    
    return [{"time": t, **data} for t, data in sorted(timestamps.items())]

def detect_reaction_peaks(reactions_json: Path) -> list[dict]:
    """Find clusters of high-intensity reactions in our own pipeline output."""
    import json
    reactions = json.loads(reactions_json.read_text())
    
    # Sliding window of 10 seconds
    peaks = []
    for i, r in enumerate(reactions):
        window = [x for x in reactions if r["start"] <= x["start"] < r["start"] + 10]
        if len(window) >= 3:  # 3+ reactions in 10s = peak
            intensity = sum(x.get("intensity", 1.0) for x in window) / len(window)
            peaks.append({
                "time": r["start"],
                "density": len(window),
                "intensity": intensity,
                "peak_emotion": window[0].get("emotion", "neutral"),
            })
    
    return peaks

def score_and_rank_peaks(
    heatmap: list[dict],
    comment_peaks: list[dict],
    reaction_peaks: list[dict],
    min_spacing_seconds: int = 60,
) -> list[dict]:
    """Combine all signals and return top ranked peak moments."""
    # Normalize each source to 0-1 scale, combine with weights, dedupe within spacing
    # ...
    pass
```

### TikTok Upload (Selenium-Based)

Reuse the pattern from `Youtubers_Aid/script/poster.py`:

```python
# src/peanut_reacts/upload/tiktok_uploader.py

from selenium import webdriver
from selenium.webdriver.common.by import By
from pathlib import Path
import time
import random

def upload_to_tiktok(
    video_path: Path,
    caption: str,
    hashtags: list[str],
    cookies_file: Path,
    privacy: str = "public",
):
    """Upload a video to TikTok using Selenium with saved cookies."""
    driver = webdriver.Chrome()
    try:
        # Load saved cookies (avoid re-login each time)
        driver.get("https://www.tiktok.com")
        load_cookies(driver, cookies_file)
        
        # Navigate to upload
        driver.get("https://www.tiktok.com/upload")
        time.sleep(5)
        
        # Upload file
        file_input = driver.find_element(By.CSS_SELECTOR, "input[type='file']")
        file_input.send_keys(str(video_path.absolute()))
        
        # Wait for processing
        time.sleep(random.uniform(8, 12))
        
        # Fill caption
        caption_field = driver.find_element(By.CSS_SELECTOR, "div[data-e2e='editor-text']")
        caption_field.click()
        full_caption = caption + "\n\n" + " ".join(f"#{h}" for h in hashtags)
        caption_field.send_keys(full_caption)
        
        # Post
        time.sleep(random.uniform(3, 6))
        post_button = driver.find_element(By.CSS_SELECTOR, "button[data-e2e='post_video_button']")
        post_button.click()
        
        # Confirm
        time.sleep(10)
        return True
    finally:
        driver.quit()
```

**Note on TikTok automation risk:** Selenium-based uploads can trigger bot detection if done too frequently or too fast. Safeguards:
- Random delays between all actions (human-like)
- Residential IP (or at least not a datacenter IP)
- Maximum 5-10 uploads per day per account
- Rotate between manual and automated uploads sometimes

**Alternative:** TikTok for Developers API requires business verification but is the "official" path once approved. We can start with Selenium and apply for API access in parallel.

---

## 9. Content Calendar

### Daily Shorts Pipeline
| Time | Task | Output |
|------|------|--------|
| 06:00 | Fetch new comments on top 10 YouTube videos | Comment data updated |
| 06:15 | Score all long videos for new peaks | Peak list per video |
| 06:30 | Extract top 5 new peak clips from archive | 5 raw clips |
| 06:45 | Apply vertical format + captions + character | 5 polished clips |
| 07:00 | Post clip 1 to YouTube Shorts + TikTok + Instagram | 3 posts |
| 12:00 | Post clip 2 to all platforms | 3 posts |
| 18:00 | Post clip 3 to all platforms | 3 posts |
| 21:00 | Post clip 4 to all platforms | 3 posts |
| 23:00 | Post clip 5 to all platforms | 3 posts |

**Total: 15 posts per day from 5 clips** (3 platforms each).

### Weekly Cadence by Character
| Character | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|-----------|-----|-----|-----|-----|-----|-----|-----|
| Peanut | 3 | 3 | 3 | 3 | 3 | 5 | 4 |
| Chilli | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Marshmallow | 1 | 1 | 1 | 1 | 1 | 2 | 2 |
| Cashew | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| **Total daily** | **9** | **9** | **9** | **9** | **9** | **12** | **11** |

**Weekly total: 68 clips across 4 characters.** x 3 platforms = **204 posts per week**.

---

## 10. KPIs & Success Metrics

### Week 1-2 (Validation)
- [ ] Extract 10 peak clips from existing videos
- [ ] Post 10 TikToks manually (test the format)
- [ ] Track engagement: views, completion rate, shares
- [ ] Identify which clip type performs best

### Week 3-4 (Automation)
- [ ] Peak detection pipeline working end-to-end
- [ ] Selenium TikTok uploader tested
- [ ] First fully automated clip posted
- [ ] Target: 50 clips posted across all platforms

### Month 2 (Scale)
- [ ] 200 TikToks posted
- [ ] First TikTok to hit 100K views
- [ ] 500 TikTok followers
- [ ] Measurable traffic from TikTok to YouTube (track via YouTube analytics -> traffic source)

### Month 3-6 (Growth)
- [ ] First viral TikTok (1M+ views)
- [ ] 10K TikTok followers
- [ ] 500+ YouTube subscribers attributed to TikTok
- [ ] Revenue from TikTok Creator Fund (eligible at 10K followers)

### Key Metrics to Track
| Metric | Target (Month 3) | Target (Month 6) |
|--------|-----------------|------------------|
| TikTok followers | 2,000 | 10,000 |
| TikTok total views | 500K | 5M |
| Avg views per post | 2K | 15K |
| Best single post | 50K | 1M |
| YouTube subs from TikTok | 50 | 500 |
| Conversion rate (TT->YT) | 0.5% | 1.5% |
| YouTube watch hours from Shorts funnel | 200 | 2,000 |

---

## 11. Integration with Existing Plan

This Shorts/TikTok strategy slots into the existing channel network without disruption:

**From BUSINESS_PLAN.md:**
- The "Shorts = growth engine" pillar is already in the content strategy
- Revenue projections assume Shorts as discovery tool
- All character IDs already exist

**From EXECUTION_PLAN.xlsx:**
- Daily schedule already includes Shorts posting
- We're just adding TikTok + Instagram to the same output

**From the autonomous scheduler:**
- Add new job: `shorts_pipeline` runs every 6 hours
- Picks 1 unprocessed long video -> extracts peaks -> queues clips for posting
- Add cron triggers for each posting window

**New channels.yaml additions:**
```yaml
shorts_automation:
  enabled: true
  peak_detection_method: "combined"  # combined | heatmap | comments | reactions
  clips_per_video: 15
  min_clip_seconds: 15
  max_clip_seconds: 60
  vertical_format: "blur_bg"  # blur_bg | split_screen | face_track
  post_schedule:
    - platform: youtube_shorts
      times: ["07:00", "12:00", "18:00", "21:00", "23:00"]
    - platform: tiktok
      times: ["07:00", "12:00", "18:00", "21:00", "23:00"]
    - platform: instagram_reels
      times: ["08:00", "14:00", "20:00"]
  tiktok_cookies: "~/.peanut_reacts/tiktok_cookies.json"
  instagram_cookies: "~/.peanut_reacts/instagram_cookies.json"
```

---

## 12. Risk Management

| Risk | Impact | Mitigation |
|------|--------|-----------|
| TikTok bans automated uploads | Can't post | Use residential IP, random delays, cap at 5/day, have manual fallback |
| Sidemen DMCA on TikTok clips | Clips removed | Credit in caption, no 1:1 reposts (add character commentary), monitor TikTok inbox |
| Comment timestamp spam/bots | False peaks | Require 3+ different commenters per timestamp bucket |
| YouTube heatmap API changes | Method 1 breaks | Fall back to methods 2+3 only |
| Character reactions feel forced | Low engagement | A/B test characters per platform, iterate on scripts |
| Cross-platform copyright | Multi-platform strikes | Start with YouTube Shorts only (same account), expand after 30 days of no issues |
| TikTok shadowban from repetition | 0 views | Vary content, use different hashtags, post original non-clip content 20% of the time |

---

## 13. Implementation Priority

### Phase 1 — Validation (Week 1)
Build the minimum viable version to test the concept:
1. `peak_detector.py` using only yt-dlp heatmap (easiest signal)
2. `clip_extractor.py` basic FFmpeg cut
3. `vertical_formatter.py` with blur-bg approach
4. Manual posting to 3 platforms
5. Track performance over 7 days

### Phase 2 — Automation (Week 2-3)
Once validation confirms the format works:
1. Add comment timestamp extraction (Method 2)
2. Add reaction density peak detection (Method 3)
3. Build combined scoring
4. Selenium TikTok uploader
5. Scheduler integration

### Phase 3 — Scale (Month 2)
1. Instagram Reels uploader
2. Character-specific content generation
3. Trending audio integration
4. Auto-hashtag research
5. Multi-account management

### Phase 4 — Optimization (Month 3+)
1. ML-based clip ranking (train on actual TikTok performance)
2. Automated A/B testing of captions/hashtags
3. Cross-character crossovers
4. Merchandise push from viral clips

---

## 14. Next Steps (This Week)

**Immediate actions (before building anything):**
1. Test yt-dlp heatmap extraction on 3 existing videos — verify it returns data
2. Create TikTok account for UK CLIPS (or verify existing)
3. Manually extract 3 peak clips from Miniminter Mega and post them
4. Compare engagement: does the peak moment format actually work?

**If validation succeeds, start building:**
1. `peak_detector.py` (2 hours)
2. `clip_extractor.py` (2 hours)
3. `vertical_formatter.py` (2 hours)
4. First end-to-end automated run (1 hour)
5. Post 5 clips per day for a week, track results

**Success criteria for Phase 1:**
- At least 1 clip exceeds 5,000 views (proves format works)
- At least 3 clips exceed 1,000 views (proves consistency)
- Measurable YouTube subscriber growth attributed to TikTok traffic (even 10 subs is a win)

---

## Document Owner
- **Created:** 2026-04-14
- **Primary Audience:** Anas (project owner)
- **Related Docs:**
  - `BUSINESS_PLAN.md` — Overall business strategy
  - `EXECUTION_PLAN.xlsx` — Day-by-day schedule
  - `DAILY_EXECUTION_PLAN.xlsx` — Granular monthly plans
  - `channels.yaml` — Channel configurations
- **Review Cadence:** Weekly for first month, monthly after
