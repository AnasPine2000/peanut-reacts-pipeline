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

    # Peanut rendering
    pnt = p.add_argument_group("Peanut options")
    pnt.add_argument("--peanut-scale", type=float, default=0.20, help="Peanut size as fraction of video width")
    pnt.add_argument("--peanut-position", default="bottom-right",
                     choices=["bottom-right", "bottom-left", "top-right", "top-left"])

    # Transcript
    p.add_argument("--transcript", default=None, help="Path to transcript JSON or SRT")
    p.add_argument("--whisper-model", default="base", help="Whisper model for transcription")
    p.add_argument("--whisper-device", default="cuda")

    # General
    p.add_argument("--work-dir", default=None, help="Working directory for intermediate files")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


_POSITION_MAP = {
    "bottom-right": ("W-w-20", "H-h-20"),
    "bottom-left": ("20", "H-h-20"),
    "top-right": ("W-w-20", "20"),
    "top-left": ("20", "20"),
}


def main() -> int:
    args = _parse_args()
    log = build_logger("peanut_react", args.verbose)

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        log.error("Video not found: %s", video_path)
        return 2

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_path = video_path.with_name(f"{video_path.stem}_peanut_reacts.mp4")

    peanut_x, peanut_y = _POSITION_MAP.get(args.peanut_position, ("W-w-20", "H-h-20"))

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
        peanut_scale=args.peanut_scale,
        peanut_x=peanut_x,
        peanut_y=peanut_y,
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
