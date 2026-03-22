#!/usr/bin/env python3
"""CLI entry point for extracting comment highlights from yt-dlp info.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.analysis.comments import extract_comment_highlights, highlights_to_dicts


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract timestamped comment highlights from a yt-dlp .info.json file.",
    )
    p.add_argument("info_json", help="Path to the .info.json file")
    p.add_argument("-o", "--output", default=None, help="Output highlights JSON (default: <video>_highlights.json)")
    p.add_argument("--max", type=int, default=10, help="Maximum highlights to extract (default: 10)")
    p.add_argument("--window", type=float, default=15.0, help="Cluster window in seconds (default: 15)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("extract_comments", args.verbose)

    info_path = Path(args.info_json).expanduser().resolve()
    if not info_path.exists():
        log.error("File not found: %s", info_path)
        return 2

    highlights = extract_comment_highlights(
        info_path, cluster_window=args.window, max_highlights=args.max,
    )

    if not highlights:
        log.warning("No highlights extracted.")
        return 0

    output_path = Path(args.output) if args.output else info_path.with_name(
        info_path.name.replace(".info.json", "_highlights.json"),
    )
    output_path.write_text(json.dumps(highlights_to_dicts(highlights), indent=2), encoding="utf-8")
    log.info("Saved %d highlight(s) to %s", len(highlights), output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
