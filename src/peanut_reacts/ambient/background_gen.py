"""Background generator for ambient videos.

Three approaches:
1. AI-generated still (Flux/SD via fal.ai or Replicate) + FFmpeg parallax animation
2. Provided still image + FFmpeg parallax/particles
3. Solid color fallback for testing

Output: looping MP4 at 1920x1080 with subtle motion that feels alive.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ── Theme prompts for AI generation ────────────────────────────────

THEME_PROMPTS = {
    "rainy_cafe": (
        "cozy cafe interior at night, rain streaks on large window, "
        "warm orange lights, steaming coffee on wooden table, "
        "ambient atmosphere, painted illustration style, lofi aesthetic, "
        "depth of field, soft glow"
    ),
    "fireplace_cabin": (
        "cozy log cabin interior with crackling fireplace, snow outside window, "
        "warm firelight, books and tea on table, ambient atmosphere, "
        "painted illustration style, peaceful, depth of field"
    ),
    "ocean_night": (
        "moonlit ocean at night, gentle waves, distant lighthouse, "
        "stars in sky, calm peaceful atmosphere, painted illustration style, "
        "deep blue palette, soft glow, ambient"
    ),
    "library_lamp": (
        "ancient library at night, single oil lamp on wooden desk, "
        "stacks of books, leather chair, dust motes in lamplight, "
        "dark academia aesthetic, painted style, peaceful"
    ),
    "tokyo_rain": (
        "tokyo street at night, neon signs reflected on wet pavement, "
        "umbrella silhouettes, rain falling, vapor wave colors, "
        "anime illustration style, lofi aesthetic"
    ),
    "forest_dusk": (
        "deep forest clearing at dusk, golden sunlight through trees, "
        "moss-covered logs, gentle mist, painted illustration style, "
        "ghibli aesthetic, peaceful"
    ),
    "spaceship_drift": (
        "interior of cozy spaceship cockpit drifting in nebula, "
        "purple and blue stars outside windows, dim console lights, "
        "sci-fi cozy aesthetic, painted illustration"
    ),
    "nordic_cabin": (
        "nordic mountain cabin at sunset, snow covered roof, smoke from chimney, "
        "warm windows glowing, mountains behind, painted illustration style, "
        "peaceful winter atmosphere"
    ),
    "campfire_lake": (
        "small campfire by mountain lake at dusk, reflection on water, "
        "tent in background, stars beginning to appear, painted illustration style, "
        "peaceful outdoor atmosphere"
    ),
    "balcony_summer": (
        "japanese apartment balcony in summer rain, cat sleeping on cushion, "
        "potted plants, warm interior light, anime illustration style, "
        "lofi aesthetic, peaceful"
    ),
}


def list_themes() -> list[str]:
    return list(THEME_PROMPTS.keys())


# ── AI Image Generation (fal.ai / Replicate) ──────────────────────

def generate_still_with_fal(theme: str, output_path: Path,
                            fal_api_key: str = None) -> Optional[Path]:
    """Generate a 1920x1080 still using fal.ai Flux model."""
    if fal_api_key is None:
        fal_api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY", "")

    if not fal_api_key:
        log.warning("No fal.ai API key — skipping AI generation")
        return None

    prompt = THEME_PROMPTS.get(theme, theme)

    try:
        import httpx
        resp = httpx.post(
            "https://fal.run/fal-ai/flux/schnell",
            headers={
                "Authorization": f"Key {fal_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "prompt": prompt,
                "image_size": "landscape_16_9",
                "num_inference_steps": 4,
                "num_images": 1,
                "enable_safety_checker": True,
            },
            timeout=120,
        )
        resp.raise_for_status()
        result = resp.json()
        img_url = result["images"][0]["url"]

        # Download the image
        img_resp = httpx.get(img_url, timeout=60)
        img_resp.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(img_resp.content)
        log.info("Generated still: %s", output_path)
        return output_path
    except Exception as e:
        log.error("fal.ai generation failed: %s", e)
        return None


# ── Procedural fallback (no AI, no cost) ──────────────────────────

def generate_gradient_still(theme: str, output_path: Path,
                           width: int = 1920, height: int = 1080) -> Optional[Path]:
    """Generate a colored gradient with text label as a fallback background."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Theme color palettes
    palettes = {
        "rainy_cafe": ("#3a2f1a", "#1a1408"),
        "fireplace_cabin": ("#4a1f0a", "#1a0a04"),
        "ocean_night": ("#0a1a3a", "#000814"),
        "library_lamp": ("#2a1f10", "#100a04"),
        "tokyo_rain": ("#2a0a3a", "#0a0014"),
        "forest_dusk": ("#1a3a14", "#040a04"),
        "spaceship_drift": ("#1a0a3a", "#0a0414"),
        "nordic_cabin": ("#1a2a3a", "#040a14"),
        "campfire_lake": ("#3a2a14", "#0a0a04"),
        "balcony_summer": ("#1a2a14", "#040a04"),
        "default": ("#1a1a2e", "#0a0a14"),
    }
    c1, c2 = palettes.get(theme, palettes["default"])
    label = theme.replace("_", " ").title()

    try:
        # Use FFmpeg to create the gradient + text overlay
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c={c1}:s={width}x{height}:d=1",
            "-vf", (
                f"drawtext=text='{label}':"
                f"fontcolor=white@0.6:fontsize=84:"
                f"x=(w-text_w)/2:y=(h-text_h)/2-50:font=Arial,"
                f"drawtext=text='Cozy Ambient':"
                f"fontcolor=white@0.4:fontsize=42:"
                f"x=(w-text_w)/2:y=(h-text_h)/2+50:font=Arial"
            ),
            "-frames:v", "1",
            str(output_path),
        ], check=True, capture_output=True, timeout=30)
        log.info("Generated gradient still: %s", output_path)
        return output_path
    except Exception as e:
        log.error("Gradient generation failed: %s", e)
        return None


