# Execution Plan V2 — $20k/month in 6 Months

**Updated:** 2026-04-17  •  **Day 1:** 2026-04-17  •  **Day 180:** 2026-10-13
**Companion:** [EXECUTION_PLAN_V2.xlsx](EXECUTION_PLAN_V2.xlsx) — day-by-day schedule + revenue model + budget

---

## TL;DR

Build a **9-channel portfolio** shipping daily across 3 verticals (Reddit narration, character reaction, ambient passive). Bet 50% of attention on Reddit Stories (Cashew) — it's the only niche where peers at our subscriber tier reliably clear $4k/mo. Diversify AdSense with sponsorships + Patreon + memberships + merch starting Month 4.

**Target**: $20,000/month gross, $18,000+ net, by Day 180.

**Budget**: ~$650 total spend across 6 months. Everything else is your time.

**Honest base rate**: Most YouTube automation attempts fail. ~3% hit $10k/mo at Month 6. This plan is aimed at that top decile. Realistic P50 outcome: $8-12k/mo at Day 180, target hit at Day 270-300. Plan for target; be happy with P50.

---

## Why this plan looks different from BUSINESS_PLAN.md (v1)

| | v1 (existing) | v2 (this) |
|---|---|---|
| Target | $1.5-3k/mo Year 1 | $20k/mo Month 6 |
| Channel count | 2-3 | **9** |
| Lead channel | Peanut Reacts (reaction) | **Reddit Stories (narration)** |
| Diversification | Ads + memberships only | Ads + sponsorships + Patreon + memberships + merch + affiliates |
| Daily cadence | Not specified | **Required on 4 flagship channels** |
| Cloud deployment | Future | Happening in Week 1 (infrastructure already there) |

V1 was conservative. V2 assumes:
- Reddit Stories works — we're following PRIVATE DIARY / Reddit Tales' proven format
- You can sustain 25-40 hrs/week for 6 months
- AdSense alone caps at ~$15k/mo; non-ad revenue does the last-mile push

If any of those break, the plan adapts (see Risk Register below).

---

## The revenue math to $20k/month

### Channel portfolio (month 6 target)

| Channel | Vertical | M6 views/mo | RPM | Monthly $ |
|---|---|---|---|---|
| Reddit Stories (Cashew) | Reddit long-form | 700k | $6 | **$4,200** |
| Reddit AITA (Pistachio) | Reddit long-form | 350k | $5 | **$2,200** |
| Peanut Reacts (Sidemen) | Reaction long-form | 400k | $4 | **$1,600** |
| Peanut Reacts (KSI) | Reaction long-form | 300k | $4 | **$1,600** |
| HasanAbi Archive | Commentary long-form | 500k | $6 | **$3,000** |
| Reddit Shorts | Reddit Shorts | 3M | $0.30 | **$900** |
| Peanut TikTok Reacts | Reaction Shorts | 1.6M | $0.30 | **$1,200** |
| Cozy Ambient | Ambient long-form | 800k | $2 | **$1,600** |
| Lofi Stream | Ambient 24/7 | 300k | $2 | **$800** |
| **Ad revenue total** | | **~7.9M** | avg $5.2 | **$17,100** |

### Plus diversification (month 6)

| Source | Amount | Notes |
|---|---|---|
| Channel memberships (~$2-5/mo × 300 fans) | $1,500 | Unlocks at 1k subs |
| Sponsorships (2-3 channels × $1,500/video × 4 videos) | $4,000 | Unlocks at 50k+ subs |
| Patreon | $600 | 150 fans × $4 avg |
| Affiliate links | $500 | Audible, Skillshare, etc. |
| Super Thanks / Super Chat | $400 | Live premieres mostly |
| **Diversified total** | **$7,000** | |

**Gross month-6 projection**: **~$24,000**
**Net** (after $180/mo operating costs): ~$23,800
**Target buffer**: $20,000 is P60 outcome; actual range P25 $9,000 → P80 $35,000.

---

## Why Reddit Stories as the flagship

We looked at what actually makes money in faceless YouTube (April 2026 research). Top channel "Am I the Jerk?" clears **$31k/month** but uses professional voice actors — not our play. The **AI-narrated tier** peers are the realistic reference:

| Channel | Subs | Monthly | What they do |
|---|---|---|---|
| PRIVATE DIARY | 750K | **$4,335** | AI narration, story animation |
| Reddit Tales | 122K | $2,016 | AI narration, gameplay bg |
| Lost Genre Stories | 100K | $1,218 | AI narration, high-quality script rewrites |

Why this niche wins:
- **RPM $8-18** — among the highest on YouTube (B2B/education advertiser demand)
- **High retention** — viewers stay for the ending (up-votes tell us the hook works)
- **Pre-validated content** — every post already passed a human quality filter (upvotes)
- **Evergreen** — old videos keep earning years later (unlike reaction clips that decay with the trend)

---

## Phase structure

