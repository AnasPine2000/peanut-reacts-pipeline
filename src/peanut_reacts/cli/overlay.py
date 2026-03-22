#!/usr/bin/env python3
"""CLI entry point for overlaying GIF/video onto a base video."""

from __future__ import annotations

import argparse
from pathlib import Path

from peanut_reacts.compositing.gif_overlay import overlay_gif


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Overlay a GIF or video onto a base video.")
    p.add_argument("base_video", help="Path to the base video")
    p.add_argument("overlay", help="Path to the GIF or overlay video")
    p.add_argument("-o", "--output", default="overlay_output.mp4", help="Output file path")
    p.add_argument("--scale", type=float, default=1.25, help="Overlay scale as fraction of base width")
    p.add_argument("--x", default="W-w-40", help="X position expression (default: W-w-40)")
    p.add_argument("--y", default="H-h-40", help="Y position expression (default: H-h-40)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        result = overlay_gif(
            Path(args.base_video),
            Path(args.overlay),
            Path(args.output),
            scale=args.scale,
            x=args.x,
            y=args.y,
        )
        print(f"Done: {result}")
        return 0
    except Exception as e:
        print(f"Failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
