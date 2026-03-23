#!/usr/bin/env python3
"""
CLI entry point for uploading reaction videos to YouTube.

Usage:
    peanut-upload video.mp4 --auto --privacy private -v
    peanut-upload video.mp4 --title "My Title" --tags "tag1,tag2" --privacy unlisted
"""

from __future__ import annotations

import argparse
from pathlib import Path

from peanut_reacts.core.config import load_config
from peanut_reacts.core.logging_setup import build_logger


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Upload a reaction video to YouTube with metadata and thumbnail.",
    )
    p.add_argument("video", help="Path to the video file to upload")

    # Manual metadata
    p.add_argument("--title", default="", help="Video title (max 100 chars)")
    p.add_argument("--description", default="", help="Video description")
    p.add_argument("--tags", default="", help="Comma-separated tags")
    p.add_argument("--category", default="24", help="YouTube category ID (default: 24 Entertainment)")
    p.add_argument("--privacy", default="", help="private | unlisted | public (default from config)")

    # Auto mode
    p.add_argument("--auto", action="store_true",
                   help="Auto-generate title/description/tags via LLM")
    p.add_argument("--original-title", default="",
                   help="Original video title (for --auto context)")
    p.add_argument("--info-json", default=None,
                   help="Path to .info.json (for --auto context)")

    # Thumbnail
    p.add_argument("--thumbnail", default=None, help="Custom thumbnail JPEG path")
    p.add_argument("--no-thumbnail", action="store_true", help="Skip thumbnail")

    # Auth
    p.add_argument("--client-secrets", default=None, help="Path to OAuth client_secret.json")

    # LLM (for auto mode)
    p.add_argument("--provider", default="", help="LLM provider (for --auto)")

    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("peanut_upload", args.verbose)
    cfg = load_config()

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        log.error("Video not found: %s", video_path)
        return 2

    # ── Resolve client secrets ───────────────────────────────────────
    client_secrets = args.client_secrets or cfg.youtube_client_secrets
    if not client_secrets:
        log.error(
            "No OAuth client secrets. Set YOUTUBE_CLIENT_SECRETS in .env "
            "or use --client-secrets path/to/client_secret.json"
        )
        return 2

    # ── Authenticate ─────────────────────────────────────────────────
    from peanut_reacts.upload.youtube_auth import get_authenticated_service

    try:
        service = get_authenticated_service(client_secrets)
    except Exception as e:
        log.error("Authentication failed: %s", e)
        return 1

    # ── Build metadata ───────────────────────────────────────────────
    from peanut_reacts.upload.youtube_upload import UploadMetadata, YouTubeUploader

    privacy = args.privacy or cfg.upload_privacy or "private"

    if args.auto:
        from peanut_reacts.character.metadata_generator import generate_metadata
        from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider

        llm_config = LLMConfig(provider=args.provider or cfg.llm_provider)
        llm = create_llm_provider(llm_config)

        # Gather context
        original_title = args.original_title or video_path.stem.replace("_peanut_reacts", "")
        highlights = []
        if args.info_json:
            import json
            try:
                data = json.loads(Path(args.info_json).read_text(encoding="utf-8"))
                highlights = [c["text"][:100] for c in data.get("comments", [])[:10]]
                original_title = original_title or data.get("title", "")
            except Exception:
                pass

        meta = generate_metadata(
            llm,
            original_title=original_title,
            comment_highlights=highlights,
        )
        log.info("Generated metadata:")
        log.info("  Title: %s", meta.title)
        log.info("  Tags: %s", ", ".join(meta.tags[:10]))

        metadata = UploadMetadata(
            title=meta.title,
            description=meta.description,
            tags=meta.tags,
            category_id=args.category,
            privacy_status=privacy,
        )
    else:
        if not args.title:
            log.error("Provide --title or use --auto for LLM-generated metadata.")
            return 2

        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        metadata = UploadMetadata(
            title=args.title,
            description=args.description,
            tags=tags,
            category_id=args.category,
            privacy_status=privacy,
        )

    # ── Thumbnail ────────────────────────────────────────────────────
    if args.thumbnail:
        metadata.thumbnail_path = Path(args.thumbnail)
    elif not args.no_thumbnail and args.auto:
        from peanut_reacts.compositing.thumbnail import generate_thumbnail
        from peanut_reacts.core.ffmpeg import get_video_duration

        thumb_path = video_path.with_suffix(".thumb.jpg")
        try:
            duration = get_video_duration(video_path)
            generate_thumbnail(
                video_path, thumb_path,
                title_text=metadata.title,
                video_duration=duration,
            )
            metadata.thumbnail_path = thumb_path
        except Exception as e:
            log.warning("Thumbnail generation failed (skipping): %s", e)

    # ── Upload ───────────────────────────────────────────────────────
    uploader = YouTubeUploader(service)

    try:
        result = uploader.upload_video(video_path, metadata)
        log.info("Uploaded! %s", result.url)
        return 0
    except Exception as e:
        log.error("Upload failed: %s", e)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
