@echo off
REM ===================================================================
REM Run one channel's pipeline as a one-off on this residential PC.
REM Used by Windows Task Scheduler to fire YouTube-scraping channels
REM that can't run from the Hetzner VPS (datacenter-IP bot detection).
REM
REM Usage:
REM   run_local_channel.bat <channel_id>
REM
REM Example:
REM   run_local_channel.bat uk_clips
REM ===================================================================

SETLOCAL

IF "%~1"=="" (
    echo ERROR: channel id required. Usage: run_local_channel.bat ^<channel_id^>
    exit /b 2
)

SET CHANNEL=%~1
SET REPO=C:\Users\anasm\Videos\4K Video Downloader+
SET PEANUT_SCHEDULER_ENV=local
SET PYTHONIOENCODING=utf-8
SET PYTHONPATH=%REPO%\src

REM Log output to a daily-rotated file for Task Scheduler observability
SET LOGDIR=%REPO%\logs
IF NOT EXIST "%LOGDIR%" mkdir "%LOGDIR%"
SET DATESTAMP=%DATE:/=-%
SET LOGFILE=%LOGDIR%\local_%CHANNEL%_%DATESTAMP: =%.log

cd /D "%REPO%"

echo [%DATE% %TIME%] starting %CHANNEL% >> "%LOGFILE%"

REM Prevent Windows from sleeping while this task runs. SetThreadExecutionState
REM keeps the system awake (and display on) while PowerShell is alive. When the
REM shell exits the request is automatically released. Non-admin, no reboot.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class Sleep { [DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint f); public const uint ES_CONTINUOUS = 0x80000000; public const uint ES_SYSTEM_REQUIRED = 0x1; public const uint ES_DISPLAY_REQUIRED = 0x2; }'; ^
   [Sleep]::SetThreadExecutionState([Sleep]::ES_CONTINUOUS -bor [Sleep]::ES_SYSTEM_REQUIRED); ^
   try { python -m peanut_reacts.scheduler.runner --run-once '%CHANNEL%' } ^
   finally { [Sleep]::SetThreadExecutionState([Sleep]::ES_CONTINUOUS) }" ^
  >> "%LOGFILE%" 2>&1

SET RC=%ERRORLEVEL%
echo [%DATE% %TIME%] finished %CHANNEL% rc=%RC% >> "%LOGFILE%"

ENDLOCAL & EXIT /B %RC%
