"""Deploy the pipeline to the Hetzner VPS via SSH.

Credentials come from environment variables, never hard-coded:

  PEANUT_VPS_HOST      IP or hostname (required)
  PEANUT_VPS_USER      SSH username (default 'root')
  PEANUT_VPS_PASSWORD  SSH password OR leave unset to use a key
  PEANUT_VPS_KEY       Path to SSH private key (preferred)

Fail-fast: if no auth material is present the script exits with a
clear error before attempting any connection. This prevents accidents
where a prod VPS gets touched by a misconfigured local run.
"""
import os
import sys
import time

import paramiko


HOST = os.environ.get("PEANUT_VPS_HOST", "").strip()
USER = os.environ.get("PEANUT_VPS_USER", "root").strip()
PASSWORD = os.environ.get("PEANUT_VPS_PASSWORD", "").strip() or None
KEY_PATH = os.environ.get("PEANUT_VPS_KEY", "").strip() or None

if not HOST:
    print(
        "ERROR: PEANUT_VPS_HOST not set.\n"
        "Export it (or put it in deploy/.env and `source deploy/.env`):\n"
        "  export PEANUT_VPS_HOST=a.b.c.d\n"
        "  export PEANUT_VPS_PASSWORD=...     # OR\n"
        "  export PEANUT_VPS_KEY=~/.ssh/id_ed25519",
        file=sys.stderr,
    )
    sys.exit(1)

if not PASSWORD and not KEY_PATH:
    print(
        "ERROR: neither PEANUT_VPS_PASSWORD nor PEANUT_VPS_KEY is set.\n"
        "Set one of them to authenticate.",
        file=sys.stderr,
    )
    sys.exit(1)

def ssh_exec(ssh, cmd, timeout=300):
    """Execute a command and print output in real-time."""
    print(f"\n>>> {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if out.strip():
        # Print last 500 chars to stay readable
        lines = out.strip().split("\n")
        if len(lines) > 10:
            print(f"  ({len(lines)} lines, showing last 10)")
            print("\n".join(f"  {l}" for l in lines[-10:]))
        else:
            print("\n".join(f"  {l}" for l in lines))
    if err.strip():
        for line in err.strip().split("\n")[-5:]:
            print(f"  [err] {line}")
    return stdout.channel.recv_exit_status()

def _connect():
    """Open an SSH connection using whichever auth method was supplied."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if KEY_PATH:
        pkey = paramiko.Ed25519Key.from_private_key_file(os.path.expanduser(KEY_PATH))
        ssh.connect(HOST, username=USER, pkey=pkey, timeout=15)
    else:
        ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    return ssh


def main():
    ssh = _connect()
    print(f"Connected to {HOST}")

    # Step 1: Install system deps
    print("\n=== STEP 1: System packages ===")
    ssh_exec(ssh, "apt-get update -y -qq", timeout=120)
    ssh_exec(ssh, "apt-get install -y -qq git docker.io docker-compose-v2 curl ffmpeg python3-pip python3-venv tmux", timeout=300)
    ssh_exec(ssh, "systemctl enable --now docker", timeout=30)

    # Step 2: Clone the repo
    print("\n=== STEP 2: Clone pipeline ===")
    ssh_exec(ssh, "rm -rf /opt/peanut && git clone https://github.com/AnasPine2000/peanut-reacts-pipeline.git /opt/peanut", timeout=120)

    # Step 3: Create directories
    print("\n=== STEP 3: Setup directories ===")
    ssh_exec(ssh, "mkdir -p /opt/peanut/tokens /opt/peanut/secrets /opt/peanut/logs /opt/peanut/production /opt/peanut/cache")

    # Step 4: Set up Python venv (non-Docker approach, simpler for now)
    print("\n=== STEP 4: Python environment ===")
    ssh_exec(ssh, "python3 -m venv /opt/peanut/venv", timeout=60)
    ssh_exec(ssh, "/opt/peanut/venv/bin/pip install --upgrade pip wheel", timeout=60)
    ssh_exec(ssh, "/opt/peanut/venv/bin/pip install -r /opt/peanut/requirements.txt 2>&1 | tail -5", timeout=600)

    # Step 5: Install yt-dlp globally
    print("\n=== STEP 5: yt-dlp ===")
    ssh_exec(ssh, "/opt/peanut/venv/bin/pip install yt-dlp", timeout=60)

    # Step 6: Verify
    print("\n=== STEP 6: Verify installation ===")
    ssh_exec(ssh, "/opt/peanut/venv/bin/python -c 'import peanut_reacts; print(\"peanut_reacts OK\")' 2>&1 || echo 'Need PYTHONPATH'")
    ssh_exec(ssh, "PYTHONPATH=/opt/peanut/src /opt/peanut/venv/bin/python -c 'from peanut_reacts.scheduler.runner import main; print(\"Scheduler OK\")'")
    ssh_exec(ssh, "ffmpeg -version | head -1")
    ssh_exec(ssh, "/opt/peanut/venv/bin/yt-dlp --version")

    # Step 7: Set up firewall
    print("\n=== STEP 7: Firewall ===")
    ssh_exec(ssh, "ufw allow 22/tcp && ufw allow 7860/tcp && ufw --force enable", timeout=15)

    print("\n" + "=" * 60)
    print("DEPLOYMENT COMPLETE!")
    print("=" * 60)
    print(f"Server: {HOST}")
    print("Next: transfer .env + tokens + secrets, then start scheduler")

    ssh.close()

if __name__ == "__main__":
    main()
