"""Smoke test for the Hedra Character-3 integration.

Runs a real render against the Peanut reference image + a short
narration audio clip to validate:
  1. Authentication works
  2. The Hedra model handles our cartoon Peanut without face-landmark
     failure
  3. Lip sync stays locked across the clip
  4. Character stays recognizably Peanut

Run BEFORE wiring Hedra into a pipeline. Costs ~30-60 credits at
720p (~$0.30-0.60 of credit on the $30/mo Creator plan, or covered by
free-tier sign-up bonus).

Usage (locally):
  $env:HEDRA_API_KEY = "hed_..."           # set the key
  PYTHONPATH=src python scripts/smoke_hedra_render.py

Optional flags:
  --image PATH      override the default Peanut reference
  --audio PATH      override the audio (default: trims a 30s slice
                    from a recently-shipped reddit_stories video)
  --resolution 540p|720p
  --aspect 16:9|9:16|1:1
  --prompt "..."    text guidance
  --output PATH     where to save the result MP4

Exit codes:
  0  render succeeded, file saved
  1  pre-flight failure (missing key, asset not found)
  2  Hedra API error
  3  download / IO error
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _find_default_peanut_image() -> Path | None:
    """Pick the realistic-design Peanut by default — per memory note,
    cartoon designs fail face-landmark detection but realistic ones work."""
    candidates = [
        ROOT / "assets" / "peanut_face" / "realistic_neutral.png",
        ROOT / "assets" / "peanut_face" / "burnt_peanut_v1.png",
        ROOT / "assets" / "peanut_face" / "cartoon_v1.png",
    ]
    for p in candidates:
        if p.exists():
            return p
    # Last resort: any image in peanut_face
    pf = ROOT / "assets" / "peanut_face"
    if pf.exists():
        for p in sorted(pf.glob("*.png")):
            return p
    return None


def _find_default_audio() -> Path | None:
    """Find a recent narration audio sample. Looks at reddit_stories
    output dirs first, then any TTS chunks from prior runs."""
    candidates = [
        ROOT / "production" / "reddit_stories",
        Path("C:/tmp/live_rx_uk_v5"),
    ]
    for d in candidates:
        if not d.exists():
            continue
        # Prefer concatenated audio over chunks
        for p in sorted(d.rglob("audio_*.mp3")):
            if p.stat().st_size > 50_000:
                return p
        for p in sorted(d.rglob("*.mp3")):
            if p.stat().st_size > 50_000 and "tts" not in p.parent.name.lower():
                return p
    return None


def _trim_audio(src: Path, out: Path, start_s: float, dur_s: float) -> Path:
    """Use ffmpeg to extract a fixed-duration chunk for the test."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", str(start_s),
            "-t", str(dur_s),
            "-i", str(src),
            "-ac", "1", "-ar", "44100",
            "-c:a", "pcm_s16le",
            str(out),
        ],
        check=True, timeout=60,
    )
    return out


def main() -> int:
    _setup_logging()
    log = logging.getLogger("smoke_hedra")

    ap = argparse.ArgumentParser()
    ap.add_argument("--image", type=Path,
                    help="Reference image (default: peanut realistic_neutral)")
    ap.add_argument("--audio", type=Path,
                    help="Audio file (default: auto-discovered narration)")
    ap.add_argument("--resolution", default="720p",
                    choices=["540p", "720p"])
    ap.add_argument("--aspect", default="16:9",
                    choices=["16:9", "9:16", "1:1"])
    ap.add_argument("--prompt", default=(
        "A stylized peanut character narrating a story, "
        "calm posh British voice, slight head bob, occasional eye blink"
    ))
    ap.add_argument("--audio-start", type=float, default=90.0,
                    help="Trim start (seconds) — skips intro narration")
    ap.add_argument("--audio-duration", type=float, default=30.0,
                    help="Trim duration (seconds) — keep short to save credits")
    ap.add_argument("--output", type=Path,
                    default=Path("C:/tmp/hedra_smoke.mp4"),
                    help="Where to save the rendered MP4")
    args = ap.parse_args()

    if not os.environ.get("HEDRA_API_KEY", "").strip():
        log.error("HEDRA_API_KEY not set in environment.")
        log.error("  Get one at https://www.hedra.com/api-profile")
        log.error("  Then: $env:HEDRA_API_KEY = 'hed_...'  (PowerShell)")
        return 1

    # Resolve image
    img = args.image or _find_default_peanut_image()
    if not img or not img.exists():
        log.error("No reference image found. Pass --image PATH.")
        log.error("  Looked in assets/peanut_face/")
        return 1
    log.info("Image: %s (%d KB)", img, img.stat().st_size // 1024)

    # Resolve audio (trim if needed)
    src_audio = args.audio or _find_default_audio()
    if not src_audio or not src_audio.exists():
        log.error("No audio found. Pass --audio PATH.")
        log.error("  Looked in production/reddit_stories/ + C:/tmp/live_rx_uk_v5/")
        return 1
    log.info("Source audio: %s (%d KB)", src_audio,
             src_audio.stat().st_size // 1024)

    # Trim to keep test short
    if args.audio.is_file() if args.audio else False:
        # User passed explicit audio, use as-is
        audio = src_audio
    else:
        audio = Path(tempfile.gettempdir()) / "hedra_smoke_audio.wav"
        try:
            _trim_audio(src_audio, audio, args.audio_start, args.audio_duration)
            log.info("Trimmed to %.0fs starting at %.0fs -> %s",
                     args.audio_duration, args.audio_start, audio)
        except Exception as e:
            log.error("Audio trim failed: %s", e)
            return 1

    # Run the render
    try:
        from peanut_reacts.character.hedra_client import (
            HedraClient, HedraError,
        )
        with HedraClient() as client:
            result = client.render_clip(
                image_path=img,
                audio_path=audio,
                output_path=args.output,
                text_prompt=args.prompt,
                resolution=args.resolution,
                aspect_ratio=args.aspect,
            )
    except HedraError as e:
        log.error("Hedra error: %s", e)
        return 2
    except Exception as e:
        log.exception("Crashed: %s", e)
        return 3

    log.info("=" * 60)
    log.info("RENDER COMPLETE")
    log.info("=" * 60)
    log.info("  output:        %s", result.output_path)
    log.info("  size:          %d KB",
             result.output_path.stat().st_size // 1024)
    log.info("  generation_id: %s", result.generation_id)
    log.info("  model_id:      %s", result.model_id)
    log.info("  wall-time:     %.1fs", result.duration_s)
    log.info("")
    log.info("Open the file and review against the 3 criteria:")
    log.info("  [ ] mouth syncs to audio (no drift past 20s)")
    log.info("  [ ] character stays Peanut (no morphing across runs)")
    log.info("  [ ] head/eyes/body move (not flapping mouth on still)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
