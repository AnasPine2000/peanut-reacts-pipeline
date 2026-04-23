"""Fast sync: git pull + pip install -q + systemctl restart on the VPS.

Use this for every-day "ship new commit to prod" operations. For the
first-time full bootstrap (apt packages, venv, systemd unit), use
remote_deploy.py instead.

Reads the same env vars as remote_deploy.py:
  PEANUT_VPS_HOST, PEANUT_VPS_USER (default root),
  PEANUT_VPS_PASSWORD or PEANUT_VPS_KEY

Exit codes:
  0  success
  1  pre-flight auth / connect failure
  2  git pull or restart failed on the VPS

Usage:
  python deploy/vps_sync.py
  python deploy/vps_sync.py --no-restart      # just pull
  python deploy/vps_sync.py --service foo     # restart a different unit
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import paramiko


HOST = os.environ.get("PEANUT_VPS_HOST", "").strip()
USER = os.environ.get("PEANUT_VPS_USER", "root").strip()
PASSWORD = os.environ.get("PEANUT_VPS_PASSWORD", "").strip() or None
KEY_PATH = os.environ.get("PEANUT_VPS_KEY", "").strip() or None


def _connect():
    if not HOST:
        print("ERROR: PEANUT_VPS_HOST not set", file=sys.stderr)
        sys.exit(1)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if KEY_PATH:
        pkey = paramiko.Ed25519Key.from_private_key_file(os.path.expanduser(KEY_PATH))
        ssh.connect(HOST, username=USER, pkey=pkey, timeout=15)
    elif PASSWORD:
        ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    else:
        print("ERROR: set PEANUT_VPS_PASSWORD or PEANUT_VPS_KEY", file=sys.stderr)
        sys.exit(1)
    return ssh


def _run(ssh, cmd, timeout=120):
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    # Safe-print: some VPS log lines contain non-ASCII; replace for Windows
    # consoles that may not be UTF-8.
    combined = (out + err).encode("ascii", "replace").decode("ascii")
    return rc, combined


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install-dir", default="/opt/peanut-reacts",
                    help="Remote clone path")
    ap.add_argument("--service", default="peanut-scheduler.service",
                    help="systemd unit to restart")
    ap.add_argument("--no-restart", action="store_true",
                    help="Just pull; skip the restart")
    ap.add_argument("--pip-install", action="store_true",
                    help="Also run pip install -r requirements.txt")
    args = ap.parse_args()

    ssh = _connect()
    print(f"connected to {HOST} ({USER})")

    t0 = time.time()

    cmd = (
        f"cd {args.install_dir} && "
        f"git fetch --quiet --all && "
        f"git reset --hard origin/master && "
        f"git log --oneline -1"
    )
    rc, out = _run(ssh, cmd)
    print(f"[git] rc={rc}\n{out.strip()}")
    if rc != 0:
        ssh.close()
        return 2

    if args.pip_install:
        cmd = (
            f"{args.install_dir}/.venv/bin/pip install -q "
            f"-r {args.install_dir}/requirements.txt 2>&1 | tail -3"
        )
        rc, out = _run(ssh, cmd, timeout=600)
        print(f"[pip] rc={rc}\n{out.strip()}")

    if not args.no_restart:
        rc, out = _run(ssh, f"systemctl restart {args.service}")
        print(f"[restart] rc={rc}  {out.strip() or args.service}")
        time.sleep(4)
        rc, out = _run(ssh, f"systemctl is-active {args.service}")
        print(f"[status] {out.strip()}")
        if "active" not in out:
            ssh.close()
            return 2

    print(f"done in {time.time() - t0:.1f}s")
    ssh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
