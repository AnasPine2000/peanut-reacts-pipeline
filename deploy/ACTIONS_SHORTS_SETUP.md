# GitHub Actions Shorts Pipeline — Setup Guide

One-time setup to enable the `.github/workflows/shorts-daily.yml`
workflow. After this, the workflow fires at **15:00 UTC** and **20:00
UTC** daily, uploading fresh Shorts to YouTube from GitHub-hosted
runners. Zero server cost.

## Architecture recap

```
  Hetzner VPS          GitHub Actions            GitHub Actions
  08:00 UTC cron       15:00 UTC cron            20:00 UTC cron
       │                      │                        │
       └──────── same run_channel_pipeline() entry ────┘
                             │
                     YouTube Data API upload
                             │
                    Peanut / Cashew channel
```

The VPS and Actions both call `run_channel_pipeline(reddit_stories, ...)`.
Each picks the current top Reddit posts at its cron tick — the three daily
runs naturally get different top posts because Reddit rankings shift
hour-to-hour.

## Step 1 — Add secrets to the repo

Go to **Settings → Secrets and variables → Actions → New repository secret**
and add each of these. The workflow will fail preflight without the three
marked REQUIRED.

| Secret name | Required? | What it is | How to get |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | **REQUIRED** | Verdict LLM | `platform.deepseek.com/api_keys` |
| `YT_CLIENT_SECRET_B64` | **REQUIRED** | OAuth app config | `base64 -w0 ~/.peanut_reacts/client_secret.json` |
| `YT_TOKEN_B64` | **REQUIRED** | Refresh token for upload | `base64 -w0 ~/.peanut_reacts/reddit_stories_token.json` |
| `REPLICATE_API_TOKEN` | recommended | Cloud vision (C2) — no GPU on runners otherwise | `replicate.com/account/api-tokens` |
| `GROQ_API_KEY` | optional | Vision fallback (rate-limited) | `console.groq.com/keys` |
| `SCRAPE_DO_TOKEN` | optional | Reddit scraping (some pipelines) | `dashboard.scrape.do` |
| `ELEVENLABS_API_KEY` | optional | Expressive voice (otherwise Edge TTS) | `elevenlabs.io/api` |
| `DISCORD_WEBHOOK_URL` | optional | Failure notifications | Discord channel webhook settings |

**Note on `REPLICATE_API_TOKEN`**: the `reddit_stories` channel doesn't
use vision (it's pure Reddit text → Cashew narration → TTS), so
Replicate isn't required for it. But `peanut_tiktok_reacts` and
anything `live_reaction` do vision on every clip, and GitHub runners
have no GPU — without Replicate those fall through to rate-limited
Groq and often produce generic "A funny clip" descriptions. See
`deploy/REMOTE_VISION_SETUP.md` for the full routing logic.

### How to base64-encode the OAuth blobs

**Linux / macOS / WSL:**
```bash
base64 -w0 ~/.peanut_reacts/client_secret.json       # paste into YT_CLIENT_SECRET_B64
base64 -w0 ~/.peanut_reacts/reddit_stories_token.json  # paste into YT_TOKEN_B64
```

**Windows PowerShell:**
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("$HOME\.peanut_reacts\client_secret.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("$HOME\.peanut_reacts\reddit_stories_token.json"))
```

Copy the single-line output and paste it into the secret value field. The
`-w0` / no-linewrap is important — linebreaks break the base64 decode on
the runner.

### If you don't have `reddit_stories_token.json` yet

The token for a channel is created by the first OAuth run on your local
machine. Easiest path: just re-use the Peanut Reacts YouTube token that
already exists (reddit_stories channel config shares the Peanut channel
for now — see commit `6157cc1`). Use
`~/.peanut_reacts/peanut_tiktok_reacts_token.json` instead.

## Step 2 — First manual run (dry mode)

The workflow defaults to `dry_run=true`, which forces every upload to
`privacy=private` so you can verify the pipeline end-to-end without
risking a public bad first post.

1. Go to **Actions → shorts-daily → Run workflow**
2. Pick channel: `reddit_stories`
3. dry_run: `true`
4. Click **Run workflow**
5. Watch the job. Expected duration on a cold cache: 8-12 min. Warm cache
   (subsequent runs): 3-5 min.
6. When it finishes, check your YouTube Studio for a new PRIVATE upload.
   Verify the video plays, audio is clear, title looks right. If anything
   is off, don't proceed to live mode until fixed.

## Step 3 — Flip to live

Once the dry run looks good:

1. Run workflow again, this time with `dry_run=false` — that overrides
   the channel's configured privacy for this one run. Upload goes public.
2. Verify the public upload looks clean on the channel.
3. The scheduled cron already uses `dry_run` defaulting to `true` from
   the dispatch input. To make the cron actually go live, edit
   `.github/workflows/shorts-daily.yml`:

   ```yaml
   env:
     DRY_RUN: ${{ github.event.inputs.dry_run || 'false' }}  # was 'true'
   ```

   Commit that change and the next 15:00 UTC cron will upload public.

## Step 4 — Observability

- **Workflow logs**: Actions tab → shorts-daily → click any run.
- **Artifacts**: Each run uploads `pipeline.db` + `pipeline_status.json`
  as a 7-day artifact so you can inspect what the pipeline saw.
- **Discord alerts**: Add `DISCORD_WEBHOOK_URL` secret and failed runs
  ping the channel with a link to the logs.

## Maintenance

### Dependencies drift

`requirements-actions.txt` is the slim install list. If you add a channel
whose pipeline imports `sentence-transformers` or `whisper`, the Actions
run crashes with `ImportError`. Fix: add the package to
`requirements-actions.txt`. Keep the list tight — each package adds
runner seconds.

### OAuth token expiry

Google refresh tokens don't typically expire unless revoked or unused for
6 months. If the workflow starts failing with auth errors, re-do the
OAuth dance on your local machine and re-upload the new base64 blob to
the `YT_TOKEN_B64` secret.

### Rate / quota

YouTube Data API default quota is 10k units/day. Each upload costs
~1600 units. 3 uploads/day × 1600 = 4800 units. Headroom for ~6 uploads/day
before quota concerns. If we ever scale past that, request a quota
increase in Google Cloud Console.

### Budget

GitHub Actions free tier on a PUBLIC repo is unlimited. On private repos
it's 2000 min/month. A Shorts run is 8-12 min cold, 3-5 min warm. Two
crons/day × 30 days × 5 min ≈ 300 min/month — safe even if the repo
were private.

## Troubleshooting

**Job fails at `Preflight — secrets present?`** — secret names in the
workflow YAML don't match what you added in Settings. Check spelling /
case. Secrets are case-sensitive.

**Pipeline crashes with "No module named X"** — add `X` to
`requirements-actions.txt` and push.

**Upload fails with 401 Unauthorized** — the `YT_TOKEN_B64` secret is
stale. Re-base64 a fresh token from your local machine.

**Upload succeeds but video gets auto-deleted** — same content-policy
issue we hit with Hasan. The content itself is the problem, not the
workflow. Shorter clips + transformative titles survive; raw chunks
don't. See `BUSINESS_PLAN.md` and the survival-audit commit notes.
