#!/usr/bin/env python3
"""Upload the Miniminter Mega compilation to YouTube (UK CLIPS channel).

Uses the existing peanut_reacts upload infrastructure:
- youtube_auth for OAuth (cached token at ~/.peanut_reacts/youtube_token.json)
- YouTubeUploader for resumable upload with retry logic
"""

import sys
import io
import json
import logging
import traceback
from pathlib import Path
from datetime import datetime

# Fix encoding for Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Also log to a dedicated file for persistent output
LOG_FILE = Path(__file__).resolve().parents[1] / "production" / "miniminter_mega" / "upload_log.txt"

class DualLogger:
    """Write to both stdout and a file."""
    def __init__(self, filepath):
        self.file = open(filepath, "w", encoding="utf-8")
        self.stdout = sys.stdout
    def write(self, msg):
        self.stdout.write(msg)
        self.stdout.flush()
        self.file.write(msg)
        self.file.flush()
    def flush(self):
        self.stdout.flush()
        self.file.flush()

sys.stdout = DualLogger(LOG_FILE)
sys.stderr = sys.stdout

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.upload.youtube_auth import get_authenticated_service
from peanut_reacts.upload.youtube_upload import UploadMetadata, YouTubeUploader

log = build_logger("upload_miniminter_mega", verbose=True)

# ── Paths ────────────────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parents[1]
VIDEO_PATH = PROJECT / "production" / "miniminter_mega" / "MINIMINTER_MEGA_FINAL.mp4"
CLIENT_SECRETS = Path("C:/Users/anasm/Downloads/client_secret_914737972580-9pd0j8k5601tk4p4mmq4e5jvnevonppa.apps.googleusercontent.com.json")
UPLOAD_META_OUT = PROJECT / "production" / "miniminter_mega" / "upload_meta.json"


