"""Fetch a narrator avatar sprite set from the DiceBear API.

DiceBear (dicebear.com) is an open-source avatar library exposed as an
HTTP API. The `avataaars` style is MIT-licensed — free for commercial
use, no attribution required — and crucially supports per-feature
expression overrides (mouth / eyes / eyebrows). A fixed `seed` always
yields the SAME character, so we can render one consistent narrator in
six expressions just by varying those three parameters.

This is the copyright-safe way to "get an avatar from the internet":
a generated, openly-licensed character — not a scraped copyrighted
image.

Output: assets/narrator/narrator_{closed,open,shocked,laughing,
angry,sad}.png — exactly the filenames character/pngtuber.py expects.

Usage:
  PYTHONPATH=src python scripts/fetch_narrator_avatar.py
  PYTHONPATH=src python scripts/fetch_narrator_avatar.py --seed my-host-42
  PYTHONPATH=src python scripts/fetch_narrator_avatar.py --style big-smile

Change --seed to get a completely different character (different
hair, skin, clothes). Change --style for a different art style;
note non-avataaars styles may not support the same expression
overrides and will fall back to seed-only variation.

The DiceBear PNG endpoint caps output at 256 px, so we upscale to
512 with LANCZOS — avataaars is flat vector art and upscales cleanly
at the ~500 px size the avatar occupies in a 1080p frame.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "narrator"
API = "https://api.dicebear.com/9.x"

# expression -> avataaars feature overrides. Each dict is appended to
# the request URL. avataaars feature vocab is fixed by the library.
EXPRESSIONS = {
    "closed":   {"mouth": "serious",    "eyes": "default",   "eyebrows": "defaultNatural"},
    "open":     {"mouth": "screamOpen", "eyes": "default",   "eyebrows": "defaultNatural"},
    "shocked":  {"mouth": "screamOpen", "eyes": "surprised", "eyebrows": "raisedExcited"},
    "laughing": {"mouth": "smile",      "eyes": "happy",     "eyebrows": "raisedExcitedNatural"},
    "angry":    {"mouth": "grimace",    "eyes": "squint",    "eyebrows": "angryNatural"},
    "sad":      {"mouth": "sad",        "eyes": "cry",       "eyebrows": "sadConcerned"},
}

UPSCALE_TO = 512


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default="narrator-cashew-01",
                    help="DiceBear seed — same seed = same character")
    ap.add_argument("--style", default="avataaars",
                    help="DiceBear style (avataaars supports expressions)")
    ap.add_argument("--out", type=Path, default=OUT_DIR,
                    help="Output directory")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"DiceBear  style={args.style}  seed={args.seed!r}")
    print(f"Output:   {args.out}")
    print()

    ok = 0
    with httpx.Client(timeout=30) as client:
        for name, feats in EXPRESSIONS.items():
            url = f"{API}/{args.style}/png?seed={args.seed}&size=256"
            # avataaars-only feature overrides; harmless on other styles
            if args.style == "avataaars":
                for k, v in feats.items():
                    url += f"&{k}={v}"
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception as e:
                print(f"  narrator_{name}.png  FAILED: {e}")
                continue

            target = args.out / f"narrator_{name}.png"
            target.write_bytes(resp.content)
            # Upscale 256 -> 512 LANCZOS (flat vector art, clean 2x)
            try:
                im = Image.open(target).convert("RGBA")
                if im.width < UPSCALE_TO:
                    im = im.resize((UPSCALE_TO, UPSCALE_TO), Image.LANCZOS)
                    im.save(target)
                print(f"  narrator_{name}.png  OK  {im.size}")
                ok += 1
            except Exception as e:
                print(f"  narrator_{name}.png  saved but upscale failed: {e}")
                ok += 1
            time.sleep(0.2)   # be polite to the free API

    print()
    if ok >= 2:
        print(f"Done — {ok}/6 expressions. Minimum (closed+open) "
              f"{'met' if ok >= 2 else 'NOT met'}.")
        print("Re-run scripts/smoke_reddit_local.py to see the new character.")
        return 0
    print(f"Only {ok}/6 fetched — check your connection / the API status.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
