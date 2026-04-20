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
CLIP_RECIPES = {
    "peanut_idle": {
        "source_image": "cartoon_v1.png",
        "motion_prompt": "cartoon peanut character idling calmly, breathing gently, "
                         "slight head sway, relaxed expression, eyes blinking "
                         "naturally, camera static",
        "duration_s": 5,
    },
    "peanut_laughing": {
        "source_image": "cartoon_amused.png",
        "motion_prompt": "cartoon peanut character bursting into laughter, head thrown "
                         "back then forward, shoulders shaking, mouth wide open, "
                         "teeth visible, genuine joy, camera static",
        "duration_s": 5,
    },
    "peanut_excited": {
        "source_image": "cartoon_excited.png",
        "motion_prompt": "cartoon peanut character celebrating excitedly, little fists "
                         "pumping up and down, bouncing in place, huge smile, eyes "
                         "sparkling with joy, camera static",
        "duration_s": 5,
    },
    "peanut_celebrating": {
        "source_image": "cartoon_excited.png",
        "motion_prompt": "cartoon peanut character raising both arms triumphantly, "
                         "spinning once, wide open-mouth smile, victorious pose, "
                         "camera static",
        "duration_s": 5,
    },
    "peanut_shocked": {
        "source_image": "cartoon_shocked.png",
        "motion_prompt": "cartoon peanut character gasping in shock, subtle head shake, "
                         "eyes widening further, mouth hanging open, small backward "
                         "recoil motion, camera static",
        "duration_s": 5,
    },
    "peanut_scared": {
        "source_image": "cartoon_shocked.png",
        "motion_prompt": "cartoon peanut character trembling nervously, slight "
                         "side-to-side shake, hesitant eye movement, apprehensive "
                         "expression, camera static",
        "duration_s": 5,
    },
    "peanut_confused": {
        "source_image": "cartoon_confused.png",
        "motion_prompt": "cartoon peanut character tilting head in confusion from one "
                         "side to the other, one eyebrow raised, slow quizzical "
                         "motion, camera static",
        "duration_s": 5,
    },
    "peanut_facepalm": {
        "source_image": "cartoon_sarcastic.png",
        "motion_prompt": "cartoon peanut character slowly shaking head in exasperation, "
                         "heavy sigh, disappointed expression, looking away then "
                         "forward, camera static",
        "duration_s": 5,
    },
    "peanut_sarcastic": {
        "source_image": "cartoon_sarcastic.png",
        "motion_prompt": "cartoon peanut character doing a slow sarcastic eye-roll, "
                         "unimpressed side-eye, flat deadpan expression, minimal "
                         "motion, camera static",
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
