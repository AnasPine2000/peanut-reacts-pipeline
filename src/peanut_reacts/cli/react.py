#!/usr/bin/env python3
"""
Master CLI entry point for the full peanut reaction pipeline.

One-click workflow — give it a YouTube URL and get a reaction video:

    peanut-react "https://www.youtube.com/watch?v=VIDEO_ID" --provider deepseek

Or use a local video file:

    peanut-react video.mp4 --info-json comments.info.json --provider deepseek
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from peanut_reacts.character.reaction_generator import LLMConfig
from peanut_reacts.character.tts import TTSConfig
from peanut_reacts.compositing.layout import FacecamPosition, LayoutConfig
from peanut_reacts.compositing.reaction_video import ReactionJob, ReactionPipeline
from peanut_reacts.core.config import load_config
from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.core.progress import ProgressTracker


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Generate a peanut reaction video. Pass a YouTube URL for the "
            "full one-click workflow (download + comments + reaction), or "
            "a local video file."
        ),
    )
    p.add_argument("video", help="YouTube URL or path to local video file")
    p.add_argument("-o", "--output", default=None,
                   help="Output video path (default: <video>_peanut_reacts.mp4)")

    # LLM options
    llm = p.add_argument_group("LLM options")
    llm.add_argument("--provider", default="deepseek", choices=["deepseek", "groq", "ollama"])
    llm.add_argument("--model", default="", help="LLM model name")
    llm.add_argument("--llm-api-key", default="", help="LLM API key (or use env var)")
    llm.add_argument("--temperature", type=float, default=0.8)
    llm.add_argument("--max-reactions", type=int, default=15)

    # TTS options
    tts = p.add_argument_group("TTS options")
    tts.add_argument("--tts-engine", default="edge", choices=["edge", "elevenlabs"],
                     help="TTS engine: edge (free) or elevenlabs (expressive, paid)")
    tts.add_argument("--elevenlabs-key", default="",
                     help="ElevenLabs API key (or set ELEVENLABS_API_KEY in .env)")
    tts.add_argument("--elevenlabs-voice", default="TX3LPaxmHKxFdv7VOQHJ",
                     help="ElevenLabs voice ID (default: Liam - Energetic Creator)")
    tts.add_argument("--voice", default="en-US-GuyNeural", help="Edge TTS voice")
    tts.add_argument("--rate", default="+10%", help="Speaking rate (e.g. +10%%)")

    # Comment options
    cmt = p.add_argument_group("Comment options")
    cmt.add_argument("--info-json", default=None, help="Path to .info.json with comments")
    cmt.add_argument("--no-comments", action="store_true", help="Skip comment analysis")
    cmt.add_argument("--youtube-api-key", default=None,
                     help="YouTube Data API key (or set YOUTUBE_API_KEY env var)")
    cmt.add_argument("--max-comments", type=int, default=0,
                     help="Max comments to fetch via YouTube API (default: from config)")

    # Download options
    dl = p.add_argument_group("Download options")
    dl.add_argument("--cookies-file", default=None,
                    help="Path to cookies.txt for yt-dlp (or set YTDLP_COOKIES_FILE in .env)")

    # Upload options
    up = p.add_argument_group("Upload options")
    up.add_argument("--upload", action="store_true", help="Upload to YouTube after generation")
    up.add_argument("--upload-privacy", default="",
                    help="Upload privacy: private|unlisted|public (default from config)")
    up.add_argument("--client-secrets", default=None,
                    help="Path to OAuth client_secret.json (or set YOUTUBE_CLIENT_SECRETS in .env)")

    # Subtitle options
    sub = p.add_argument_group("Subtitle options")
    sub.add_argument("--diarize", action="store_true",
                     help="Enable speaker diarization for color-coded subtitles")
    sub.add_argument("--hf-token", default="",
                     help="HuggingFace token for pyannote (or set HF_TOKEN env var)")
    sub.add_argument("--num-speakers", type=int, default=0,
                     help="Number of speakers (0 = auto-detect)")

    # Layout options
    lay = p.add_argument_group("Layout options")
    lay.add_argument("--facecam-scale", type=float, default=0.22,
                     help="Facecam size as fraction of video width")
    lay.add_argument("--facecam-position", default="bottom-right",
                     choices=["bottom-right", "bottom-left", "top-right", "top-left"])
    lay.add_argument("--facecam-border-color", default="white", help="Facecam border color")
    lay.add_argument("--name-tag", default="PEANUT", help="Name tag text (empty to disable)")
    lay.add_argument("--speech-position", default="below_facecam",
                     choices=["below_facecam", "bottom_center"],
                     help="Where to show speech text")

    # Character renderer
    char = p.add_argument_group("Character options")
    char.add_argument("--wav2lip", action="store_true",
                      help="Use Wav2Lip AI lip-sync (requires face image)")
    char.add_argument("--face-image", default=None,
                      help="Path to peanut face image for Wav2Lip (default: assets/peanut_face/realistic_neutral.png)")

    # Transcript
    p.add_argument("--transcript", default=None, help="Path to transcript JSON or SRT")
    p.add_argument("--whisper-model", default="base", help="Whisper model for transcription")
    p.add_argument("--whisper-device", default="cuda")

    # General
    p.add_argument("--work-dir", default=None, help="Working directory for intermediate files")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


_FACECAM_POSITION_MAP = {
    "bottom-right": FacecamPosition.BOTTOM_RIGHT,
    "bottom-left": FacecamPosition.BOTTOM_LEFT,
    "top-right": FacecamPosition.TOP_RIGHT,
    "top-left": FacecamPosition.TOP_LEFT,
}


def _is_youtube_url(s: str) -> bool:
    return any(domain in s for domain in ("youtube.com/", "youtu.be/"))


def _download_video(url: str, dl_dir: Path, log, cookies_file: str = "") -> Path:
    """Download video + subtitles via yt-dlp (no comments — API handles those)."""
    import yt_dlp

    dl_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "merge_output_format": "mp4",
        "outtmpl": str(dl_dir / "%(title)s.%(ext)s"),
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "subtitlesformat": "srt",
        "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}],
        "windowsfilenames": True,
        "ignoreerrors": True,
        "remote_components": ["ejs:github"],  # required for YouTube JS challenges
    }

    if cookies_file and Path(cookies_file).exists():
        ydl_opts["cookiefile"] = cookies_file
        log.info("Using cookies: %s", cookies_file)

    log.info("Downloading video from %s ...", url)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if not info:
        raise RuntimeError("yt-dlp failed to download the video.")

    # Find the downloaded MP4
    video_files = sorted(dl_dir.glob("*.mp4"))
    if not video_files:
        raise RuntimeError("No .mp4 file found after download.")

    log.info("Downloaded: %s", video_files[0].name)
    return video_files[0]


def _fetch_comments_via_api(url: str, output_dir: Path, api_key: str, max_comments: int, log) -> Path:
    """Fetch comments via YouTube Data API v3 and save as .info.json."""
    from peanut_reacts.download.youtube_comments import YouTubeCommentFetcher

    log.info("Fetching comments via YouTube Data API ...")
    fetcher = YouTubeCommentFetcher.from_api_key(api_key)
    info_path = fetcher.fetch_and_save(
        url, output_dir, max_results=max_comments,
    )
    log.info("Fetched comments → %s", info_path.name)
    return info_path


def main() -> int:
    args = _parse_args()
    log = build_logger("peanut_react", args.verbose)

    # ── Load config (.env + config.yaml + env vars) ──────────────────
    cfg = load_config()

    video_input = args.video.strip()
    is_url = _is_youtube_url(video_input)

    # ── Resolve work directory ───────────────────────────────────────
    work_dir = Path(args.work_dir or cfg.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # ── Progress tracker ─────────────────────────────────────────────
    tracker = ProgressTracker(work_dir / cfg.progress_file)

    info_json_path = Path(args.info_json) if args.info_json else None

    # ── Extract video ID for progress tracking ───────────────────────
    video_id = video_input  # fallback to full string
    if is_url:
        try:
            from peanut_reacts.download.youtube_comments import extract_video_id
            video_id = extract_video_id(video_input)
        except ValueError:
            pass

    # Skip if already completed
    if tracker.is_completed(video_id):
        prev = tracker.get(video_id)
        log.info("Already processed: %s → %s", video_id, prev.output_path if prev else "?")
        log.info("Use --work-dir to change output or delete progress file to reprocess.")
        return 0

    # ── URL flow: download video + fetch comments ────────────────────
    if is_url:
        dl_dir = work_dir / "download"
        cookies = args.cookies_file or cfg.cookies_file

        tracker.start(video_id, url=video_input)
        tracker.update_step(video_id, "downloading")

        try:
            video_path = _download_video(video_input, dl_dir, log, cookies_file=cookies)
        except Exception as e:
            log.error("Download failed: %s", e)
            tracker.fail(video_id, str(e), step="downloading")
            return 1

        # Fetch comments via YouTube API (if key available and not skipped)
        yt_api_key = args.youtube_api_key or cfg.youtube_api_key

        if args.no_comments:
            log.info("Skipping comments (--no-comments).")
        elif info_json_path:
            log.info("Using provided info.json: %s", info_json_path)
        elif yt_api_key:
            tracker.update_step(video_id, "fetching_comments")
            try:
                info_json_path = _fetch_comments_via_api(
                    video_input, work_dir, yt_api_key,
                    args.max_comments or cfg.max_comments, log,
                )
            except Exception as e:
                log.warning("Comment fetch failed (continuing without): %s", e)
        else:
            log.warning(
                "No YouTube API key found. Set YOUTUBE_API_KEY in .env or "
                "use --youtube-api-key to enable comment analysis."
            )
    else:
        # ── Local file flow ──────────────────────────────────────────
        video_path = Path(video_input).expanduser().resolve()
        if not video_path.exists():
            log.error("Video not found: %s", video_path)
            return 2

    # ── Output path ──────────────────────────────────────────────────
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_path = video_path.with_name(f"{video_path.stem}_peanut_reacts.mp4")

    # ── Build job config (CLI flags override .env/config defaults) ───
    layout = LayoutConfig(
        facecam_position=_FACECAM_POSITION_MAP.get(
            args.facecam_position, FacecamPosition.BOTTOM_RIGHT,
        ),
        facecam_scale=args.facecam_scale,
        facecam_border_color=args.facecam_border_color,
        name_tag_enabled=bool(args.name_tag),
        name_tag_text=args.name_tag or cfg.name_tag,
        speech_position=args.speech_position,
    )

    llm_config = LLMConfig(
        provider=args.provider or cfg.llm_provider,
        model=args.model or cfg.llm_model,
        api_key=args.llm_api_key,
        temperature=args.temperature,
    )

    tts_config = TTSConfig(
        voice=args.voice or cfg.tts_voice,
        rate=args.rate or cfg.tts_rate,
    )

    if args.no_comments:
        info_json_path = None

    transcript_path = Path(args.transcript) if args.transcript else None

    # Resolve face image for Wav2Lip
    face_image = None
    if args.wav2lip:
        if args.face_image:
            face_image = Path(args.face_image).resolve()
        else:
            # Default face image location
            default_face = Path(__file__).resolve().parents[2].parent / "assets" / "peanut_face" / "realistic_neutral.png"
            if default_face.exists():
                face_image = default_face
            else:
                log.warning("--wav2lip: no face image found at %s, falling back to PIL renderer", default_face)

    import os as _os
    job = ReactionJob(
        video_path=video_path,
        output_path=output_path,
        info_json_path=info_json_path,
        llm_config=llm_config,
        max_reactions=args.max_reactions or cfg.max_reactions,
        tts_engine_type=args.tts_engine,
        tts_config=tts_config,
        elevenlabs_api_key=args.elevenlabs_key or os.environ.get("ELEVENLABS_API_KEY", ""),
        elevenlabs_voice_id=args.elevenlabs_voice,
        use_wav2lip=args.wav2lip,
        peanut_face_image=face_image,
        layout=layout,
        transcript_path=transcript_path,
        whisper_model=args.whisper_model or cfg.whisper_model,
        whisper_device=args.whisper_device or cfg.whisper_device,
        work_dir=work_dir,
        enable_diarization=args.diarize,
        hf_token=args.hf_token or _os.environ.get("HF_TOKEN", ""),
        num_speakers=args.num_speakers,
    )

    # ── Run pipeline with progress tracking ──────────────────────────
    pipeline = ReactionPipeline(log)
    tracker.update_step(video_id, "pipeline_running")

    try:
        result = pipeline.run(job)
        tracker.complete(video_id, str(result))
        log.info("Done! Output: %s", result)
    except Exception as e:
        tracker.fail(video_id, str(e), step="pipeline")
        log.error("Pipeline failed: %s", e)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    # ── Upload to YouTube (if requested) ─────────────────────────────
    if args.upload or cfg.auto_upload:
        client_secrets = args.client_secrets or cfg.youtube_client_secrets
        if not client_secrets:
            log.warning("--upload requested but no client secrets configured. Skipping upload.")
            return 0

        tracker.update_step(video_id, "uploading")
        try:
            from peanut_reacts.upload.youtube_auth import get_authenticated_service
            from peanut_reacts.upload.youtube_upload import UploadMetadata, YouTubeUploader
            from peanut_reacts.character.metadata_generator import generate_metadata
            from peanut_reacts.character.reaction_generator import create_llm_provider
            from peanut_reacts.compositing.thumbnail import generate_thumbnail
            from peanut_reacts.core.ffmpeg import get_video_duration

            # Generate metadata
            llm = create_llm_provider(llm_config)
            original_title = video_path.stem.replace("_peanut_reacts", "")
            meta = generate_metadata(llm, original_title=original_title)
            log.info("Upload title: %s", meta.title)

            # Generate thumbnail
            thumb_path = Path(result).with_suffix(".thumb.jpg")
            try:
                duration = get_video_duration(Path(result))
                generate_thumbnail(
                    Path(result), thumb_path,
                    title_text=meta.title, video_duration=duration,
                )
            except Exception as e:
                log.warning("Thumbnail failed (skipping): %s", e)
                thumb_path = None

            # Upload
            privacy = args.upload_privacy or cfg.upload_privacy or "private"
            metadata = UploadMetadata(
                title=meta.title,
                description=meta.description,
                tags=meta.tags,
                privacy_status=privacy,
                thumbnail_path=thumb_path,
            )

            service = get_authenticated_service(client_secrets)
            uploader = YouTubeUploader(service)
            upload_result = uploader.upload_video(Path(result), metadata)

            tracker.mark_uploaded(video_id, upload_result.url)
            log.info("Uploaded! %s", upload_result.url)

        except Exception as e:
            log.error("Upload failed: %s", e)
            if args.verbose:
                import traceback
                traceback.print_exc()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
