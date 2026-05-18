#!/usr/bin/env python3
"""peanut — unified operations CLI for the content pipeline.

One command to run the whole thing without Claude. Every subcommand
is self-contained: it loads channels.yaml, resolves credentials, and
calls the same pipeline code the cron scheduler uses.

  python scripts/peanut.py doctor               diagnose config/creds
  python scripts/peanut.py channels             list channels + schedules
  python scripts/peanut.py run reddit_stories   produce + upload one channel
  python scripts/peanut.py run --all            run every enabled channel
  python scripts/peanut.py preview reddit_stories   render locally, NO upload
  python scripts/peanut.py status               recent pipeline jobs
  python scripts/peanut.py deploy               git push + sync VPS + restart
  python scripts/peanut.py logs                 tail the VPS scheduler log

A "run" does the FULL pipeline for that channel: download source ->
generate script -> TTS -> composite -> avatar -> polish -> upload.
"preview" is the same but with upload disabled and privacy forced
private — for checking output before it goes public.

Exit codes: 0 ok, 1 usage/config error, 2 pipeline produced errors.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ANSI marks — kept ASCII-safe for Windows consoles
OK = "[ OK ]"
BAD = "[FAIL]"
WARN = "[WARN]"


def _hr(title: str = "") -> None:
    line = "=" * 64
    print(line if not title else f"== {title} " + "=" * (60 - len(title)))


def _load_env() -> None:
    """Load .env so DEEPSEEK_API_KEY etc. are in os.environ."""
    try:
        from peanut_reacts.scheduler.runner import _load_dotenv
        _load_dotenv()
    except Exception:
        # Minimal fallback .env parser
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _load_config():
    """Return the parsed PipelineConfig (channels + settings)."""
    from peanut_reacts.scheduler.channel_config import load_channels
    return load_channels(ROOT / "channels.yaml")


# ── doctor ─────────────────────────────────────────────────────────

def cmd_doctor(args) -> int:
    """Diagnose everything the pipeline needs. Prints a checklist."""
    _load_env()
    _hr("peanut doctor")
    problems = 0

    # 1. external binaries
    for tool in ("ffmpeg", "ffprobe", "yt-dlp", "git"):
        found = _which(tool)
        print(f"  {OK if found else BAD}  {tool:<10} {'found' if found else 'MISSING — install it'}")
        problems += 0 if found else 1

    # 2. python package
    try:
        import peanut_reacts  # noqa: F401
        print(f"  {OK}  peanut_reacts package importable")
    except Exception as e:
        print(f"  {BAD}  peanut_reacts import failed: {e}")
        problems += 1

    # 3. channels.yaml
    try:
        cfg = _load_config()
        enabled = sum(1 for c in cfg.channels if c.enabled)
        print(f"  {OK}  channels.yaml parses — {len(cfg.channels)} channels, {enabled} enabled")
    except Exception as e:
        print(f"  {BAD}  channels.yaml failed to parse: {e}")
        problems += 1
        cfg = None

    # 4. API keys in env
    keys = {
        "DEEPSEEK_API_KEY": "required — script generation",
        "SCRAPEDO_API_TOKEN": "required for cloud — Reddit fetch",
        "GROQ_API_KEY": "optional — vision fallback",
        "ELEVENLABS_API_KEY": "optional — expressive voice",
        "REPLICATE_API_TOKEN": "optional — cloud vision",
    }
    for k, note in keys.items():
        v = os.environ.get(k, "").strip()
        req = note.startswith("required")
        if v:
            print(f"  {OK}  {k:<22} set ({note})")
        else:
            mark = BAD if req else WARN
            print(f"  {mark}  {k:<22} NOT set ({note})")
            if req:
                problems += 1

    # 5. OAuth token files
    secrets = Path("~/.peanut_reacts").expanduser()
    if secrets.exists():
        tokens = sorted(p.name for p in secrets.glob("*token*.json"))
        cs = (secrets / "client_secret.json").exists()
        print(f"  {OK if cs else BAD}  client_secret.json {'present' if cs else 'MISSING'}")
        print(f"  {OK if tokens else WARN}  OAuth tokens: {', '.join(tokens) or 'none found'}")
        if not cs:
            problems += 1
    else:
        print(f"  {BAD}  ~/.peanut_reacts/ directory missing")
        problems += 1

    # 6. Scrape.do liveness (the token has 401'd before)
    sd = os.environ.get("SCRAPEDO_API_TOKEN", "").strip()
    if sd:
        try:
            import httpx
            r = httpx.get(
                f"https://api.scrape.do/?token={sd}"
                "&url=https%3A%2F%2Fwww.reddit.com%2Fr%2Ftifu%2Ftop.json%3Flimit%3D1",
                timeout=30,
            )
            if r.status_code == 200:
                print(f"  {OK}  Scrape.do token is LIVE")
            else:
                print(f"  {BAD}  Scrape.do token returned HTTP {r.status_code} — rotate it")
                problems += 1
        except Exception as e:
            print(f"  {WARN}  Scrape.do check failed (network?): {e}")

    # 7. VPS deploy creds
    vps = os.environ.get("PEANUT_VPS_HOST", "").strip()
    vps_auth = os.environ.get("PEANUT_VPS_PASSWORD") or os.environ.get("PEANUT_VPS_KEY")
    if vps and vps_auth:
        print(f"  {OK}  VPS deploy creds set (host {vps})")
    else:
        print(f"  {WARN}  VPS deploy creds not set — 'peanut deploy' needs "
              "PEANUT_VPS_HOST + PEANUT_VPS_PASSWORD/KEY")

    print()
    if problems == 0:
        print(f"  {OK}  No blocking problems. Pipeline is ready to run.")
        return 0
    print(f"  {BAD}  {problems} blocking problem(s) — fix the [FAIL] lines above.")
    return 1


def _which(tool: str) -> bool:
    from shutil import which
    return which(tool) is not None


# ── channels ───────────────────────────────────────────────────────

def cmd_channels(args) -> int:
    """List all channels with their schedule + enabled state."""
    try:
        cfg = _load_config()
    except Exception as e:
        print(f"{BAD} channels.yaml: {e}")
        return 1
    _hr("channels")
    print(f"  {'id':<22} {'enabled':<8} {'env':<7} {'source':<14} schedule")
    print(f"  {'-'*22} {'-'*8} {'-'*7} {'-'*14} {'-'*16}")
    for c in cfg.channels:
        print(f"  {c.id:<22} {str(c.enabled):<8} "
              f"{getattr(c,'execution_env','?'):<7} {c.source_type:<14} "
              f"{c.schedule or '(none)'}")
    return 0


# ── run / preview ──────────────────────────────────────────────────

def _run_channels(channel_ids: list[str], dry_run: bool) -> int:
    _load_env()
    os.environ.setdefault("PEANUT_SCHEDULER_ENV", "local")
    from peanut_reacts.scheduler.db import PipelineDB
    from peanut_reacts.scheduler.pipelines import run_channel_pipeline

    cfg = _load_config()
    by_id = {c.id: c for c in cfg.channels}

    targets = []
    for cid in channel_ids:
        ch = by_id.get(cid)
        if ch is None:
            print(f"{BAD} unknown channel '{cid}'. Try: peanut channels")
            return 1
        targets.append(ch)

    db = PipelineDB(ROOT / "pipeline.db")
    total_videos, total_errors = 0, 0

    for ch in targets:
        _hr(f"run {ch.id}{'  (DRY — no upload)' if dry_run else ''}")
        if dry_run:
            # Disable upload by blanking the OAuth path + forcing
            # private. The pipeline renders fully, just doesn't publish.
            ch.oauth_token = ""
            ch.upload_privacy = "private"
        t0 = time.time()
        try:
            videos, errors = run_channel_pipeline(ch, cfg.settings, db)
        except Exception as e:
            print(f"  {BAD} {ch.id} crashed: {e}")
            total_errors += 1
            continue
        dt = time.time() - t0
        total_videos += videos
        total_errors += errors
        mark = OK if errors == 0 else WARN
        print(f"  {mark} {ch.id}: {videos} video(s), {errors} error(s) in {dt:.0f}s")

    print()
    print(f"  TOTAL: {total_videos} video(s), {total_errors} error(s)")
    return 2 if total_errors else 0


def cmd_run(args) -> int:
    """Run one channel, or every enabled channel with --all."""
    if args.all:
        cfg = _load_config()
        ids = [c.id for c in cfg.channels if c.enabled]
        if not ids:
            print(f"{WARN} no enabled channels")
            return 0
        print(f"Running {len(ids)} enabled channels: {', '.join(ids)}")
        return _run_channels(ids, dry_run=args.dry_run)
    if not args.channel:
        print(f"{BAD} specify a channel id, or use --all. See: peanut channels")
        return 1
    return _run_channels([args.channel], dry_run=args.dry_run)


def cmd_preview(args) -> int:
    """Render a channel locally with upload disabled — for review."""
    if not args.channel:
        print(f"{BAD} preview needs a channel id")
        return 1
    rc = _run_channels([args.channel], dry_run=True)
    # Surface the newest output file so the user can open it
    out = ROOT / "production" / args.channel
    if out.exists():
        vids = sorted(out.rglob("*_final.mp4"), key=lambda p: p.stat().st_mtime)
        if vids:
            print(f"\n  Newest render: {vids[-1]}")
    return rc


# ── status ─────────────────────────────────────────────────────────

def cmd_status(args) -> int:
    """Show recent pipeline jobs from pipeline.db."""
    import sqlite3
    db_path = ROOT / "pipeline.db"
    if not db_path.exists():
        print(f"{WARN} no pipeline.db yet — nothing has run locally")
        return 0
    _hr("recent pipeline jobs")
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT channel_id, status, step, created_at, title "
            "FROM jobs ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    except Exception as e:
        print(f"{BAD} could not read jobs: {e}")
        return 1
    finally:
        con.close()
    if not rows:
        print("  (no jobs recorded)")
        return 0
    for cid, status, step, created, title in rows:
        print(f"  {str(created)[:19]:<20} {str(cid):<18} "
              f"{str(status):<9} {str(step or ''):<12} {str(title or '')[:42]}")
    return 0


# ── deploy ─────────────────────────────────────────────────────────

def cmd_deploy(args) -> int:
    """git push the current branch, then sync + restart the VPS."""
    _load_env()
    _hr("deploy")

    # 1. warn on uncommitted changes
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(ROOT),
        capture_output=True, text=True,
    ).stdout.strip()
    if dirty:
        print(f"  {WARN} uncommitted changes present — they will NOT deploy:")
        for line in dirty.splitlines()[:8]:
            print(f"         {line}")
        if not args.yes:
            ans = input("  Continue deploying only committed work? [y/N] ")
            if ans.strip().lower() != "y":
                print("  aborted.")
                return 1

    # 2. push
    print("  pushing to origin...")
    push = subprocess.run(["git", "push"], cwd=str(ROOT),
                          capture_output=True, text=True)
    if push.returncode != 0:
        print(f"  {BAD} git push failed: {push.stderr.strip()[:200]}")
        return 1
    print(f"  {OK} pushed")

    # 3. VPS sync via the existing helper
    if not os.environ.get("PEANUT_VPS_HOST"):
        print(f"  {WARN} PEANUT_VPS_HOST not set — skipping VPS sync.")
        print("         set PEANUT_VPS_HOST + PEANUT_VPS_PASSWORD/KEY to enable.")
        return 0
    print("  syncing VPS...")
    rc = subprocess.run(
        [sys.executable, str(ROOT / "deploy" / "vps_sync.py")],
        cwd=str(ROOT),
    ).returncode
    if rc == 0:
        print(f"  {OK} VPS synced + scheduler restarted")
        return 0
    print(f"  {BAD} VPS sync failed (exit {rc})")
    return 1


# ── logs ───────────────────────────────────────────────────────────

def cmd_logs(args) -> int:
    """Tail the VPS scheduler log over SSH."""
    _load_env()
    host = os.environ.get("PEANUT_VPS_HOST", "").strip()
    pw = os.environ.get("PEANUT_VPS_PASSWORD", "").strip()
    key = os.environ.get("PEANUT_VPS_KEY", "").strip()
    if not host or not (pw or key):
        print(f"{BAD} set PEANUT_VPS_HOST + PEANUT_VPS_PASSWORD/KEY first")
        return 1
    try:
        import paramiko
    except ImportError:
        print(f"{BAD} paramiko not installed (pip install paramiko)")
        return 1

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if key:
            pkey = paramiko.Ed25519Key.from_private_key_file(os.path.expanduser(key))
            ssh.connect(host, username=os.environ.get("PEANUT_VPS_USER", "root"),
                        pkey=pkey, timeout=15)
        else:
            ssh.connect(host, username=os.environ.get("PEANUT_VPS_USER", "root"),
                        password=pw, timeout=15)
    except Exception as e:
        print(f"{BAD} SSH connect failed: {e}")
        return 1
    n = args.lines
    _, stdout, _ = ssh.exec_command(
        f"tail -{n} /opt/peanut-reacts/logs/scheduler.log 2>&1", timeout=30,
    )
    out = stdout.read().decode("utf-8", "replace")
    ssh.close()
    print(out.encode("ascii", "replace").decode("ascii"))
    return 0


# ── main ───────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        prog="peanut",
        description="Unified operations CLI for the content pipeline.",
    )
    sub = ap.add_subparsers(dest="command")

    sub.add_parser("doctor", help="diagnose config + credentials")
    sub.add_parser("channels", help="list channels + schedules")

    p_run = sub.add_parser("run", help="produce + upload a channel")
    p_run.add_argument("channel", nargs="?", help="channel id (omit with --all)")
    p_run.add_argument("--all", action="store_true", help="run every enabled channel")
    p_run.add_argument("--dry-run", action="store_true",
                       help="render but do NOT upload (private)")

    p_prev = sub.add_parser("preview", help="render a channel locally, no upload")
    p_prev.add_argument("channel", help="channel id")

    sub.add_parser("status", help="recent pipeline jobs")

    p_dep = sub.add_parser("deploy", help="git push + sync VPS + restart")
    p_dep.add_argument("--yes", action="store_true", help="skip confirmation prompts")

    p_log = sub.add_parser("logs", help="tail the VPS scheduler log")
    p_log.add_argument("--lines", type=int, default=40, help="lines to show")

    args = ap.parse_args()
    if not args.command:
        ap.print_help()
        return 1

    dispatch = {
        "doctor": cmd_doctor,
        "channels": cmd_channels,
        "run": cmd_run,
        "preview": cmd_preview,
        "status": cmd_status,
        "deploy": cmd_deploy,
        "logs": cmd_logs,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
