# Operating the pipeline — `peanut` CLI

One command runs everything. No Claude required.

```
peanut doctor                  diagnose config + credentials
peanut channels                list channels, schedules, enabled state
peanut run <channel>           produce + upload one channel NOW
peanut run --all               run every enabled channel
peanut run <channel> --dry-run produce but DON'T upload
peanut preview <channel>       render locally, no upload, print the file path
peanut status                  recent pipeline jobs
peanut deploy                  git push + sync the VPS + restart its scheduler
peanut logs                    tail the VPS scheduler log
```

Run it as `peanut <cmd>` (Windows: `peanut.bat` wrapper; Linux/macOS:
`./peanut`) or directly as `python scripts/peanut.py <cmd>`.

## What a "run" does

`peanut run reddit_stories` executes the FULL pipeline for that
channel, end to end:

```
fetch source  ->  generate script  ->  TTS voice  ->  composite video
   ->  dynamic narrator avatar  ->  SFX + intro + outro  ->  upload
```

Download is included — the pipeline fetches its own source (Reddit
posts, playlist videos, etc.). `--dry-run` does everything except the
upload, and forces privacy to private if anything slips through.

## First-time setup checklist

Run `peanut doctor` — it tells you exactly what's missing. The things
it checks:

| check | how to fix if it fails |
|---|---|
| ffmpeg / ffprobe / yt-dlp / git | install them, put on PATH |
| `peanut_reacts` importable | `pip install -e .` from repo root |
| channels.yaml parses | fix YAML syntax |
| `DEEPSEEK_API_KEY` | add to `.env` — platform.deepseek.com |
| `SCRAPEDO_API_TOKEN` | add to `.env` — dashboard.scrape.do (cloud Reddit fetch) |
| `client_secret.json` | the Google OAuth app config, in `~/.peanut_reacts/` |
| OAuth tokens | run `scripts/oauth_setup_network.py` |
| Scrape.do token LIVE | if 401: rotate it on the Scrape.do dashboard, update `.env` |
| VPS deploy creds | set `PEANUT_VPS_HOST` + `PEANUT_VPS_PASSWORD` or `PEANUT_VPS_KEY` |

## Daily operation

The pipeline runs itself — the VPS scheduler + GitHub Actions fire
the channels on cron (see `peanut channels` for the schedule). You
don't normally need to do anything.

Manual use cases for the CLI:

- **Check a channel before it goes public**: `peanut preview <channel>`
  renders it locally, no upload — open the printed file path and watch.
- **Fire a channel off-schedule**: `peanut run <channel>`.
- **Re-run everything**: `peanut run --all`.
- **Ship a code change**: commit, then `peanut deploy` — pushes to
  GitHub and syncs+restarts the VPS scheduler.
- **See what the VPS has been doing**: `peanut logs`.
- **See recent local runs**: `peanut status`.

## Environment variables

The CLI reads `.env` at the repo root automatically. Keys:

```
DEEPSEEK_API_KEY=...        # required — LLM script generation
SCRAPEDO_API_TOKEN=...      # required for cloud — Reddit fetch proxy
GROQ_API_KEY=...            # optional — vision fallback
REPLICATE_API_TOKEN=...     # optional — cloud GPU vision
ELEVENLABS_API_KEY=...      # optional — expressive TTS
PEANUT_VPS_HOST=...         # for `peanut deploy` / `peanut logs`
PEANUT_VPS_PASSWORD=...     # OR PEANUT_VPS_KEY=path/to/ssh/key
```

## Where things live

```
channels.yaml                 channel definitions + schedules
src/peanut_reacts/             the pipeline package
scripts/peanut.py              this CLI
scripts/fetch_narrator_avatar.py   regenerate the avatar (--seed to change look)
scripts/oauth_setup_network.py     authorize new YouTube channels
deploy/vps_sync.py             git pull + restart on the VPS
pipeline.db                    local job history (SQLite)
production/<channel>/          rendered output per channel
~/.peanut_reacts/              OAuth tokens + client secret
```

## The autonomous loop (no human, no Claude)

1. **VPS** runs `peanut_reacts.scheduler.runner` as a systemd service.
   It fires each VPS-env channel on its cron schedule.
2. **GitHub Actions** (`.github/workflows/shorts-daily.yml`) fires the
   Actions-env channels on cron.
3. Both call the same `run_channel_pipeline` the CLI's `run` uses.
4. New code reaches the VPS when you run `peanut deploy`.

That's the whole system. `peanut doctor` first, fix any `[FAIL]`,
then it runs itself.
