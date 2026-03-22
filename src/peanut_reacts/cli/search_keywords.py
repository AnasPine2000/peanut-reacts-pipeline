#!/usr/bin/env python3
"""CLI entry point for keyword-based video clip extraction."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.analysis.keyword_search import KeywordClipExtractor, KeywordSearchJob


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Search video transcripts for a keyword and extract matching clips.",
    )
    p.add_argument("keyword", help="Keyword to search for in transcripts")
    p.add_argument("-i", "--input-dir", default="downloads", help="Directory containing videos")
    p.add_argument("-o", "--output-dir", default=None, help="Output directory for clips (default: downloads/keyword_clips_<keyword>)")
    p.add_argument("--mode", choices=["exact", "fuzzy", "both"], default="both", help="Search mode")
    p.add_argument("--threshold", type=float, default=0.6, help="Fuzzy match threshold (0-1)")
    p.add_argument("--clip-min", type=int, default=20, help="Min clip duration in seconds")
    p.add_argument("--clip-max", type=int, default=30, help="Max clip duration in seconds")
    p.add_argument("--model", default="base", help="Whisper model name (default: base)")
    p.add_argument("--device", default="cuda", help="Whisper device (default: cuda)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("keyword_search", args.verbose)

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        log.error("Input directory does not exist: %s", input_dir)
        return 2

    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
    else:
        safe_kw = "".join(c for c in args.keyword if c.isalnum() or c.isspace()).strip().replace(" ", "_")
        output_dir = input_dir / f"keyword_clips_{safe_kw}"

    job = KeywordSearchJob(
        keyword=args.keyword,
        input_dir=input_dir,
        output_dir=output_dir,
        search_mode=args.mode,
        fuzzy_threshold=args.threshold,
        clip_min_seconds=args.clip_min,
        clip_max_seconds=args.clip_max,
        model_name=args.model,
        device=args.device,
    )

    extractor = KeywordClipExtractor(log)

    try:
        result = extractor.run(job)
        if result:
            log.info("Combined video: %s", result)
        return 0
    except Exception as e:
        log.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
