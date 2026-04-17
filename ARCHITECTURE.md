# Architecture

High-level map of how the pipeline fits together. Dive into the modules for the detail.

---

## Context

This is a multi-channel YouTube automation pipeline. It scrapes / fetches source content,
narrates or reacts to it with an AI character, and uploads the result to YouTube + TikTok
on schedules.

One repo, one Python package (`peanut_reacts`), one scheduler daemon that orchestrates
per-channel content jobs.

---

## 10,000-foot view

```
          ┌──────────────────────────────────────────────────┐
          │   Hetzner VPS — peanut-reacts daemon             │
          │   (systemd timer → scheduler.runner)             │
          └───────────────────┬──────────────────────────────┘
                              │  reads channels.yaml
                              ▼
                ┌─────────────────────────────┐
                │  PipelineEngine             │
                │  (scheduler/engine.py)      │
                └─────────────────────────────┘
                              │  dispatches per source_type
        ┌─────────────────┬───┴────┬─────────────────┬────────────┐
        ▼                 ▼        ▼                 ▼            ▼
  playlist        hasan_archive  reddit           ambient       lofi
  (peanut reacts) (HasanAbi)    (Cashew narr.)   (soundscapes)  (24/7 stream)
        │                 │        │                 │            │
        └────────┬────────┴────────┴────────┬────────┴────────────┘
                 ▼                          ▼
        ┌────────────────┐        ┌──────────────────┐
        │ Compositor     │        │ Uploader         │
        │ (reaction_video│        │ (youtube_upload, │
        │  vertical_story│        │  tiktok_uploader)│
        └────────────────┘        └──────────────────┘
                 │                          │
                 └──────────────┬───────────┘
                                ▼
                     production/<channel>/*.mp4
                     + youtube / tiktok uploads
                     + pipeline.db (state)
```

---

## Source layout

```
src/peanut_reacts/
├── analysis/       # Comment extraction, diarization, topic segmentation
├── ambient/        # Ambient pipeline (rain/fireplace/cafe soundscapes)
├── character/      # TTS (Edge + ElevenLabs), reaction generator, animators
│                   # renderers: cartoon, peanut_animator, wav2lip_sync,
│                   # sadtalker_sync (GPU, local-only)
├── clips/          # Caption generator, reaction clip utilities
├── compilations/   # Multi-video compilations
├── compositing/    # reaction_video (horizontal), vertical_story (9:16 Shorts),
│                   # layout, karaoke-style ASS subtitle generator
├── core/           # Config loader (.env), logging, ffmpeg helpers, SRT
├── decoded/        # Decoded channel (dense reactions format)
├── download/       # yt-dlp wrappers, comment/subtitle/playlist download
├── learning/       # Competitor tracking, learning loop for title/thumbnail improvements
├── reddit/         # NEW — local-only Reddit short pipeline with SadTalker;
│                   # scraper, story_processor, db, pipeline
├── scheduler/      # The cloud daemon:
│                   # - runner.py: entry point, argparse
│                   # - engine.py: cron dispatcher
│                   # - channel_config.py: channels.yaml loader
│                   # - pipelines.py: source_type → pipeline dispatcher
│                   # - reddit_pipeline.py: Cashew narrator (cloud-ready)
│                   # - hasan_pipeline.py: HasanAbi archive
│                   # - shorts_pipeline.py: Short extraction from long-form
│                   # - stats_fetcher.py, notify.py, status_exporter.py
├── shorts/         # Shorts-specific editor + uploader glue
└── upload/         # YouTube + TikTok auth/upload clients
```

External infrastructure:
```
deploy/             # Dockerfile, docker-compose.yml, setup-vps.sh, remote_deploy.py
docs/               # GitHub Pages dashboard (data.json + index.html)
scripts/            # One-off scripts (build_execution_plan, test_e2e, etc.)
tests/              # Unit + integration tests
channels.yaml       # Per-channel config (cron schedule, credentials, persona)
pipeline.db         # Job state SQLite (scheduler writes)
shorts_queue.db     # Short-form upload queue
learning.db         # Feedback signals for title/thumbnail optimization
reddit_queue.db     # Reddit-shorts pipeline state (local SadTalker path)
```

---

## Key flows

### Daily Reddit long-form (cloud)

