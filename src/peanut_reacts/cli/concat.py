#!/usr/bin/env python3
"""CLI entry point for video concatenation."""

from __future__ import annotations

import argparse
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.compositing.concatenation import concat_validated, parallel_reencode
from peanut_reacts.core.ffmpeg import concatenate_with_transitions, overlay_video


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Concatenate video files with optional transitions and overlay.")
    p.add_argument("-i", "--input-dir", required=True, help="Directory containing video files")
    p.add_argument("-o", "--output", default="concatenated.mp4", help="Output file")
    p.add_argument("--shuffle", action="store_true", help="Randomise clip order")
    p.add_argument("--transition", default=None, help="Transition video to insert between clips")
    p.add_argument("--reencode", action="store_true", help="Re-encode WebM files to MP4 first (NVENC)")
    p.add_argument("--reencode-workers", type=int, default=4, help="Parallel re-encode workers")
    p.add_argument("--facecam", default=None, help="Facecam video to overlay on final output")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("concat", args.verbose)

    input_dir = Path(args.input_dir).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()

    try:
        if args.reencode:
            temp_dir = input_dir / "temp_mp4"
            log.info("Re-encoding WebM files to MP4 ...")
            encoded = parallel_reencode(input_dir, temp_dir, workers=args.reencode_workers)
            if not encoded:
                log.error("No files encoded.")
                return 1
            # Use encoded files as input for concatenation
            from peanut_reacts.core.ffmpeg import concatenate
            if args.transition:
                concatenate_with_transitions(encoded, Path(args.transition), output)
            else:
                concatenate(encoded, output, shuffle=args.shuffle)
        else:
            if args.transition:
                import os
                from peanut_reacts.core.ffmpeg import is_valid_video
                video_files = sorted(
                    input_dir / f for f in os.listdir(input_dir)
                    if f.lower().endswith((".mkv", ".webm", ".mp4", ".avi"))
                )
                valid = [v for v in video_files if is_valid_video(v)]
                concatenate_with_transitions(valid, Path(args.transition), output)
            else:
                concat_validated(input_dir, output, shuffle=args.shuffle)

        log.info("Output: %s", output)

        if args.facecam:
            final = output.with_name(f"final_{output.name}")
            overlay_video(output, Path(args.facecam), final)
            log.info("Final with facecam: %s", final)

        return 0
    except Exception as e:
        log.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
