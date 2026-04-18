<#
.SYNOPSIS
    Register Windows Task Scheduler tasks for the local-execution channels.

.DESCRIPTION
    Creates three scheduled tasks that fire the corresponding `--run-once`
    pipeline at the cron time defined in channels.yaml. All times are UTC
    to match the VPS daemon's TZ setting.

    Task Scheduler's "Run task as soon as possible after a scheduled start
    is missed" option is enabled — so if the PC was off at the fire time,
    the task runs the next time the PC is available.

    Each task runs as the current user (no admin required for user-context
    tasks). Python needs to be on PATH.

.NOTES
    Run this in a regular PowerShell window. Re-running is safe — it
    replaces any existing task with the same name.

    To remove later:
        Get-ScheduledTask -TaskName "Peanut-*" | Unregister-ScheduledTask -Confirm:$false
#>

param(
    [string] $RepoRoot = "C:\Users\anasm\Videos\4K Video Downloader+"
)

$ErrorActionPreference = "Stop"

$Wrapper = Join-Path $RepoRoot "deploy\windows\run_local_channel.bat"
if (-not (Test-Path $Wrapper)) {
    Write-Error "Wrapper not found: $Wrapper"
    exit 1
}

# ── Tasks to register — matches `execution_env: local` channels in channels.yaml
#    Times are UTC; Task Scheduler converts to local for display.
$Tasks = @(
    @{
        Name     = "Peanut-uk_clips"
        Channel  = "uk_clips"
        Schedule = "0 9 * * 1,3,5"            # Mon/Wed/Fri 09:00 UTC
        DaysOfWeek = @("Monday", "Wednesday", "Friday")
        TimeUtc  = "09:00"
    },
    @{
        Name     = "Peanut-hasanabi_archive"
        Channel  = "hasanabi_archive"
        Schedule = "0 10 * * *"               # Daily 10:00 UTC
        DaysOfWeek = @("Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday")
        TimeUtc  = "10:00"
    },
    @{
        Name     = "Peanut-peanut_ksi"
        Channel  = "peanut_ksi"
        Schedule = "0 14 * * 2,4,6"           # Tue/Thu/Sat 14:00 UTC
        DaysOfWeek = @("Tuesday","Thursday","Saturday")
        TimeUtc  = "14:00"
    }
)

function ConvertUtcToLocalDateTime([string] $utcHHmm) {
    # UTC clock time → today's corresponding local DateTime for task trigger.
    $parts = $utcHHmm.Split(":")
    $utcNow = (Get-Date).ToUniversalTime()
    $utcToday = [DateTime]::new($utcNow.Year, $utcNow.Month, $utcNow.Day,
                                [int]$parts[0], [int]$parts[1], 0,
                                [DateTimeKind]::Utc)
    return $utcToday.ToLocalTime()
}

foreach ($t in $Tasks) {
    $localStart = ConvertUtcToLocalDateTime $t.TimeUtc

    $action = New-ScheduledTaskAction `
        -Execute $Wrapper `
        -Argument $t.Channel `
        -WorkingDirectory $RepoRoot

    $trigger = New-ScheduledTaskTrigger `
        -Weekly `
        -DaysOfWeek $t.DaysOfWeek `
        -At $localStart

    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable:$true `
        -AllowStartIfOnBatteries:$true `
        -DontStopIfGoingOnBatteries:$true `
        -RunOnlyIfNetworkAvailable:$true `
        -WakeToRun:$false `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 4)

    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive `
        -RunLevel Limited

    Write-Host ("registering {0,-30} channel={1,-20} days={2,-25} local_start={3}" -f `
        $t.Name, $t.Channel, ($t.DaysOfWeek -join ','), $localStart)

    Register-ScheduledTask `
        -TaskName $t.Name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description ("Peanut Reacts pipeline - channel {0} (execution_env: local)" -f $t.Channel) `
        -Force | Out-Null
}

Write-Host ""
Write-Host "Registered tasks:"
Get-ScheduledTask -TaskName "Peanut-*" | Format-Table TaskName, State, @{
    Label = "NextRun"
    Expression = { (Get-ScheduledTaskInfo $_).NextRunTime }
} -AutoSize

Write-Host "To test immediately (without waiting for cron):"
Write-Host "    Start-ScheduledTask -TaskName Peanut-hasanabi_archive"
Write-Host ""
Write-Host "To view logs:"
Write-Host "    Get-Content `"$RepoRoot\logs\local_<channel>_*.log`" -Tail 50"
