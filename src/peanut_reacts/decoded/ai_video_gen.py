"""
AI video generation via cloud GPU (RunPod + SkyReels-V2).

Generates original AI footage from text prompts for copyright-free
reaction videos. Uses RunPod's serverless API or pod-based GPU.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class AIVideoConfig:
    """Configuration for AI video generation."""
    runpod_api_key: str = ""
    model: str = "skyreels-v2-1.3b"  # or "skyreels-v2-14b"
    resolution: str = "540p"          # "540p" or "720p"
    fps: int = 24
    default_duration: int = 8         # seconds per clip


@dataclass
class GeneratedClip:
    """Result of AI video generation."""
    prompt: str
    video_path: Path
    duration: float
    resolution: str


class RunPodClient:
    """Client for RunPod serverless/pod API."""

    BASE_URL = "https://api.runpod.ai/v2"

    def __init__(self, api_key: str):
        self._key = api_key
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"

    def run_serverless(
        self,
        endpoint_id: str,
        prompt: str,
        duration: int = 8,
        resolution: str = "540p",
    ) -> dict:
        """Submit a job to a RunPod serverless endpoint."""
        payload = {
            "input": {
                "prompt": prompt,
                "num_frames": duration * 24,
                "height": 540 if resolution == "540p" else 720,
                "width": 960 if resolution == "540p" else 1280,
                "guidance_scale": 7.5,
                "num_inference_steps": 30,
            }
        }

        resp = self._session.post(
            f"{self.BASE_URL}/{endpoint_id}/run",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def check_status(self, endpoint_id: str, job_id: str) -> dict:
        """Check job status."""
        resp = self._session.get(
            f"{self.BASE_URL}/{endpoint_id}/status/{job_id}",
        )
        resp.raise_for_status()
        return resp.json()

    def wait_for_result(
        self, endpoint_id: str, job_id: str, timeout: int = 600,
    ) -> dict:
        """Poll until job completes."""
        start = time.time()
        while time.time() - start < timeout:
            status = self.check_status(endpoint_id, job_id)
            if status.get("status") == "COMPLETED":
                return status
            elif status.get("status") in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"Job failed: {status}")
            time.sleep(5)
        raise TimeoutError(f"Job {job_id} timed out after {timeout}s")


def generate_scene_videos(
    scenes: list[dict],
    output_dir: Path,
    config: AIVideoConfig,
) -> list[GeneratedClip]:
    """Generate AI video clips for each scene description.

    Args:
        scenes: List of {"id": int, "prompt": str, "duration": int}
        output_dir: Where to save generated videos
        config: AI video generation config

    Returns:
        List of GeneratedClip objects
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if not config.runpod_api_key:
        logger.warning("No RunPod API key — generating placeholder videos")
        return _generate_placeholders(scenes, output_dir, config)

    client = RunPodClient(config.runpod_api_key)
    clips: list[GeneratedClip] = []

    # For now, use placeholder approach until RunPod endpoint is configured
    # TODO: Replace with actual SkyReels-V2 endpoint when available
    logger.info("Generating %d AI video scenes...", len(scenes))
    return _generate_placeholders(scenes, output_dir, config)


def _generate_placeholders(
    scenes: list[dict],
    output_dir: Path,
    config: AIVideoConfig,
) -> list[GeneratedClip]:
    """Generate placeholder videos with animated text (for testing pipeline).

    These will be replaced with actual AI-generated footage when
    RunPod + SkyReels is configured.
    """
    import subprocess

    clips = []
    for scene in scenes:
        sid = scene.get("id", 0)
        prompt = scene.get("prompt", "")
        duration = scene.get("duration", config.default_duration)
        output = output_dir / f"scene_{sid:03d}.mp4"

        # Create a visually interesting placeholder
        safe_prompt = prompt[:60].replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")

        # Animated gradient background with prompt text
        vf = (
            f"drawtext=text='{safe_prompt}':"
            f"fontsize=28:fontcolor=white:borderw=2:bordercolor=black:"
            f"x=(w-tw)/2:y=(h-th)/2:"
            f"box=1:boxcolor=black@0.5:boxborderw=12,"
            f"drawtext=text='AI Scene {sid + 1}':"
            f"fontsize=48:fontcolor=yellow:borderw=3:bordercolor=black:"
            f"x=(w-tw)/2:y=80"
        )

        # Animated color shifting background
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"color=c=0x0a0a1e:s=1920x1080:d={duration}:r={config.fps}",
            "-vf", vf,
            "-c:v", "libx264", "-crf", "22", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            str(output),
        ]

        subprocess.run(cmd, check=True, capture_output=True)

        clips.append(GeneratedClip(
            prompt=prompt,
            video_path=output,
            duration=duration,
            resolution=config.resolution,
        ))
        logger.info("  Scene %d: placeholder generated", sid)

    return clips
