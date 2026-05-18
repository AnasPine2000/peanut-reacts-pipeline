"""Local smoke test — render ONE full reddit_stories video, no upload.

Runs the complete reddit_stories pipeline end-to-end on your machine:
  Reddit fetch -> DeepSeek script -> Edge TTS -> composite -> PNGtuber
  narrator avatar -> SFX + intro + outro polish -> final MP4 saved locally.

upload_service is forced to None, so NOTHING is published to YouTube or
TikTok. The output file is written to disk for you to open and review.

This is the "check it before deploy" gate. After you watch the output
and approve, we sync the VPS and the avatar goes live on the real
schedule. Until then the VPS keeps shipping the old faceless format.

Prerequisites:
  - .env present with DEEPSEEK_API_KEY + SCRAPEDO_API_TOKEN
  - assets/narrator/narrator_closed.png + narrator_open.png
    (placeholder sprites work; replace with your real AI images first
     if you want to review the real look)
  - ffmpeg on PATH

Usage:
  PYTHONPATH=src python scripts/smoke_reddit_local.py

Flags:
  --stories N      stories per video (default 1 — fast; prod uses 3)
  --no-avatar      render the faceless version for side-by-side compare
  --output PATH    where to save (default C:/tmp/reddit_smoke/)
  --min-score N    Reddit score floor (default 2000)

Typical run time: 2-4 min for a 1-story video on a CPU box.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log = logging.getLogger("smoke_reddit")

    ap = argparse.ArgumentParser()
    ap.add_argument("--stories", type=int, default=1,
                    help="Stories per video (default 1 for speed; prod=3)")
    ap.add_argument("--no-avatar", action="store_true",
                    help="Render faceless (for A/B comparison)")
    ap.add_argument("--output", type=Path, default=Path("C:/tmp/reddit_smoke"),
                    help="Output directory")
    ap.add_argument("--min-score", type=int, default=2000)
    args = ap.parse_args()

    # Load .env so DEEPSEEK_API_KEY / SCRAPEDO_API_TOKEN are available
    from peanut_reacts.scheduler.runner import _load_dotenv
    _load_dotenv()

    import os
    missing = [k for k in ("DEEPSEEK_API_KEY",) if not os.environ.get(k)]
    if missing:
        log.error("Missing env vars: %s — check your .env", missing)
        return 1
    if not os.environ.get("SCRAPEDO_API_TOKEN"):
        log.warning("SCRAPEDO_API_TOKEN not set — Reddit fetch may 403 from "
                    "this IP. If it fails, that's why.")

    # Load the reddit_stories channel config
    from peanut_reacts.scheduler.channel_config import load_channels
    cfg = load_channels(ROOT / "channels.yaml")
    channel = next((c for c in cfg.channels if c.id == "reddit_stories"), None)
    if channel is None:
        log.error("reddit_stories channel not found in channels.yaml")
        return 1

    # Resolve the narrator avatar dir (unless --no-avatar)
    narrator_avatar = "" if args.no_avatar else channel.narrator_avatar
    if narrator_avatar:
        adir = Path(narrator_avatar)
        if not adir.is_absolute():
            adir = ROOT / adir
        closed = adir / "narrator_closed.png"
        opened = adir / "narrator_open.png"
        if not (closed.exists() and opened.exists()):
            log.warning(
                "Avatar sprites missing in %s — rendering FACELESS. "
                "Generate narrator_closed.png + narrator_open.png first.",
                adir,
            )
            narrator_avatar = ""
        else:
            log.info("Avatar sprites found: %s", adir)
            narrator_avatar = str(adir)

    # Build LLM provider
    from peanut_reacts.character.reaction_generator import create_llm_provider, LLMConfig
    from peanut_reacts.core.config import load_config
    core_cfg = load_config()
    llm = create_llm_provider(LLMConfig(
        provider=cfg.settings.llm_provider,
        api_key=core_cfg.deepseek_api_key,
        model=cfg.settings.llm_model,
        temperature=0.8,
    ))

    out_dir = args.output
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 64)
    log.info("LOCAL SMOKE — reddit_stories  (NO UPLOAD)")
    log.info("  stories/video: %d   avatar: %s", args.stories,
             "ON" if narrator_avatar else "OFF (faceless)")
    log.info("  output dir:    %s", out_dir)
    log.info("=" * 64)

    from peanut_reacts.scheduler.reddit_pipeline import run_reddit_pipeline

    t0 = time.time()
    result = run_reddit_pipeline(
        output_dir=out_dir,
        llm_provider=llm,
        subreddits=channel.subreddits,
        stories_per_video=args.stories,
        min_score=args.min_score,
        voice=channel.tts_voice,
        rate=channel.tts_rate,
        character=channel.character.title() if channel.character else "Narrator",
        upload_service=None,             # <-- the key line: no upload
        upload_privacy="private",
        tags=channel.tags,
        tiktok_cookies="",               # <-- no TikTok either
        narrator_avatar=narrator_avatar,
        background_video=channel.background_video,
    )
    dt = time.time() - t0

    log.info("=" * 64)
    if result.get("video_path"):
        vp = Path(result["video_path"])
        size_mb = vp.stat().st_size / (1024 * 1024) if vp.exists() else 0
        log.info("RENDER COMPLETE in %.0fs", dt)
        log.info("  video:  %s", vp)
        log.info("  size:   %.0f MB", size_mb)
        log.info("")
        log.info("Open the file and review:")
        log.info("  [ ] avatar mouth opens/closes roughly with the narration")
        log.info("  [ ] avatar placement (bottom-left corner) doesn't cover subs")
        log.info("  [ ] avatar size feels right (not too big / too small)")
        log.info("  [ ] intro sting + outro CTA look intentional")
        log.info("  [ ] SFX (whoosh/ding) land at reasonable moments")
        log.info("  [ ] overall: would a stranger watch this?")
        log.info("")
        log.info("Tell me what to change — size, position, sprites, timing —")
        log.info("then we deploy. Nothing is live until you say so.")
        if result.get("errors"):
            log.warning("Non-fatal errors during render: %s", result["errors"])
        return 0
    else:
        log.error("RENDER FAILED after %.0fs", dt)
        log.error("  errors: %s", result.get("errors"))
        return 2


if __name__ == "__main__":
    sys.exit(main())
