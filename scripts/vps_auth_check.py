#!/usr/bin/env python3
"""Tiny sanity check that runs on the VPS after deploy.

Loads .env, loads channels.yaml, resolves OAuth paths for a channel, calls
the YouTube Data API, and prints the authed channel's title + sub count.

Usage (on VPS):
    cd /opt/peanut-reacts
    PYTHONPATH=src .venv/bin/python scripts/vps_auth_check.py hasanabi_archive
"""
import os
import sys
from pathlib import Path

# Load .env
env = Path(".env")
if env.exists():
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from peanut_reacts.scheduler.channel_config import load_channels
from peanut_reacts.upload.youtube_auth import get_authenticated_service

channel_id = sys.argv[1] if len(sys.argv) > 1 else "hasanabi_archive"
cfg = load_channels(Path("channels.yaml"))
ch = next((c for c in cfg.channels if c.id == channel_id), None)
if ch is None:
    print(f"Channel not found: {channel_id}")
    sys.exit(2)

token_path = Path(ch.oauth_token).expanduser()
secrets_path = Path(ch.client_secrets).expanduser()
print(f"channel_id  : {ch.id}")
print(f"token       : {token_path}  exists={token_path.exists()}")
print(f"client_secret: {secrets_path}  exists={secrets_path.exists()}")

if not (token_path.exists() and secrets_path.exists()):
    print("missing credentials — upload them before retrying")
    sys.exit(3)

svc = get_authenticated_service(secrets_path, token_path=token_path)
r = svc.channels().list(part="snippet,statistics", mine=True).execute()
item = r["items"][0]
print(f"YouTube auth OK: {item['snippet']['title']!r} "
      f"(subs={item['statistics'].get('subscriberCount', '?')}, "
      f"videos={item['statistics'].get('videoCount', '?')})")
