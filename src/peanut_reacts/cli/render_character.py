#!/usr/bin/env python3
"""CLI entry point for rendering the peanut character animation."""

from __future__ import annotations

import argparse
from pathlib import Path

from peanut_reacts.character.renderer import export_alpha_webm, export_gif, render_frames


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render an animated peanut character as GIF or WebM.")
    p.add_argument("--duration", type=float, default=30.0, help="Animation duration in seconds")
    p.add_argument("--fps", type=int, default=12, help="Frames per second (default: 12)")
    p.add_argument("--canvas", type=int, default=512, help="Canvas size in pixels")
    p.add_argument("--char-size", type=int, default=512, help="Character render size")
    p.add_argument("--seed", type=int, default=0, help="Random seed for speckle pattern")
    p.add_argument("-o", "--out", default="output", help="Output directory")
    p.add_argument("--webm", action="store_true", help="Also export alpha WebM")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    out_dir = Path(args.out)
    frames_dir = out_dir / "frames"

    print(f"[1/3] Rendering frames to: {frames_dir}")
    render_frames(frames_dir, args.duration, args.fps, args.canvas, args.char_size, args.seed)

    gif_path = out_dir / "peanut.gif"
    print(f"[2/3] Creating GIF: {gif_path}")
    export_gif(frames_dir, args.fps, gif_path)

    if args.webm:
        webm_path = out_dir / "peanut_alpha.webm"
        print(f"[3/3] Creating alpha WebM: {webm_path}")
        export_alpha_webm(frames_dir, args.fps, webm_path)
    else:
        print("[3/3] Done.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
