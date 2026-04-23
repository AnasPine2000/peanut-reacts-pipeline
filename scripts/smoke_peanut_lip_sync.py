"""End-to-end smoke test for Peanut lip sync (Phase 1A).

Takes one TTS audio + one Peanut body video + the sprite directory,
runs Rhubarb, composites the talking Peanut, and writes an mp4.

If the sprites aren't in place yet, it still prints the viseme cues
so you can see what Rhubarb would drive, then exits with a helpful
message. Useful for "did my audio parse OK" before committing to
drawing all 9 sprites.

Usage:
  # Cue dump only (before sprites exist):
  PYTHONPATH=src python scripts/smoke_peanut_lip_sync.py \\
      --audio C:/tmp/live_rx_uk_v5/tts/reaction_012.mp3

  # Full composite (after 9 sprites are in assets/peanut_mouth_sprites/):
  PYTHONPATH=src python scripts/smoke_peanut_lip_sync.py \\
      --audio C:/tmp/live_rx_uk_v5/tts/reaction_012.mp3 \\
      --body  C:/tmp/live_rx_uk_v5/reaction_clips/reaction_012.mp4 \\
      --out   C:/tmp/lipsync_smoke.mp4
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from peanut_reacts.character.rhubarb_lip_sync import (  # noqa: E402
    SPRITE_FILENAMES,
    composite_talking_peanut,
    generate_mouth_cues,
    verify_sprite_pack,
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, type=Path,
                    help="TTS audio file (mp3/wav)")
    ap.add_argument("--body", type=Path, default=None,
                    help="Peanut body video (mp4). Omit for cue-dump-only mode.")
    ap.add_argument("--out", type=Path, default=Path("C:/tmp/lipsync_smoke.mp4"),
                    help="Output mp4 path")
    ap.add_argument(
        "--sprites",
        type=Path,
        default=ROOT / "assets" / "peanut_mouth_sprites",
        help="Directory containing the 9 mouth sprite PNGs",
    )
    ap.add_argument("--mouth-x", type=int, default=0,
                    help="X pixel where mouth sprite overlays on body")
    ap.add_argument("--mouth-y", type=int, default=0,
                    help="Y pixel where mouth sprite overlays on body")
    args = ap.parse_args()

    print(f"Audio:   {args.audio}")
    print(f"Sprites: {args.sprites}")

    # Always do the Rhubarb parse — informative even without sprites
    cues = generate_mouth_cues(args.audio)
    if not cues:
        print("\n  [FAIL] Rhubarb returned no cues. Check the audio file and that "
              "third_party/Rhubarb-Lip-Sync-1.14.0-Windows/rhubarb.exe exists.")
        return 1

    print(f"\n  Rhubarb produced {len(cues)} cues.")
    print(f"  First 10:")
    for c in cues[:10]:
        print(f"    {c.start:5.2f}s -> {c.end:5.2f}s  {c.viseme}  ({c.duration:.2f}s)")
    if len(cues) > 10:
        print(f"    ... and {len(cues) - 10} more")

    # Sprite pack check
    ok, missing = verify_sprite_pack(args.sprites)
    if not ok:
        print(f"\n  [WARN] Sprite pack incomplete. Missing {len(missing)}:")
        for m in missing:
            print(f"    {m}")
        print(f"\n  Expected filenames (see assets/peanut_mouth_sprites/PROMPTS.md):")
        for viseme, fname in SPRITE_FILENAMES.items():
            mark = "OK " if (args.sprites / fname).exists() else "... "
            print(f"    {mark}{viseme}  {fname}")
        print("\n  Run again with the full pack + a --body video to produce an mp4.")
        return 2

    if not args.body:
        print("\n  [OK] Sprite pack looks good. Pass --body VIDEO.mp4 and --out to "
              "produce the composite.")
        return 0

    # Full composite
    print(f"\n  Body:  {args.body}")
    print(f"  Out:   {args.out}")
    result = composite_talking_peanut(
        audio_path=args.audio,
        body_video_path=args.body,
        sprites_dir=args.sprites,
        output_path=args.out,
        mouth_x=args.mouth_x,
        mouth_y=args.mouth_y,
    )
    if result:
        size_mb = result.stat().st_size / (1024 * 1024)
        print(f"\n  [OK] Wrote {result} ({size_mb:.1f} MB)")
        return 0
    print("\n  [FAIL] Composite failed. Check logs above for ffmpeg errors.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
