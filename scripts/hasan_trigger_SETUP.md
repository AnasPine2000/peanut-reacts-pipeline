# HasanAbi Stream Auto-Trigger System

A two-sided system that automatically processes every new HasanAbi Archive
stream the moment it drops — without any manual kickoff.

## Architecture

```
┌────────────────────────────────┐        ┌────────────────────────────────┐
│  GitHub Actions (always on)    │  git   │  Residential PC (when on)      │
│                                │ push   │                                │
│  Every 30 min cron:            │  ───>  │  Windows Task Scheduler:       │
│   watch_hasan_source.py        │        │   every 15 min:                │
│   - yt-dlp latest 5 from       │        │   process_hasan_queue.py       │
│     @HasanAbiArchivefan0       │        │   - git pull                   │
│   - diff against state         │        │   - filter vs pipeline.db      │
│   - new? → append to queue     │        │   - fire pipeline on newest    │
│   - commit back to master      │        │   - move pending → processed   │
│                                │        │   - git push                   │
└────────────────────────────────┘        └────────────────────────────────┘

          data/hasan_watcher_state.json  (all video_ids ever seen)
          data/hasan_trigger_queue.json  (pending + processed lists)
```

## What triggers what

- **Source drops a stream** → within 30 min, GitHub Actions notices and
  adds an entry to `data/hasan_trigger_queue.json` (pushed to master).
- **PC is online** → within 15 min of that commit landing, the processor
  pulls, reads the queue, fires `run_channel_pipeline("hasanabi_archive")`
  which downloads the stream, segments it by topic, uploads each segment,
  and extracts Shorts.
- **PC is offline** → queue keeps growing in GitHub. When the PC comes
  back, the processor drains one entry per run (newest first), so no
  streams are lost.

## One-time setup on the local PC

### 1. Verify scripts work

Both scripts are in `scripts/`. Test them manually:

```powershell
# Dry-run the processor — shows the queue but doesn't fire anything
PYTHONPATH=src python scripts/process_hasan_queue.py --dry-run
```

### 2. Register with Windows Task Scheduler

Open Task Scheduler → Create Task:

- **General**
  - Name: `HasanAbi Queue Processor`
  - Run whether user is logged on or not: yes (so it runs unattended)
  - Run with highest privileges: yes
- **Triggers**
  - Daily, repeat every **15 minutes** for a duration of **1 day**
  - Enabled
- **Actions**
  - Start a program: `cmd.exe`
  - Arguments: `/c cd /d "C:\Users\anasm\Videos\4K Video Downloader+" && PYTHONPATH=src python scripts/process_hasan_queue.py >> data/hasan_processor.log 2>&1`
- **Conditions**
  - Start only if network available: yes
  - Stop if battery: off (keep running on AC and battery — the GPU work
    is short vs the download so battery drain is tolerable)
- **Settings**
  - If task is already running: **Do not start a new instance** (we rely
    on the PID-file lock anyway, but defence in depth)
  - Stop task if runs longer than: **3 hours** (average stream is 4-5 h,
    but Task Scheduler's timeout just aborts *this* Task invocation —
    the next one picks up naturally if an earlier run was still going)

### 3. Pull latest to test the queue end-to-end

```powershell
git pull
PYTHONPATH=src python scripts/process_hasan_queue.py --dry-run --no-pull
```

Should print `Queue is empty — nothing to do.` on a fresh state.

## One-time setup on GitHub

Nothing to do — the workflow is committed and runs automatically.

### Manually poke the watcher

- Go to the repo on GitHub → **Actions** → **hasan-stream-watcher** →
  **Run workflow**. This fires a poll immediately instead of waiting
  for the next 30-min tick. Useful after first install to populate the
  state file so the first real cron run doesn't dump 5 entries.

## Manual override cheats

- **Process a specific video right now, bypassing the queue entirely**:
  `PYTHONPATH=src python -c "from peanut_reacts.scheduler.channel_config import load_channels; from peanut_reacts.scheduler.db import PipelineDB; from peanut_reacts.scheduler.pipelines import run_channel_pipeline; cfg = load_channels('channels.yaml'); ch = next(c for c in cfg.channels if c.id == 'hasanabi_archive'); run_channel_pipeline(ch, cfg.settings, PipelineDB('pipeline.db'))"`
- **Clear a stuck queue entry**: edit `data/hasan_trigger_queue.json`
  and remove the entry from `pending`. Commit + push.
- **Force the watcher to re-see already-processed videos**: wipe
  `data/hasan_watcher_state.json` (it'll rebuild on next poll). The
  processor's DB filter will immediately skip any entries that are
  already `done` in `pipeline.db`.

## Observability

- GitHub Actions tab → watcher run history + logs
- Local: `data/hasan_processor.log` (appended to by the Task Scheduler
  invocation)
- `pipeline.db` jobs table records every run start + completion
- `data/hasan_trigger_queue.json:processed[]` is an audit log of every
  entry that moved through the system
