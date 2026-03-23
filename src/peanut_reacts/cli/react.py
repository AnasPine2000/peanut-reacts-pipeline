#!/usr/bin/env python3
"""
Master CLI entry point for the full peanut reaction pipeline.

Usage:
    peanut-react video.mp4 --provider deepseek -o output.mp4
    peanut-react video.mp4 --provider ollama --model llama3.1 --no-comments
"""

from __future__ import annotations

import argparse
from pathlib import Path

from peanut_reacts.character.reaction_generator import LLMConfig
from peanut_reacts.character.tts import TTSConfig
from peanut_reacts.compositing.layout import FacecamPosition, LayoutConfig
from peanut_reacts.compositing.reaction_video import ReactionJob, ReactionPipeline
from peanut_reacts.core.logging_setup import build_logger


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a peanut reaction video from a YouTube video.",
    )
    p.add_argument("video", help="Path to input video file")
    p.add_argument("-o", "--output", default=None, help="Output video path (default: <video>_peanut_reacts.mp4)")

    # LLM options
    llm = p.add_argument_group("LLM options")
    llm.add_argument("--provider", default="deepseek", choices=["deepseek", "groq", "ollama"])
    llm.add_argument("--model", default="", help="LLM model name")
    llm.add_argument("--api-key", default="", help="API key (or use env var)")
    llm.add_argument("--temperature", type=float, default=0.8)
    llm.add_argument("--max-reactions", type=int, default=15)

    # TTS options
    tts = p.add_argument_group("TTS options")
    tts.add_argument("--voice", default="en-US-GuyNeural", help="Edge TTS voice")
    tts.add_argument("--rate", default="+10%", help="Speaking rate")

    # Comment options
    cmt = p.add_argument_group("Comment options")
    cmt.add_argument("--info-json", default=None, help="Path to .info.json with comments")
    cmt.add_argument("--no-comments", action="store_true", help="Skip comment analysis")

    # Layout options
    lay = p.add_argument_group("Layout options")
    lay.add_argument("--facecam-scale", type=float, default=0.22, help="Facecam size as fraction of video width")
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


def main() -> int:
    args = _parse_args()
    log = build_logger("peanut_react", args.verbose)

    video_input = args.video.strip()
    is_url = video_input.startswith("http://") or video_input.startswith("https://")

    if is_url:
        # Download the video first
        log.info("URL detected — downloading video with comments ...")
        import yt_dlp

        work_dir = Path(args.work_dir) if args.work_dir else Path("peanut_work")
        work_dir.mkdir(parents=True, exist_ok=True)
        dl_dir = work_dir / "download"
        dl_dir.mkdir(exist_ok=True)

        ydl_opts = {
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": str(dl_dir / "%(title)s.%(ext)s"),
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "srt",
            "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}],
            "writeinfojson": True,
            "getcomments": True,
            "windowsfilenames": True,
            "ignoreerrors": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_input, download=True)

        if not info:
            log.error("Failed to download video.")
            return 1

        # Find the downloaded video file
        import os
        video_files = [dl_dir / f for f in os.listdir(dl_dir) if f.endswith(".mp4")]
        if not video_files:
            log.error("No video file found after download.")
            return 1
        video_path = video_files[0]
        log.info("Downloaded: %s", video_path.name)
    else:
        video_path = Path(video_input).expanduser().resolve()
        if not video_path.exists():
            log.error("Video not found: %s", video_path)
            return 2

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_path = video_path.with_name(f"{video_path.stem}_peanut_reacts.mp4")

    layout = LayoutConfig(
        facecam_position=_FACECAM_POSITION_MAP.get(args.facecam_position, FacecamPosition.BOTTOM_RIGHT),
        facecam_scale=args.facecam_scale,
        facecam_border_color=args.facecam_border_color,
        name_tag_enabled=bool(args.name_tag),
        name_tag_text=args.name_tag or "PEANUT",
        speech_position=args.speech_position,
    )

    llm_config = LLMConfig(
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        temperature=args.temperature,
    )

    tts_config = TTSConfig(voice=args.voice, rate=args.rate)

    info_json = Path(args.info_json) if args.info_json else None
    if args.no_comments:
        info_json = None  # explicitly skip

    transcript_path = Path(args.transcript) if args.transcript else None
    work_dir = Path(args.work_dir) if args.work_dir else None

    job = ReactionJob(
        video_path=video_path,
        output_path=output_path,
        info_json_path=info_json,
        llm_config=llm_config,
        max_reactions=args.max_reactions,
        tts_config=tts_config,
        layout=layout,
        transcript_path=transcript_path,
        whisper_model=args.whisper_model,
        whisper_device=args.whisper_device,
        work_dir=work_dir,
    )

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
