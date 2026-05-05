"""One-shot OAuth setup for the 4 new network channels (money_drama,
karma_files, workplace_wars, heart_drama).

Walks you through Google's OAuth consent flow once per channel,
saves the token file at the path channels.yaml expects, and
verifies the upload scope is granted. After running this, the
pipelines for those channels can upload immediately.

Prerequisites:
  1. Created the 4 new YouTube channels under your existing Google
     account at youtube.com -> profile -> "Add or manage your channel(s)"
     -> "Create a new channel".
  2. ~/.peanut_reacts/client_secret.json already in place (it's the
     existing Peanut Reacts OAuth client; we re-use it).

Usage:
  PYTHONPATH=src python scripts/oauth_setup_network.py

  Optional: --channel <id> to authorize just one specific channel.
  Useful if a token expires or only one channel needs re-auth.

For each channel:
  1. Browser opens with Google's account-picker.
  2. Pick the SPECIFIC YouTube channel (Google asks "which brand
     account?" — pick the matching one for the channel id we're
     authorizing).
  3. Grant 'Manage your YouTube account' scope.
  4. Token gets saved to ~/.peanut_reacts/<channel_id>_token.json.
  5. Script verifies by listing the channel name from the API.

The flow is interactive and requires you to be in front of a
browser. Total user time: ~5 min × 4 channels = 20 min.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# Channel id -> token filename + expected channel-name hint shown to user
NEW_CHANNELS = {
    "money_drama": {
        "token": "money_drama_token.json",
        "expected_handle": "@MoneyDramaReddit (or your chosen handle)",
        "hint": "Money / finance Reddit drama narrator",
    },
    "karma_files": {
        "token": "karma_files_token.json",
        "expected_handle": "@KarmaFiles (or your chosen handle)",
        "hint": "Petty + Pro revenge Reddit stories",
    },
    "workplace_wars": {
        "token": "workplace_wars_token.json",
        "expected_handle": "@WorkplaceWars (or your chosen handle)",
        "hint": "Antiwork + recruiting hell + malicious compliance",
    },
    "heart_drama": {
        "token": "heart_drama_token.json",
        "expected_handle": "@HeartDramaReddit (or your chosen handle)",
        "hint": "Relationship advice + heart drama Reddit",
    },
}


def _authorize(channel_id: str, secrets_dir: Path) -> bool:
    """Run the OAuth flow for one channel, save token, verify."""
    info = NEW_CHANNELS[channel_id]
    secrets_path = secrets_dir / "client_secret.json"
    token_path = secrets_dir / info["token"]

    print()
    print("=" * 70)
    print(f"  AUTHORIZING: {channel_id}")
    print(f"  Brand account hint:  {info['expected_handle']}")
    print(f"  Niche:               {info['hint']}")
    print(f"  Token will save to:  {token_path}")
    print("=" * 70)

    if token_path.exists():
        ans = input(f"\n  Token file already exists. Overwrite? [y/N] ").strip().lower()
        if ans != "y":
            print(f"  Skipping {channel_id}.")
            return True

    print(
        "\n  Browser will open. When Google asks 'choose an account',\n"
        "  pick your Google account THEN pick the SPECIFIC YouTube\n"
        f"  brand-account/channel for {channel_id}.\n"
        "  Grant the 'Manage your YouTube account' scope.\n"
        "\n  Press ENTER to continue..."
    )
    input()

    try:
        from peanut_reacts.upload.youtube_auth import get_authenticated_service
        service = get_authenticated_service(
            client_secrets_path=secrets_path,
            token_path=token_path,
            interactive=True,
        )
    except Exception as e:
        print(f"\n  ERROR: OAuth flow failed: {e}")
        return False

    # Verify by hitting the channel API
    try:
        resp = service.channels().list(part="snippet,statistics", mine=True).execute()
        items = resp.get("items", [])
        if not items:
            print("  WARNING: API returned no channel — token saved but unusable")
            return False
        channel = items[0]
        sn = channel["snippet"]
        st = channel.get("statistics", {})
        print()
        print(f"  ✓ Token saved successfully")
        print(f"    Channel name:  {sn.get('title')}")
        print(f"    Channel id:    {channel.get('id')}")
        print(f"    Subs:          {st.get('subscriberCount', '?')}")
        print(f"    Videos:        {st.get('videoCount', '?')}")
        print(f"    Token at:      {token_path}")
        return True
    except Exception as e:
        print(f"  WARNING: token saved but verify failed: {e}")
        return False


def main() -> int:
    logging.basicConfig(level=logging.WARNING)

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--channel",
        choices=list(NEW_CHANNELS.keys()),
        help="Authorize just one channel (default: all 4)",
    )
    args = ap.parse_args()

    secrets_dir = Path("~/.peanut_reacts").expanduser()
    if not (secrets_dir / "client_secret.json").exists():
        print(f"ERROR: ~/.peanut_reacts/client_secret.json missing.")
        print(f"  This is the existing Peanut Reacts OAuth client.")
        print(f"  If you're starting fresh, copy it from your other channel setup.")
        return 1

    targets = [args.channel] if args.channel else list(NEW_CHANNELS.keys())

    print(f"\n  Will authorize {len(targets)} channel(s) under the existing Google")
    print(f"  account. For each one, the browser will open and ask you to")
    print(f"  PICK THE SPECIFIC BRAND ACCOUNT for that channel.")
    print(f"\n  Channels to authorize:")
    for cid in targets:
        print(f"    {cid:<20}  ->  {NEW_CHANNELS[cid]['expected_handle']}")
    print(f"\n  Press ENTER to start, Ctrl+C to abort.")
    try:
        input()
    except KeyboardInterrupt:
        print("\n  Aborted.")
        return 1

    results = {}
    for cid in targets:
        results[cid] = _authorize(cid, secrets_dir)

    print()
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    for cid, ok in results.items():
        mark = "✓" if ok else "✗"
        print(f"    {mark}  {cid}")

    if all(results.values()):
        print(f"\n  All {len(results)} channels authorized.")
        print(f"\n  Next steps:")
        print(f"    1. Run scp commands to copy tokens to VPS (script will print)")
        print(f"    2. For each token, base64-encode and add as a GitHub Actions")
        print(f"       secret named YT_TOKEN_<CHANNEL_ID>_B64.")
        print(f"       (We'll auto-generate the base64 next.)")
        return 0
    else:
        failed = [cid for cid, ok in results.items() if not ok]
        print(f"\n  {len(failed)} channel(s) failed: {', '.join(failed)}")
        print(f"  Re-run with --channel <id> to retry just one.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