```
systemd timer @08:00
  → scheduler.runner --run-once reddit_stories
    → PipelineEngine.run_once("reddit_stories")
      → pipelines.run_reddit_pipeline(channel, settings, db)
        → scheduler.reddit_pipeline.run_reddit_pipeline(...)
          1. fetch_top_stories(subreddits)          [PRAW OAuth or Scrape.do]
          2. generate_narration_script(llm)          [DeepSeek]
          3. script_to_tts(edge_tts)                 [captures word timings]
          4. build_tiktok_ass(chunks)                [karaoke subtitles]
          5. concat_audio(chunks)
          6. get_background_gameplay(duration)
          7. composite_reddit_video(subtitles_ass)   [ffmpeg + libass]
          8. upload via YouTubeUploader (if OAuth present)
```

### Peanut reaction (Sidemen, local GPU)

```
reaction_video.ReactionPipeline.run(job)
  1. Load/transcribe video
  2. Extract comments (optional)
  3. Generate reaction lines (LLM)
  4. TTS each line (Edge or ElevenLabs)
  5. Render peanut segments — one of:
     - cartoon_renderer  (CPU, PNG emotions, zoom pulse)
     - wav2lip_sync      (GPU, real lip-sync, deprecated)
     - sadtalker_sync    (GPU, current premium — face-landmark model)
     - burnt_peanut      (CPU, 2-image speaking/idle switch)
  6. _build_reaction_audio (TTS mixed at correct offsets)
  7. _final_composite (facecam layout, original ducking, chroma-key)
```

---

## State stores

| DB | Written by | Used for |
|---|---|---|
| `pipeline.db` | scheduler.db.PipelineDB | Per-channel job status (ran / failed / error_msg) |
| `shorts_queue.db` | shorts_pipeline | Queue of ready Shorts awaiting upload |
| `learning.db` | learning.loop | Title/thumbnail signals to feed generator |
| `reddit_queue.db` | reddit/db.StoryDB | Reddit-shorts pipeline (local SadTalker path) |

All SQLite. Each pipeline owns its own DB — no shared schema.

---

## Credentials

Everything via `.env` at repo root. Dev machine has one; VPS gets a separate upload
via sftp. Never committed.

| Env var | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | LLM provider |
| `ELEVENLABS_API_KEY` | Premium TTS |
| `YOUTUBE_API_KEY` | Data API (stats, comments) |
| `YOUTUBE_CLIENT_SECRETS` | Path to OAuth client_secret.json |
| `HF_TOKEN` | Diarization model access |
| `YTDLP_COOKIES_FILE` | yt-dlp auth cookies |
| `REDDIT_CLIENT_ID` / `_SECRET` / `_USER_AGENT` | PRAW OAuth (when approved) |
| `SCRAPEDO_API_TOKEN` | Reddit scraping via Scrape.do (alt path) |

OAuth tokens per channel live in `~/.peanut_reacts/tokens/<channel_id>.json` on
the machine that will do the upload.

---

## Deployment

- **Cloud daemon** (Hetzner): `/opt/peanut-reacts/` — git clone, `.venv`, systemd timer
- **Local dev** (Windows): Git Bash + Anaconda envs for `sadtalker` (Py 3.10) and `talking_head` (Py 3.11)
- **GitHub Pages**: `docs/` folder serves the dashboard at
  `https://anaspine2000.github.io/peanut-reacts-pipeline/`

See [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md) and [deploy/](deploy/).

---

## Non-obvious dependencies

- **FFmpeg 4.2+ with libass support** — for karaoke subtitle burn (`subtitles=` filter)
- **FFmpeg 5.0+** optional — needed for `gradients` filter in vertical compositor
- **NVENC** — enabled automatically if `h264_nvenc` encoder is present (RTX 4070 Laptop: yes)
- **SadTalker conda env** — local-only, `C:\Users\anasm\anaconda3\envs\sadtalker\`; paths in
  env vars `SADTALKER_PYTHON` and `SADTALKER_HOME`
- **Docker** — optional; the cloud deployment uses bare Python + systemd currently

---

## Testing

See [CONTRIBUTING.md](CONTRIBUTING.md#testing). Unit tests run on Python 3.10/3.11/3.12 in CI.
Integration tests run with mocked external services — Reddit, DeepSeek, ElevenLabs, YouTube
APIs all mocked so CI never costs real money or hits rate limits.

GPU tests (`-m gpu`) only run locally on the dev machine.
