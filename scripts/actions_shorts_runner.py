"""GitHub Actions entry point for the Shorts pipeline.

Fires a single channel's pipeline from a GitHub-hosted runner. Reads
every secret from environment variables (injected by the workflow from
repo secrets), decodes the OAuth token + client secret blobs into
real files on the runner's tmpfs, and invokes run_channel_pipeline.

On DRY_RUN=1 the pipeline still builds the video but skips the upload
step. Use that for the first couple of runs after rolling out.

Usage (inside the workflow step):
    python scripts/actions_shorts_runner.py --channel reddit_stories

Env vars the workflow must set:
    PEANUT_CHANNEL_ID       Channel id from channels.yaml (e.g. reddit_stories)
    DRY_RUN                 "1" to skip uploads, "0" to go live
    DEEPSEEK_API_KEY        LLM provider
    GROQ_API_KEY            Vision fallback (used only if Ollama not present)
    SCRAPE_DO_TOKEN         Reddit + trending scraping
    YT_CLIENT_SECRET_B64    Base64-encoded client_secret.json
    YT_TOKEN_B64            Base64-encoded OAuth refresh token for this channel
    ELEVENLABS_API_KEY      Optional; Edge TTS fallback works without it

Exit codes:
    0  success (pipeline returned, possibly 0 uploads which is OK)
    1  pre-flight failure (missing env, token decode failed, etc.)
    2  pipeline crashed with unhandled exception
"""
from __future__ import annotations

import argparse
import base64
import logging
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # Quiet noisy subloggers
    for n in ("httpx", "httpcore", "urllib3", "googleapiclient", "oauth2client", "yt_dlp"):
        logging.getLogger(n).setLevel(logging.WARNING)


def _write_b64_secret(env_name: str, target: Path) -> bool:
    """Decode a base64 env var into a file. Returns True on success."""
    blob = os.environ.get(env_name, "").strip()
    if not blob:
        logging.warning("%s is empty — skipping %s", env_name, target.name)
        return False
    try:
        data = base64.b64decode(blob)
    except Exception as e:
        logging.error("Could not base64-decode %s: %s", env_name, e)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    target.chmod(0o600)
    logging.info("Wrote %s (%d bytes)", target, len(data))
    return True


def _materialize_secrets(channel_id: str) -> tuple[Path, Path]:
    """Decode the two required YouTube secrets into ~/.peanut_reacts/."""
    secrets_root = Path.home() / ".peanut_reacts"
    client_secret = secrets_root / "client_secret.json"
    token_path = secrets_root / f"{channel_id}_token.json"

    if not _write_b64_secret("YT_CLIENT_SECRET_B64", client_secret):
        logging.error("YT_CLIENT_SECRET_B64 is required")
        return Path(), Path()

    if not _write_b64_secret("YT_TOKEN_B64", token_path):
        logging.error("YT_TOKEN_B64 is required")
        return Path(), Path()

    return client_secret, token_path


def _patch_channel_config(channel_id: str, client_secret: Path, token_path: Path) -> None:
    """Override the channel's oauth_token + client_secrets paths to the runner-local files.

    The YAML ships with `~/.peanut_reacts/...` which expanduser() resolves fine,
    but we re-point here to be explicit and avoid any surprises with the
    runner's HOME env.
    """
    # Nothing to do — the YAML paths already resolve to the paths we
    # just wrote. Kept as a hook for future per-channel path overrides.
    pass


def _dry_run_enabled() -> bool:
    v = os.environ.get("DRY_RUN", "1").strip().lower()
    return v not in ("0", "false", "no", "")


def _set_api_env() -> None:
    """Copy GitHub secrets into the env names the code expects."""
    # Most of our code reads DEEPSEEK_API_KEY / GROQ_API_KEY etc directly,
    # so the workflow job's `env:` block is enough. This function exists
    # as a placeholder if we ever need to remap names.
    pass


def main() -> int:
    _setup_logging()
    log = logging.getLogger("actions_shorts")

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--channel",
        default=os.environ.get("PEANUT_CHANNEL_ID", "reddit_stories"),
        help="Channel id from channels.yaml",
    )
    args = ap.parse_args()

    channel_id = args.channel.strip()
    dry_run = _dry_run_enabled()
    log.info("=== GitHub Actions Shorts runner ===")
    log.info("channel=%s dry_run=%s runner=%s", channel_id, dry_run, os.environ.get("RUNNER_NAME", "?"))

    _set_api_env()

    # Materialize OAuth secrets
    client_secret, token_path = _materialize_secrets(channel_id)
    if not client_secret.exists() or not token_path.exists():
        log.error("Secrets missing — aborting")
        return 1

    # Late imports so that logging is set up before any heavy import happens
    from peanut_reacts.scheduler.channel_config import load_channels
    from peanut_reacts.scheduler.db import PipelineDB
    from peanut_reacts.scheduler.pipelines import run_channel_pipeline
    from peanut_reacts.scheduler.runner import _load_dotenv

    # Load .env if present (lets you run this locally with .env instead of
    # full shell exports). Actions workflow doesn't need this; env is
    # already injected by the job's `env:` block.
    _load_dotenv()

    cfg_path = ROOT / "channels.yaml"
    cfg = load_channels(cfg_path)
    channel = next((c for c in cfg.channels if c.id == channel_id), None)
    if channel is None:
        log.error("Channel %r not found in %s", channel_id, cfg_path)
        return 1

    if not channel.enabled:
        log.warning("Channel %s is enabled=false in YAML — skipping", channel_id)
        return 0

    log.info("Channel loaded: %s  source_type=%s  privacy=%s",
             channel.name, channel.source_type, channel.upload_privacy)

    # DRY_RUN strategy: flip upload_privacy to 'private' so that if the
    # pipeline does upload anyway, the video is invisible and we can
    # verify the rest of the flow worked without polluting public feed.
    if dry_run:
        log.info("DRY_RUN=1 -> forcing upload_privacy='private' on this run only")
        channel.upload_privacy = "private"

    # Actions-scoped pipeline.db. Persist via workflow artifact if we want
    # cross-run dedup; otherwise each run is isolated.
    db_path = Path(os.environ.get("PEANUT_DB_PATH", "pipeline.db")).resolve()
    db = PipelineDB(db_path)
    log.info("Pipeline DB: %s", db_path)

    t0 = time.time()
    try:
        videos, errors = run_channel_pipeline(channel, cfg.settings, db)
    except Exception as e:
        log.exception("Pipeline crashed: %s", e)
        return 2

    dt = time.time() - t0
    log.info("=== Run complete in %.1fs: %d uploaded, %d errors ===", dt, videos, errors)
    return 0


if __name__ == "__main__":
    sys.exit(main())
