"""
YouTube OAuth 2.0 credential management with persistent token caching.

Authenticates once via browser, caches the token at
~/.peanut_reacts/youtube_token.json, and auto-refreshes on subsequent runs.
Shared by both comment fetching and video uploading.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

TOKEN_DIR = Path.home() / ".peanut_reacts"
TOKEN_PATH = TOKEN_DIR / "youtube_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def _load_credentials(token_path: Path, scopes: list[str]) -> Optional[Credentials]:
    """Load cached credentials and refresh if expired."""
    if not token_path.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning("Invalid token file, will re-authenticate: %s", e)
        return None

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds, token_path)
            logger.info("Refreshed YouTube OAuth token.")
            return creds
        except Exception as e:
            logger.warning("Token refresh failed, will re-authenticate: %s", e)
            return None

    return None


def _save_credentials(creds: Credentials, token_path: Path) -> None:
    """Persist credentials to disk."""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    logger.debug("Saved YouTube token to %s", token_path)


def get_authenticated_service(
    client_secrets_path: str | Path,
    token_path: Path = TOKEN_PATH,
    scopes: list[str] | None = None,
    interactive: bool = True,
):
    """Return an authenticated YouTube Data API v3 service.

    Loads cached credentials from token_path, refreshes if expired, or
    runs the OAuth browser flow if no valid token exists.

    Set `interactive=False` to raise instead of prompting the user — use
    this for non-interactive contexts (cron jobs, stats pollers, the VPS
    daemon) where we'd rather fail fast than hang waiting for a click.
    """
    scopes = scopes or SCOPES
    client_secrets_path = Path(client_secrets_path)

    if not client_secrets_path.exists():
        raise FileNotFoundError(
            f"OAuth client secrets file not found: {client_secrets_path}\n"
            "Download it from https://console.cloud.google.com/apis/credentials"
        )

    # Try cached credentials first
    creds = _load_credentials(token_path, scopes)

    if not creds:
        if not interactive:
            raise RuntimeError(
                f"OAuth token at {token_path} is missing or expired; "
                "re-auth required but interactive=False was set. "
                "Run the OAuth flow once from a machine with a browser to refresh."
            )
        logger.info("No valid cached token — starting OAuth flow (browser will open) ...")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), scopes)
        creds = flow.run_local_server(port=0)
        _save_credentials(creds, token_path)
        logger.info("YouTube OAuth authentication successful.")

    return build("youtube", "v3", credentials=creds)