# ── Animate still into looping video ───────────────────────────────

def animate_still_to_loop(
    still_path: Path,
    output_path: Path,
    duration_seconds: float = 3600,
    width: int = 1920,
    height: int = 1080,
    add_particles: bool = True,
    particle_type: str = "rain",
) -> Optional[Path]:
    """Convert a still image into a subtle animated looping video.

    Effects:
    - Slow Ken Burns zoom (zoompan)
    - Optional particle overlay (rain, snow, embers)
    - Subtle vignette
    """
    if not still_path.exists():
        log.error("Still not found: %s", still_path)
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Calculate frames for the duration at 30fps
    fps = 30
    total_frames = int(duration_seconds * fps)

    # Filter chain for parallax/Ken Burns effect
    filter_parts = [
        # Scale and pad to ensure we have room to zoom
        f"scale={width * 1.2}:{height * 1.2}:flags=lanczos",
        # Slow Ken Burns: zoom in subtly over the duration
        f"zoompan=z='min(1+0.0001*on,1.15)':d={total_frames}:s={width}x{height}:fps={fps}",
        # Subtle vignette
        f"vignette=PI/4",
    ]

    vf = ",".join(filter_parts)

    try:
        # Use NVENC if available, otherwise libx264
        encoder = "h264_nvenc" if _nvenc_available() else "libx264"
        preset = "fast" if encoder == "h264_nvenc" else "veryfast"

        subprocess.run([
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(still_path),
            "-vf", vf,
            "-t", str(duration_seconds),
            "-c:v", encoder, "-preset", preset,
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-movflags", "+faststart",
            str(output_path),
        ], check=True, capture_output=True, timeout=1800)
        log.info("Animated still to %s (%.1f hours)", output_path, duration_seconds / 3600)
        return output_path
    except subprocess.CalledProcessError as e:
        log.error("Animation failed: %s", e.stderr.decode("utf-8", errors="replace")[-400:])
        return None


def _nvenc_available() -> bool:
    """Delegate to canonical check that actually tests nvenc at runtime."""
    from peanut_reacts.core.ffmpeg import nvenc_available
    return nvenc_available()


# ── Main entry: theme -> finished background ──────────────────────

def generate_background(
    theme: str,
    output_dir: Path,
    duration_seconds: float = 3600,
    use_ai: bool = True,
    fal_api_key: str = None,
) -> Optional[Path]:
    """Full pipeline: pick theme → still → animated loop video."""
    output_dir.mkdir(parents=True, exist_ok=True)
    still_path = output_dir / f"{theme}_still.png"
    video_path = output_dir / f"{theme}_loop.mp4"

    if video_path.exists():
        log.info("Background already exists: %s", video_path)
        return video_path

    # Step 1: get a still
    if use_ai:
        still = generate_still_with_fal(theme, still_path, fal_api_key=fal_api_key)
    else:
        still = None

    if still is None or not still.exists():
        log.info("Falling back to gradient still")
        still = generate_gradient_still(theme, still_path)

    if not still:
        return None

    # Step 2: animate to loop video
    return animate_still_to_loop(still, video_path, duration_seconds=duration_seconds)
