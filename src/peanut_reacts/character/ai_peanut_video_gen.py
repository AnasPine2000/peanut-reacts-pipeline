"""AI image-to-video generator for the 3D Peanut character library.

Takes the existing cartoon_*.png stills (3D Pixar-style Peanut with shell
texture, expressive face, rainbow beanie) and runs each through fal.ai's
Kling 1.6 Standard image-to-video model with an emotion-specific motion
prompt. Produces proper 5-second animated loops with real body motion,
breathing, mouth movement — the "AI fruit video" quality tier.

Cost: ~$0.175 per 5s clip × 9 emotion targets = ~$1.58 one-time. Results
are cached under data/ai_peanut_clips_3d/ so downstream pipelines can
reuse them forever.

Model selection rationale (Kling 1.6 Standard):
- Preserves character identity from input image (no drift)
- Produces subtle idle motion + emotion-appropriate body language
- Good balance of quality vs. cost (Pro is 2x price for marginal gain
  on cartoon content that doesn't need photorealism)

If we want premium later:
- fal-ai/kling-video/v1.6/pro/image-to-video  — $0.35/5s
- fal-ai/kling-video/v2/master/image-to-video  — $1.40/5s (cinematic)
- fal-ai/hailuo-02/pro/image-to-video          — MiniMax variant

Env: FAL_KEY (or FAL_API_KEY) must be set. See fal.ai dashboard.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CARTOON_ASSETS = PROJECT_ROOT / "assets" / "peanut_face"

# Which Kling model endpoint to hit. Standard tier is good enough for
# cartoon character loops. Upgrade to pro/v2/master later if needed.
FAL_MODEL = "fal-ai/kling-video/v1.6/standard/image-to-video"

# One recipe per clip we need to produce. source_image is the still PNG
# from assets/peanut_face/; motion_prompt directs Kling on what to animate.
# Some emotion targets reuse the same still with different motion prompts.
# IMPORTANT: all 9 clips use the SAME source image (cartoon_amused.png).
# Earlier versions sourced from 6 different PNGs, which meant each emotion
# looked like a different character (different beanie colors, different
# body scales, different backgrounds). The transitions between clips in
# the final episode were jarring because the *character itself* was
# changing between emotions, not just the animation.
#
# Now every clip is a Kling animation of the exact same cartoon Peanut
# base frame. Only the motion prompt varies; Kling morphs the expression
# and body language while preserving the character's shell, beanie,
# eyes, proportions, and green background. Result: perfect cross-clip
# continuity so the idle->reaction switch feels like a single character
# changing mood, not a character swap.
_SINGLE_SOURCE_IMAGE = "cartoon_amused.png"

CLIP_RECIPES = {
    "peanut_idle": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character idling calmly with a relaxed "
                         "neutral expression, breathing gently, slight head sway, "
                         "small natural blinks, arms relaxed at sides, camera static, "
                         "preserve exact character design",
        "duration_s": 5,
    },
    "peanut_laughing": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character bursting into genuine laughter, "
                         "head thrown back then forward, shoulders shaking, mouth "
                         "wide open, teeth and tongue visible, arms clutching belly, "
                         "pure joy, camera static, preserve exact character design",
        "duration_s": 5,
    },
    "peanut_excited": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character celebrating excitedly, both "
                         "little fists pumping up and down, bouncing in place with "
                         "energy, huge smile, eyes sparkling, camera static, "
                         "preserve exact character design",
        "duration_s": 5,
    },
    "peanut_celebrating": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character raising both arms triumphantly in "
                         "the air, wide open-mouth smile, victorious cheering pose, "
                         "slight spin, camera static, preserve exact character design",
        "duration_s": 5,
    },
    "peanut_shocked": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character gasping in shock, eyes wide open, "
                         "mouth hanging open in surprise, small backward recoil, "
                         "hands flying up near face, camera static, preserve exact "
                         "character design",
        "duration_s": 5,
    },
    "peanut_scared": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character trembling nervously, body shaking "
                         "side to side, hesitant fearful eye movement, hands held "
                         "close, apprehensive expression, camera static, preserve "
                         "exact character design",
        "duration_s": 5,
    },
    "peanut_confused": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character tilting head in confusion side to "
                         "side, one eyebrow raised skeptically, hand scratching top "
                         "of head, slow quizzical motion, camera static, preserve "
                         "exact character design",
        "duration_s": 5,
    },
    "peanut_facepalm": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character bringing one hand to forehead in "
                         "facepalm gesture, shaking head slowly in exasperation, "
                         "heavy disappointed sigh, looking away, camera static, "
                         "preserve exact character design",
        "duration_s": 5,
    },
    "peanut_sarcastic": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character doing a slow sarcastic eye-roll, "
                         "arms crossed, unimpressed side-eye, flat deadpan expression, "
                         "minimal body motion, camera static, preserve exact "
                         "character design",
        "duration_s": 5,
    },

    # ── Phase 2 special animations (Peanut-specific visual gags the
    # research said we can do that KSI can't — cartoon is a feature here).
    # Used by DeepSeek when a verdict calls for an escalated physical
    # reaction beyond the standard 6 emotions.
    "peanut_shell_crack": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character with a visible hairline crack "
                         "appearing on shell, crack slowly growing larger across the "
                         "body, wide startled eyes, shell vibration, dust particles "
                         "falling, dramatic comedic tension, camera static, preserve "
                         "exact character design",
        "duration_s": 5,
    },
    "peanut_mouth_clamp": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character trying desperately not to laugh, "
                         "both small hands clamped over the mouth, cheeks puffed out, "
                         "eyes squeezed shut, shoulders trembling from held-in "
                         "laughter, camera static, preserve exact character design",
        "duration_s": 5,
    },
    "peanut_spit_take": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character performing a huge comedic "
                         "spit-take, mouth erupting with a spray of peanut butter, "
                         "eyes bulging in shocked disbelief, arms flying up, "
                         "head jerking back, camera static, preserve exact character "
                         "design",
        "duration_s": 5,
    },
    "peanut_explode": {
        "source_image": _SINGLE_SOURCE_IMAGE,
        "motion_prompt": "cartoon peanut character's shell splitting open and the "
                         "inner kernel popping out upward in surprise then snapping "
                         "back into place with a satisfied smile, comedic spring "
                         "animation, camera static, preserve exact character design",
        "duration_s": 5,
    },
}


@dataclass
class FalConfig:
    api_key: Optional[str] = None       # falls back to FAL_KEY env var
    model: str = FAL_MODEL
    negative_prompt: str = (
        "blurry, distorted, extra limbs, extra characters, text, watermark, "
        "logo, low quality, deformed peanut, photorealistic human face, "
        "scene change, multiple peanuts"
    )


def _get_api_key(cfg: FalConfig) -> str:
    key = cfg.api_key or os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY", "")
    if not key:
        raise RuntimeError(
            "No fal.ai API key found. Set FAL_KEY in .env or pass cfg.api_key."
        )
    return key


def _upload_to_fal(image_path: Path, api_key: str) -> str:
    """Upload a local image to fal.ai storage, return the public URL.

    Uses the fal_client library which handles multipart upload + auth.
    """
    import fal_client

    os.environ["FAL_KEY"] = api_key   # fal_client reads from env
    url = fal_client.upload_file(str(image_path))
    log.debug("Uploaded %s -> %s", image_path.name, url[:80])
    return url


def _submit_image_to_video(
    image_url: str, prompt: str, duration_s: int,
    cfg: FalConfig, api_key: str,
) -> str:
    """Submit the image-to-video job, return the resulting video URL."""
    import fal_client

    os.environ["FAL_KEY"] = api_key

    handler = fal_client.submit(
        cfg.model,
        arguments={
            "prompt": prompt,
            "image_url": image_url,
            "duration": str(duration_s),
            "aspect_ratio": "1:1",      # square — matches our facecam PIP
            "negative_prompt": cfg.negative_prompt,
            "cfg_scale": 0.5,            # character preservation weight
        },
    )

    # Block until complete (5-30s for Kling standard)
    result = handler.get()
    video_url = result.get("video", {}).get("url") if isinstance(result.get("video"), dict) else result.get("video")
    if not video_url:
        raise RuntimeError(f"fal.ai returned no video url: {result}")
    return video_url


def _download_video(url: str, output_path: Path) -> Path:
    """Download the Kling result to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=180, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(output_path, "wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=65536):
                fh.write(chunk)
    return output_path


def generate_ai_peanut_clip(
    clip_name: str,
    output_path: Path,
    cfg: Optional[FalConfig] = None,
    assets_dir: Path = CARTOON_ASSETS,
    force: bool = False,
) -> Optional[Path]:
    """Generate one AI-animated Peanut reaction clip from a 3D still.

    Args:
        clip_name: One of CLIP_RECIPES keys.
        output_path: Where to write the MP4.
        cfg: Override default fal config (model, prompt, etc.).
        assets_dir: Directory containing cartoon_*.png source stills.
        force: If False and output_path exists, skip regeneration (caching).

    Returns the output path on success, None on failure.
    """
    cfg = cfg or FalConfig()
    if clip_name not in CLIP_RECIPES:
        raise ValueError(f"Unknown clip_name {clip_name!r}. Known: {list(CLIP_RECIPES)}")

    if output_path.exists() and not force:
        log.info("Cached: %s (skip regen)", output_path.name)
        return output_path

    recipe = CLIP_RECIPES[clip_name]
    source_image = assets_dir / recipe["source_image"]
    if not source_image.exists():
        log.error("Missing source image: %s", source_image)
        return None

    api_key = _get_api_key(cfg)

    try:
        log.info("[AI] Uploading %s...", source_image.name)
        image_url = _upload_to_fal(source_image, api_key)

        log.info("[AI] Generating %s (%ds) ...", clip_name, recipe["duration_s"])
        video_url = _submit_image_to_video(
            image_url=image_url,
            prompt=recipe["motion_prompt"],
            duration_s=recipe["duration_s"],
            cfg=cfg,
            api_key=api_key,
        )

        log.info("[AI] Downloading %s ...", clip_name)
        _download_video(video_url, output_path)
        log.info("[AI] Saved %s (%.1f KB)", output_path.name, output_path.stat().st_size / 1024)
        return output_path

    except Exception as e:
        log.error("[AI] %s failed: %s", clip_name, e)
        return None


def regenerate_all_ai_clips(
    output_dir: Path,
    cfg: Optional[FalConfig] = None,
    force: bool = False,
) -> dict[str, Optional[Path]]:
    """Generate every clip in CLIP_RECIPES via fal.ai Kling.

    Results are cached (skipped on re-run unless force=True). Safe to
    resume if a previous run crashed mid-way.

    Returns {clip_name: path_or_None}. Total cost: ~9 * $0.175 = ~$1.58
    for Kling 1.6 Standard at 5s per clip.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Optional[Path]] = {}
    for clip_name in CLIP_RECIPES:
        out = output_dir / f"{clip_name}.mp4"
        results[clip_name] = generate_ai_peanut_clip(clip_name, out, cfg=cfg, force=force)
    return results


def postprocess_chromakey_ready(input_path: Path, output_path: Path) -> Optional[Path]:
    """Re-encode a Kling-generated clip so chromakey pipelines are happy.

    Kling outputs H.264 in variable formats depending on pipeline; normalize
    to yuv420p + libx264 + 25fps + 512x512 to match the downstream facecam
    expectations (which were built around flat_peanut_renderer output).
    """
    if not input_path.exists():
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", "scale=512:512:force_original_aspect_ratio=decrease,"
                   "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00FF00,"
                   "setsar=1:1,fps=25",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output_path),
        ], check=True, capture_output=True, timeout=180)
        return output_path
    except subprocess.CalledProcessError as e:
        log.error("Postprocess failed: %s", e.stderr.decode("utf-8", errors="replace")[-200:])
        return None
