# Local execution on Windows (residential IP)

These files run the YouTube-scraping channels on the user's local PC
instead of the Hetzner VPS, because YouTube bot-detects datacenter IP
ranges.

## Files

| File | Purpose |
|---|---|
| `run_local_channel.bat` | One-off CLI wrapper around `python -m peanut_reacts.scheduler.runner --run-once <channel>`. Sets `PEANUT_SCHEDULER_ENV=local` + logs to `logs/local_<channel>_<date>.log`. |
| `install_scheduled_tasks.ps1` | Registers Windows Task Scheduler tasks for each `execution_env: local` channel. |

## Setup (one time)

1. Open **PowerShell** (regular, no admin needed).
2. Navigate to the repo:
   ```powershell
   cd "C:\Users\anasm\Videos\4K Video Downloader+"
   ```
3. Register the tasks:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\deploy\windows\install_scheduled_tasks.ps1
   ```
4. Confirm the 3 tasks are registered:
   ```powershell
   Get-ScheduledTask -TaskName "Peanut-*"
   ```

## Triggering manually

To run one channel immediately (handy for testing):

```powershell
Start-ScheduledTask -TaskName Peanut-hasanabi_archive
```

Or directly without Task Scheduler:

```powershell
.\deploy\windows\run_local_channel.bat hasanabi_archive
```

## Logs

Each task appends to a daily-rotated log:

```powershell
Get-Content "logs\local_hasanabi_archive_*.log" -Tail 50 -Wait
```

Also visible in Task Scheduler's GUI: **Task Scheduler → Task Scheduler
Library → Peanut-\* → History** tab.

## Schedules (all UTC in `channels.yaml`, converted to local time by the PowerShell installer)

| Channel | UTC cron | UTC time |
|---|---|---|
| `uk_clips` | `0 9 * * 1,3,5` | Mon/Wed/Fri 09:00 |
| `hasanabi_archive` | `0 10 * * *` | Daily 10:00 |
| `peanut_ksi` | `0 14 * * 2,4,6` | Tue/Thu/Sat 14:00 |

If your PC was off at the scheduled time, the task fires at the next
wake (`StartWhenAvailable=True`).

## Uninstall

```powershell
Get-ScheduledTask -TaskName "Peanut-*" | Unregister-ScheduledTask -Confirm:$false
```
