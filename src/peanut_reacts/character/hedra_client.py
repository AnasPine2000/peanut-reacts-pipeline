"""Hedra Character-3 API client — image + audio -> talking-head MP4.

Hedra's Character-3 / Omnia model takes a single character reference
image + a voice audio track and produces a video where the character
lip-syncs to the audio with natural head bob, eye blinks, and shoulder
shifts. Critically for our Peanut use case: it handles cartoon /
stylized characters where face-landmark-based models (SadTalker,
EchoMimic, Hallo*) all fail.

Why this module exists:
  * Lemon Slice's public API is live-chat-only (verified April 2026 —
    only /liveai/* endpoints exist), so we can't integrate it for
    batch YouTube production.
  * Wan 2.2 + Rhubarb is the local fallback but needs the user to
    draw 9 mouth sprites first.
  * Hedra has a documented batch API (`/web-app/public/{models,assets,
    generations}`) that fits a pipeline cleanly.

This client owns:
  - asset upload (image + audio)
  - generation submission
  - polling
  - download to a local path

Behind a feature flag (HEDRA_API_KEY env) — pipelines call
render_hedra_clip(image, audio, output) and get None back when the key
isn't set, so the fallback path (Kling loops / Rhubarb) takes over
automatically.

Design contract (matches youtube_upload / tiktok_upload):
  * Failure returns None or raises HedraError; pipelines wrap calls
    in try/except so a bad render never kills a run.
  * Polling has a hard timeout (default 10 min); after that we bail
    rather than waiting forever on a stuck job.
  * NEVER logs the raw API key. Masked-only in error context.

Reference: ShmuelRonen/ComfyUI_Hedra (April 2026, MIT)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)


HEDRA_BASE_URL = "https://api.hedra.com/web-app/public"
DEFAULT_RESOLUTION = "720p"      # 720p / 540p — 720p is creator-tier sweet spot
DEFAULT_ASPECT_RATIO = "16:9"    # for landscape long-form (reddit_stories)
DEFAULT_POLL_TIMEOUT_S = 600     # 10 min cap on a single render
DEFAULT_POLL_INTERVAL_S = 5


class HedraError(Exception):
    """Any Hedra API failure that the caller should distinguish from a
    network blip. Carries the masked key + endpoint for debugging
    without leaking the full credential into logs."""


def _mask_key(key: str) -> str:
    if not key or len(key) < 12:
        return "(missing)"
    return f"{key[:4]}...{key[-4:]}"


@dataclass
class HedraGenerationResult:
    """Return value from render_hedra_clip on success."""
    output_path: Path
    generation_id: str
    duration_s: float       # render wall-clock, not video duration
    model_id: str
    download_url: str


class HedraClient:
    """Thin client around Hedra's web-app public API.

    Usage:
        client = HedraClient(api_key=os.environ["HEDRA_API_KEY"])
        result = client.render_clip(
            image_path=Path("assets/peanut_face/realistic_neutral.png"),
            audio_path=Path("audio.wav"),
            output_path=Path("out.mp4"),
            text_prompt="Cartoon peanut narrating reddit stories, calm posh British, slight head bob",
        )
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = HEDRA_BASE_URL,
        timeout_s: float = 60,
    ):
        self.api_key = api_key or os.environ.get("HEDRA_API_KEY", "").strip()
        if not self.api_key:
            raise HedraError(
                "HEDRA_API_KEY not set. Get one at https://www.hedra.com/api-profile "
                "and add to .env or GitHub Actions secrets."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        # Persistent session reuses the underlying TCP connection across
        # the 4-6 calls we make per render (models -> asset -> upload x2
        # -> generation -> poll), saving ~200-400 ms on warm requests.
        self._client = httpx.Client(
            timeout=timeout_s,
            headers={"x-api-key": self.api_key},
        )

    # ── Lifecycle ──────────────────────────────────────────────────

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Internal helpers ──────────────────────────────────────────

    def _json_post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        resp = self._client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise HedraError(
                f"POST {path} -> {resp.status_code}: "
                f"{resp.text[:300]} (key={_mask_key(self.api_key)})"
            )
        return resp.json()

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        resp = self._client.get(url)
        if resp.status_code != 200:
            raise HedraError(
                f"GET {path} -> {resp.status_code}: "
                f"{resp.text[:300]} (key={_mask_key(self.api_key)})"
            )
        return resp.json()

    def _multipart_upload(self, path: str, file_path: Path, mime: str) -> dict:
        """Upload bytes to /assets/{id}/upload — multipart, no JSON body."""
        url = f"{self.base_url}{path}"
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, mime)}
            # NOTE: we deliberately skip Content-Type header here —
            # httpx auto-sets multipart boundary. Sending application/json
            # would break the upload.
            resp = self._client.post(url, files=files, headers={})
        if resp.status_code != 200:
            raise HedraError(
                f"upload {path} -> {resp.status_code}: "
                f"{resp.text[:300]} (file={file_path.name}, "
                f"key={_mask_key(self.api_key)})"
            )
        return resp.json() if resp.text else {}

    # ── Public API ────────────────────────────────────────────────

    def list_models(self) -> list[dict]:
        """Returns the available models. We pick [0] by default —
        Hedra's primary model rotates as they ship new versions, and
        [0] is consistently the latest stable per the ComfyUI plugin."""
        return self._get("/models")

    def create_image_asset(self, image_path: Path) -> str:
        """Create + upload an image asset. Returns the asset id."""
        if not image_path.exists():
            raise HedraError(f"image not found: {image_path}")
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"

        meta = self._json_post(
            "/assets",
            {"name": image_path.name, "type": "image"},
        )
        asset_id = meta.get("id")
        if not asset_id:
            raise HedraError(f"asset creation returned no id: {meta}")

        self._multipart_upload(f"/assets/{asset_id}/upload", image_path, mime)
        return asset_id

    def create_audio_asset(self, audio_path: Path) -> str:
        """Create + upload an audio asset. Returns the asset id.

        Hedra accepts WAV and MP3 from observation. For best results
        feed it 16-bit PCM WAV at 44.1 kHz — convert ahead of time
        with ffmpeg if the source is something else.
        """
        if not audio_path.exists():
            raise HedraError(f"audio not found: {audio_path}")
        mime = "audio/wav" if audio_path.suffix.lower() == ".wav" else "audio/mpeg"

        meta = self._json_post(
            "/assets",
            {"name": audio_path.name, "type": "audio"},
        )
        asset_id = meta.get("id")
        if not asset_id:
            raise HedraError(f"asset creation returned no id: {meta}")

        self._multipart_upload(f"/assets/{asset_id}/upload", audio_path, mime)
        return asset_id

    def submit_generation(
        self,
        image_id: str,
        audio_id: str,
        model_id: str,
        text_prompt: str = "",
        resolution: str = DEFAULT_RESOLUTION,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    ) -> str:
        """Kick off a generation job. Returns the generation id (poll with
        wait_for_completion)."""
        payload = {
            "type": "video",
            "ai_model_id": model_id,
            "start_keyframe_id": image_id,
            "audio_id": audio_id,
            "generated_video_inputs": {
                "text_prompt": text_prompt or "Talking character",
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
            },
        }
        result = self._json_post("/generations", payload)
        gen_id = result.get("id")
        if not gen_id:
            raise HedraError(f"generation submit returned no id: {result}")
        return gen_id

    def wait_for_completion(
        self,
        generation_id: str,
        timeout_s: float = DEFAULT_POLL_TIMEOUT_S,
        interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> str:
        """Block until the generation completes. Returns the download URL.

        Raises HedraError on:
          - server-reported error status
          - timeout (interval-based, no extension)
          - any 5xx during polling
        """
        deadline = time.time() + timeout_s
        last_status = None
        while time.time() < deadline:
            data = self._get(f"/generations/{generation_id}/status")
            status = data.get("status", "Unknown")

            if status != last_status:
                log.info("[HEDRA] generation %s -> %s", generation_id[:8], status)
                last_status = status

            if status == "complete":
                url = data.get("url")
                if not url:
                    raise HedraError(
                        f"complete but no url for {generation_id}: {data}"
                    )
                return url

            if status == "error":
                raise HedraError(
                    f"generation {generation_id} failed: "
                    f"{data.get('error_message', 'unknown')}"
                )

            time.sleep(interval_s)

        raise HedraError(
            f"generation {generation_id} timed out after {timeout_s}s "
            f"(last status={last_status!r})"
        )

    def download_video(self, url: str, output_path: Path) -> Path:
        """Stream the finished MP4 to disk."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise HedraError(
                    f"video download failed: HTTP {resp.status_code} url={url[:60]}..."
                )
            with open(output_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
        return output_path

    def render_clip(
        self,
        image_path: Path,
        audio_path: Path,
        output_path: Path,
        text_prompt: str = "",
        resolution: str = DEFAULT_RESOLUTION,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        timeout_s: float = DEFAULT_POLL_TIMEOUT_S,
    ) -> HedraGenerationResult:
        """One-call wrapper: upload assets + submit + poll + download.

        Returns HedraGenerationResult on success. Raises HedraError on
        any failure — caller is expected to wrap in try/except so a
        flaky Hedra render never kills the surrounding pipeline.
        """
        t0 = time.time()
        log.info(
            "[HEDRA] render: image=%s audio=%s -> %s (key=%s)",
            image_path.name, audio_path.name, output_path.name,
            _mask_key(self.api_key),
        )

        models = self.list_models()
        if not models:
            raise HedraError("no models available")
        model_id = models[0]["id"]
        log.info("[HEDRA] using model: %s", model_id)

        image_id = self.create_image_asset(image_path)
        audio_id = self.create_audio_asset(audio_path)

        gen_id = self.submit_generation(
            image_id=image_id,
            audio_id=audio_id,
            model_id=model_id,
            text_prompt=text_prompt,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
        )
        log.info("[HEDRA] submitted: generation_id=%s", gen_id)

        download_url = self.wait_for_completion(gen_id, timeout_s=timeout_s)
        self.download_video(download_url, output_path)

        elapsed = time.time() - t0
        log.info(
            "[HEDRA] render complete: %s (%d KB) in %.1fs",
            output_path.name,
            output_path.stat().st_size // 1024,
            elapsed,
        )
        return HedraGenerationResult(
            output_path=output_path,
            generation_id=gen_id,
            duration_s=elapsed,
            model_id=model_id,
            download_url=download_url,
        )


# ── Convenience function for pipelines ────────────────────────────

def render_hedra_clip(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    text_prompt: str = "",
    resolution: str = DEFAULT_RESOLUTION,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> Optional[HedraGenerationResult]:
    """Pipeline-friendly wrapper.

    Returns None if HEDRA_API_KEY is not set OR if any error occurs —
    the caller should treat None as "fall back to Kling loops or
    Rhubarb sprites". Never raises.
    """
    if not os.environ.get("HEDRA_API_KEY", "").strip():
        log.info("[HEDRA] HEDRA_API_KEY not set — skipping cloud render")
        return None
    try:
        with HedraClient() as client:
            return client.render_clip(
                image_path=image_path,
                audio_path=audio_path,
                output_path=output_path,
                text_prompt=text_prompt,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
            )
    except HedraError as e:
        log.warning("[HEDRA] render failed (falling back): %s", e)
        return None
    except Exception as e:
        log.warning("[HEDRA] render crashed (falling back): %s", e)
        return None
