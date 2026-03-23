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
from peanut_reacts.core.logging_setup import build_logger


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
    tts.add_argument("--voice", default="en-US-GuyNeural", help="Edge TTS voice")
    tts.add_argument("--rate", default="+10%", help="Speaking rate (e.g. +10%%)")

    # Comment options
    cmt = p.add_argument_group("Comment options")
    cmt.add_argument("--info-json", default=None, help="Path to .info.json with comments")
    cmt.add_argument("--no-comments", action="store_true", help="Skip comment analysis")
    cmt.add_argument("--youtube-api-key", default=None,
                     help="YouTube Data API key (or set YOUTUBE_API_KEY env var)")
    cmt.add_argument("--max-comments", type=int, default=500,
                     help="Max comments to fetch via YouTube API (default: 500)")

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


def _download_video(url: str, dl_dir: Path, log) -> Path:
    """Download video + subtitles via yt-dlp (no comments — API handles those)."""
    import yt_dlp

    dl_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": str(dl_dir / "%(title)s.%(ext)s"),
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "subtitlesformat": "srt",
        "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}],
        "windowsfilenames": True,
        "ignoreerrors": True,
    }

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

    video_input = args.video.strip()
    is_url = _is_youtube_url(video_input)

    # ── Resolve work directory ───────────────────────────────────────
    work_dir = Path(args.work_dir) if args.work_dir else Path("peanut_work")
    work_dir.mkdir(parents=True, exist_ok=True)

    info_json_path = Path(args.info_json) if args.info_json else None

    # ── URL flow: download video + fetch comments ────────────────────
    if is_url:
        dl_dir = work_dir / "download"

        try:
            video_path = _download_video(video_input, dl_dir, log)
        except Exception as e:
            log.error("Download failed: %s", e)
            return 1

        # Fetch comments via YouTube API (if key available and not skipped)
        yt_api_key = args.youtube_api_key or os.environ.get("YOUTUBE_API_KEY", "")

        if args.no_comments:
            log.info("Skipping comments (--no-comments).")
        elif info_json_path:
            log.info("Using provided info.json: %s", info_json_path)
        elif yt_api_key:
            try:
                info_json_path = _fetch_comments_via_api(
                    video_input, work_dir, yt_api_key,
                    args.max_comments, log,
                )
            except Exception as e:
                log.warning("Comment fetch failed (continuing without): %s", e)
        else:
            log.warning(
                "No YouTube API key found. Set YOUTUBE_API_KEY env var or "
                "use --youtube-api-key to enable comment analysis. "
                "Proceeding without comments."
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

    # ── Build job config ─────────────────────────────────────────────
    layout = LayoutConfig(
        facecam_position=_FACECAM_POSITION_MAP.get(
            args.facecam_position, FacecamPosition.BOTTOM_RIGHT,
        ),
        facecam_scale=args.facecam_scale,
        facecam_border_color=args.facecam_border_color,
        name_tag_enabled=bool(args.name_tag),
        name_tag_text=args.name_tag or "PEANUT",
        speech_position=args.speech_position,
    )

    llm_config = LLMConfig(
        provider=args.provider,
        model=args.model,
        api_key=args.llm_api_key,
        temperature=args.temperature,
    )

    tts_config = TTSConfig(voice=args.voice, rate=args.rate)

    if args.no_comments:
        info_json_path = None

    transcript_path = Path(args.transcript) if args.transcript else None

    job = ReactionJob(
        video_path=video_path,
        output_path=output_path,
        info_json_path=info_json_path,
        llm_config=llm_config,
        max_reactions=args.max_reactions,
        tts_config=tts_config,
        layout=layout,
        transcript_path=transcript_path,
        whisper_model=args.whisper_model,
        whisper_device=args.whisper_device,
        work_dir=work_dir,
    )

    # ── Run pipeline ─────────────────────────────────────────────────
    pipeline = ReactionPipeline(log)

    try:
        result = pipeline.run(job)
        log.info("Done! Output: %s", result)
        return 0
    except Exception as e:
        log.error("Pipeline failed: %s", e)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
