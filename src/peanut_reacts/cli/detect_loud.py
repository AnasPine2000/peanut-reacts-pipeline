#!/usr/bin/env python3
"""CLI entry point for loudness-based clip extraction."""

from __future__ import annotations

import argparse
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.analysis.loudness import LoudnessJob, LoudSegmentDetector


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract the loudest clips from videos and concatenate them.",
    )
    p.add_argument("-i", "--input-dir", default="downloads", help="Directory containing videos")
    p.add_argument("-o", "--output-dir", default="output_clips", help="Output directory for clips")
    p.add_argument("--selection-pct", type=int, default=15, help="Top N%% loudest windows (default: 15)")
    p.add_argument("--min-clip", type=int, default=5000, help="Min clip duration in ms (default: 5000)")
    p.add_argument("--max-clip", type=int, default=15000, help="Max clip duration in ms (default: 15000)")
    p.add_argument("--workers", type=int, default=8, help="Parallel workers (default: 8)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("detect_loud", args.verbose)

    job = LoudnessJob(
        input_dir=Path(args.input_dir).expanduser().resolve(),
        output_dir=Path(args.output_dir).expanduser().resolve(),
        selection_percentage=args.selection_pct,
        min_clip_duration_ms=args.min_clip,
        max_clip_duration_ms=args.max_clip,
        max_workers=args.workers,
    )

    detector = LoudSegmentDetector(log)

    try:
        result = detector.run(job)
        if result:
            log.info("Final compilation: %s", result)
        return 0
    except Exception as e:
        log.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
