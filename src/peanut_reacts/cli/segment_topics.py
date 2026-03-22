#!/usr/bin/env python3
"""CLI entry point for topic segmentation and keyword-based clip extraction."""

from __future__ import annotations

import argparse
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.analysis.topic_segmentation import segment_video_by_keywords


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Segment a video by topics and extract clips matching filename keywords.",
    )
    p.add_argument("video", help="Path to input video file")
    p.add_argument("-o", "--output-dir", default="final_segments", help="Output directory")
    p.add_argument("--threshold", type=float, default=0.7, help="Cosine similarity threshold (default: 0.7)")
    p.add_argument("--model", default="base", help="Whisper model (default: base)")
    p.add_argument("--device", default="cuda", help="Whisper device (default: cuda)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("segment_topics", args.verbose)

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        log.error("Video not found: %s", video_path)
        return 2

    output_dir = Path(args.output_dir).expanduser().resolve()

    try:
        outputs = segment_video_by_keywords(
            video_path, output_dir,
            similarity_threshold=args.threshold,
            model_name=args.model,
            device=args.device,
        )
        log.info("Created %d segment clip(s) in %s", len(outputs), output_dir)
        return 0
    except Exception as e:
        log.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