def main():
    # ── Verify files ─────────────────────────────────────────────────
    if not VIDEO_PATH.exists():
        log.error("Video not found: %s", VIDEO_PATH)
        return 1
    log.info("Video: %s (%.2f GB)", VIDEO_PATH.name, VIDEO_PATH.stat().st_size / (1024**3))

    if not CLIENT_SECRETS.exists():
        log.error("Client secrets not found: %s", CLIENT_SECRETS)
        return 1

    # ── Build metadata ───────────────────────────────────────────────
    title = "7 HOURS of Miniminter Reacts to Sidemen Among Us | Peanut Reacts 2026"

    description = (
        "Peanut watches over 7 HOURS of Miniminter reacting to every Sidemen Among Us episode! "
        "All 7 episodes in one mega compilation with 1,778 peanut reactions. "
        "From Simon's wild accusations to the most chaotic impostor rounds, "
        "Peanut has something to say about every single moment.\n"
        "\n"
        "Featuring all Sidemen Among Us episodes that Miniminter reacted to, "
        "with Peanut's animated commentary throughout.\n"
        "\n"
        "Original content by Miniminter (Simon Minter) and the Sidemen.\n"
        "Peanut Reacts is an AI-generated animated reaction character.\n"
        "\n"
        "#sidemen #miniminter #amongus #simon #sidemenamongus #peanutreacts "
        "#reaction #compilation #gaming #2026 #megacompilation\n"
        "\n"
        "Subscribe for more Peanut Reacts!"
    )

    tags = [
        "sidemen", "among us", "miniminter", "simon", "simon minter",
        "reaction", "peanut reacts", "compilation", "2026",
        "sidemen among us", "mega compilation", "7 hours",
        "gaming", "funny reaction", "animated reaction",
        "imposter", "crewmate", "gaming reaction",
        "sidemen reaction", "peanut", "UK CLIPS",
    ]

    metadata = UploadMetadata(
        title=title,
        description=description,
        tags=tags,
        category_id="20",        # Gaming
        privacy_status="public",
        thumbnail_path=None,
    )

    log.info("Title: %s", metadata.title)
    log.info("Privacy: %s", metadata.privacy_status)
    log.info("Category: %s (Gaming)", metadata.category_id)
    log.info("Tags: %s", ", ".join(metadata.tags[:10]))

    # ── Save upload metadata for records ─────────────────────────────
    meta_record = {
        "title": metadata.title,
        "description": metadata.description,
        "tags": metadata.tags,
        "category_id": metadata.category_id,
        "privacy_status": metadata.privacy_status,
    }
    UPLOAD_META_OUT.write_text(json.dumps(meta_record, indent=2), encoding="utf-8")
    log.info("Saved upload metadata to %s", UPLOAD_META_OUT)

    # ── Authenticate ─────────────────────────────────────────────────
    log.info("Authenticating with YouTube API...")
    try:
        service = get_authenticated_service(CLIENT_SECRETS)
        log.info("Authentication successful (using cached token)")
    except Exception as e:
        log.error("Authentication failed: %s", e)
        return 1

    # ── Upload (inline, no wrapper, for maximum error capture) ────────
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    file_size = VIDEO_PATH.stat().st_size
    print(f"[{datetime.now()}] Starting upload of {file_size / (1024**3):.2f} GB file...")
    print(f"[{datetime.now()}] This will take a while for a 7.4 hour video...")

    body = {
        "snippet": {
            "title": metadata.title[:100],
            "description": metadata.description[:5000],
            "tags": metadata.tags[:500],
            "categoryId": metadata.category_id,
        },
        "status": {
            "privacyStatus": metadata.privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    print(f"[{datetime.now()}] Creating MediaFileUpload (10MB chunks)...")
    media = MediaFileUpload(
        str(VIDEO_PATH),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10MB chunks
    )

    print(f"[{datetime.now()}] Calling videos().insert()...")
    try:
        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        print(f"[{datetime.now()}] Insert request created, starting resumable upload...")
    except Exception as e:
        print(f"[{datetime.now()}] ERROR creating insert request: {e}")
        traceback.print_exc()
        return 1

    # Resumable upload loop with detailed progress
    response = None
    chunk_num = 0
    try:
        while response is None:
            chunk_num += 1
            status, response = request.next_chunk()
            if status:
                pct = status.progress() * 100
                uploaded_mb = status.resumable_progress / (1024 * 1024)
                total_mb = file_size / (1024 * 1024)
                print(f"[{datetime.now()}] Chunk {chunk_num}: {pct:.1f}% ({uploaded_mb:.0f}/{total_mb:.0f} MB)")
            else:
                if chunk_num % 10 == 0:
                    print(f"[{datetime.now()}] Chunk {chunk_num} sent (no status yet)...")

    except HttpError as e:
        error_msg = str(e)
        print(f"[{datetime.now()}] HTTP ERROR during upload: {error_msg}")
        traceback.print_exc()

        if "uploadLimitExceeded" in error_msg or "quota" in error_msg.lower():
            print(f"[{datetime.now()}] DAILY UPLOAD LIMIT REACHED - retry tomorrow")
            return 2
        return 1

    except Exception as e:
        print(f"[{datetime.now()}] UNEXPECTED ERROR during upload: {e}")
        traceback.print_exc()
        return 1

    if response is None:
        print(f"[{datetime.now()}] ERROR: Upload loop ended with no response")
        return 1

    video_id = response["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"[{datetime.now()}] {'=' * 60}")
    print(f"[{datetime.now()}] UPLOAD COMPLETE!")
    print(f"[{datetime.now()}] Video ID: {video_id}")
    print(f"[{datetime.now()}] URL: {url}")
    print(f"[{datetime.now()}] {'=' * 60}")

    # Save result
    meta_record["video_id"] = video_id
    meta_record["url"] = url
    UPLOAD_META_OUT.write_text(json.dumps(meta_record, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        code = main()
        print(f"\n[{datetime.now()}] Script exiting with code {code}")
        sys.exit(code)
    except Exception as e:
        print(f"\n[{datetime.now()}] UNHANDLED EXCEPTION:")
        traceback.print_exc()
        sys.exit(99)