| Phase | Days | Focus | Exit criteria |
|---|---|---|---|
| 1. Foundation | 1-14 | Unblock Reddit scraping, ship first video, thumbnails, backgrounds | First Reddit video hits 1k views |
| 2. First Channel Live | 15-28 | Daily Reddit uploads, A/B titles + thumbnails | Reddit Stories channel hits YPP (1k subs, 4k watch hrs) |
| 3. Production Scaling | 29-60 | Launch Peanut Sidemen + HasanAbi, Shorts extraction | 3 channels live + combined $500/mo |
| 4. Portfolio Build | 61-90 | Launch KSI + TikTok Reacts + Cozy Ambient + Pistachio AITA | 7 channels live + combined $3k/mo |
| 5. Scale | 91-120 | Kill losers, double-down on winners, sponsorship outreach | ≥1 channel at 50k subs |
| 6. Monetization Diversification | 121-150 | Patreon, memberships, affiliates, merch | Non-AdSense revenue > $3k/mo |
| 7. Target Hit | 151-180 | Retention work, hiring, second-wave diversification | $20k/mo gross |

---

## Week 1 — the critical path

Nothing else ships until these block is clear. See Daily Schedule sheet for Days 1-14, but the summary:

| Day | Action |
|---|---|
| **Day 1** (today) | Sign up Scrape.do free tier (or get Reddit OAuth approved). **You must do this — I can't.** |
| Day 2 | Wire scraping API into `scheduler/reddit_pipeline.py`. *(Claude does this.)* |
| Day 3 | Deploy to VPS, run `--run-once reddit_stories`. First video produced. |
| Day 4 | YouTube OAuth for the reddit_stories channel. **You do the browser flow.** |
| Day 5 | First real upload. Algorithm starts. |
| Day 6 | Fix anything the first upload broke. |
| Day 7 | Daily cron installed. Pipeline fully autonomous. |

**Week 1 blocker visibility**: 2 things need your browser — Scrape.do signup + YouTube OAuth. Maybe 20 minutes of your time total.

---

## Budget breakdown

Total 6-month spend: **$650**. Dwarfed by one month of Fal.ai OmniHuman we'd rejected.

| Line item | Monthly range | Notes |
|---|---|---|
| Hetzner VPS | $16 | Already provisioned |
| Reddit scraping API | $0-30 | Free tier until M4, then $30/mo |
| ElevenLabs TTS | $0-99 | Free during MVP → Creator at M5 |
| DeepSeek LLM | $2-15 | Cheapest per-token model that works |
| YT tools (TubeBuddy, vidIQ) | $0-30 | Optional, M3+ |
| Stock assets (Envato etc.) | $0-20 | Optional, M4+ |
| Misc | $2-15 | Music licensing, testing |

**Zero GPU cost** — SadTalker runs on your local laptop. If we ever want Hallo2 quality, add $30-80/mo GPU cloud at M4+.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Reddit shuts down scraping further | Medium | High | Option B: switch to alternative story sources (Tumblr, BORU archives, forum content) |
| YouTube flags content as "inauthentic" | Medium | Critical | Vary thumbnail styles, pacing, intros across videos; LLM paraphrase every Reddit story; no cross-channel template reuse |
| ElevenLabs quota + pricing | Low | Medium | Edge TTS fallback (free, already wired) — good enough at scale |
| Any single channel hits flat growth | High | Low | Kill it at M3/M4 retros; portfolio design assumes 2-3 will flop |
| You burn out on 30hr/week cadence | Medium | High | VA hiring built into Phase 7; part of plan to de-load you by M5 |
| Niche saturation (more AI Reddit channels launch) | Medium | Medium | Differentiate via character (Cashew persona), cross-posting to TikTok, better writing quality |
| Sponsorship market flat | Low-Medium | Medium | Non-AdSense targets (memberships, merch, Patreon) don't depend on sponsor market |

---

## What "done" looks like at Day 180

- 9 channels with 30 days of scheduled content each
- One VA handling comment management + research (20 hrs/week)
- Weekly automated reports driving the next week's priorities
- $20k+ gross monthly, operating costs under $200
- 2-3 sponsorship contracts signed (not one-offs)
- Patreon at 100-300 supporters
- Your time: down to ~15 hrs/week of strategy + creative direction

---

## Supporting artifacts

- **[EXECUTION_PLAN_V2.xlsx](EXECUTION_PLAN_V2.xlsx)** — full 180-day schedule, revenue model, budget
- [BUSINESS_PLAN.md](BUSINESS_PLAN.md) — v1 business plan (still relevant for research + policy)
- [CHANNEL_SETUP_GUIDE.md](CHANNEL_SETUP_GUIDE.md) — per-channel YouTube setup
- [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md) — VPS deployment guide
- [SHORTS_TIKTOK_STRATEGY.md](SHORTS_TIKTOK_STRATEGY.md) — Shorts format research

---

*Plan maintained in the repo. Update this file as Phase retros land and assumptions shift.*
