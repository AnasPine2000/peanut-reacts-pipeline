"""
Oddly Satisfying Shorts pipeline orchestrator.

Fetches clips → edits → generates metadata → uploads to YouTube.
Designed for batch production: 3-5 shorts per run.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from peanut_reacts.shorts.video_source import (
    SATISFYING_QUERIES,
    UsedVideoTracker,
    fetch_satisfying_clips,
)
from peanut_reacts.shorts.editor import (
    add_background_music,
    add_text_overlay,
    compile_short,
    crop_to_portrait,
    extract_thumbnail,
)

logger = logging.getLogger(__name__)


@dataclass
class ShortsConfig:
    """Configuration for the shorts pipeline."""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    deepseek_api_key: str = ""
    youtube_client_secrets: str = ""

    output_dir: Path = Path("production/shorts")
    clips_per_short: int = 3
    target_duration: float = 45.0   # seconds (aim for 30-55s)
    upload: bool = True
    privacy: str = "public"

    # Music
    music_dir: Path = Path("assets/music")
    music_volume: float = 0.12


@dataclass
class ShortResult:
    """Result of generating one short."""
    video_path: Path
    thumbnail_path: Path
    title: str
    description: str
    tags: list[str]
    youtube_url: str = ""
    query: str = ""


class SatisfyingShortsPipeline:
    """Generate and upload oddly satisfying YouTube Shorts."""

    def __init__(self, config: ShortsConfig, log: Optional[logging.Logger] = None):
        self._cfg = config
        self._log = log or logger
        self._tracker = UsedVideoTracker(config.output_dir / "used_videos.json")

    def generate(self, count: int = 5, query: str = "") -> list[ShortResult]:
        """Generate `count` shorts and optionally upload them."""
        self._cfg.output_dir.mkdir(parents=True, exist_ok=True)
        results: list[ShortResult] = []

        for i in range(count):
            try:
                self._log.info("=== Generating Short %d/%d ===", i + 1, count)
                result = self._generate_one(i + 1, query)
                results.append(result)
                self._log.info("Short %d complete: %s", i + 1, result.video_path.name)
            except Exception as e:
                self._log.error("Short %d failed: %s", i + 1, e)
                continue

        self._log.info("Generated %d/%d shorts", len(results), count)
        return results

    def _generate_one(self, index: int, query: str = "") -> ShortResult:
        """Generate a single short."""
        cfg = self._cfg
        work_dir = cfg.output_dir / f"short_{index:03d}"
        work_dir.mkdir(parents=True, exist_ok=True)

        # Pick a random query if not specified
        q = query or random.choice(SATISFYING_QUERIES)
        self._log.info("Query: '%s'", q)

        # Step 1: Fetch clips
        clips = fetch_satisfying_clips(
            pexels_key=cfg.pexels_api_key,
            pixabay_key=cfg.pixabay_api_key,
            count=cfg.clips_per_short,
            query=q,
            output_dir=work_dir / "clips",
            tracker=self._tracker,
        )

        if not clips:
            raise RuntimeError(f"No clips found for query '{q}'")

        self._log.info("Fetched %d clips", len(clips))

        # Step 2: Crop each clip to portrait
        portrait_clips = []
        for j, clip in enumerate(clips):
            portrait = work_dir / f"portrait_{j:02d}.mp4"
            crop_to_portrait(clip.local_path, portrait)
            portrait_clips.append(portrait)

        # Step 3: Compile into one video
        compiled = work_dir / "compiled.mp4"
        compile_short(portrait_clips, compiled, target_duration=cfg.target_duration)

        # Step 4: Add text overlays
        with_text = work_dir / "with_text.mp4"
        add_text_overlay(compiled, with_text)

        # Step 5: Add background music (if available)
        final = work_dir / "final.mp4"
        music_files = list(cfg.music_dir.glob("*.mp3")) + list(cfg.music_dir.glob("*.wav"))
        if music_files:
            music = random.choice(music_files)
            add_background_music(with_text, music, final, music_volume=cfg.music_volume)
        else:
            # No music — just copy
            import shutil
            shutil.copy2(str(with_text), str(final))
            self._log.info("No background music available — skipping")

        # Step 6: Extract thumbnail
        thumbnail = work_dir / "thumbnail.jpg"
        extract_thumbnail(final, thumbnail)

        # Step 7: Generate metadata via LLM
        title, description, tags = self._generate_metadata(q, clips)

        # Step 8: Upload to YouTube
        youtube_url = ""
        if cfg.upload:
            youtube_url = self._upload(final, thumbnail, title, description, tags)

        return ShortResult(
            video_path=final,
            thumbnail_path=thumbnail,
            title=title,
            description=description,
            tags=tags,
            youtube_url=youtube_url,
            query=q,
        )

    def _generate_metadata(
        self, query: str, clips: list,
    ) -> tuple[str, str, list[str]]:
        """Generate title, description, and tags via LLM."""
        if not self._cfg.deepseek_api_key:
            # Fallback: template-based metadata
            title = f"This {query} is SO satisfying to watch! 😍 #shorts"
            description = (
                f"Oddly satisfying {query} compilation! "
                f"Watch till the end for the most satisfying moment.\n\n"
                f"#oddlysatisfying #satisfying #{query.replace(' ', '')} "
                f"#asmr #shorts #viral"
            )
            tags = [
                "oddly satisfying", "satisfying", "asmr", query,
                "satisfying video", "shorts", "viral", "relaxing",
                "satisfying compilation", "cleaning", "oddly satisfying video",
            ]
            return title, description, tags

        try:
            from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider

            llm = create_llm_provider(LLMConfig(
                provider="deepseek",
                api_key=self._cfg.deepseek_api_key,
            ))

            prompt = (
                f"Generate YouTube Shorts metadata for an oddly satisfying video about '{query}'.\n\n"
                f"Return JSON with:\n"
                f'- "title": catchy clickbait title under 70 chars with emoji (like "This is SO satisfying! 😍")\n'
                f'- "description": 2-3 sentences + hashtags, under 200 chars\n'
                f'- "tags": array of 15-20 SEO tags\n\n'
                f"The video is a {len(clips)}-clip compilation of {query} footage. "
                f"Make it viral and engaging. Include #shorts in description."
            )

            messages = [
                {"role": "system", "content": "You are a YouTube SEO expert. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ]

            response = llm.generate(messages, temperature=0.8)
            text = response.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            data = json.loads(text)
            return (
                data.get("title", f"Satisfying {query}! 😍 #shorts"),
                data.get("description", f"Oddly satisfying {query}. #shorts #satisfying"),
                data.get("tags", ["satisfying", "shorts", query]),
            )
        except Exception as e:
            self._log.warning("LLM metadata failed (%s), using template", e)
            title = f"This {query} is SO satisfying to watch! 😍 #shorts"
            description = (
                f"Oddly satisfying {query} compilation! "
                f"Watch till the end for the most satisfying moment.\n\n"
                f"#oddlysatisfying #satisfying #{query.replace(' ', '')} "
                f"#asmr #shorts #viral"
            )
            tags = [
                "oddly satisfying", "satisfying", "asmr", query,
                "satisfying video", "shorts", "viral", "relaxing",
            ]
            return title, description, tags

    def _upload(
        self,
        video_path: Path,
        thumbnail_path: Path,
        title: str,
        description: str,
        tags: list[str],
    ) -> str:
        """Upload to YouTube and return the video URL."""
        try:
            from peanut_reacts.upload.youtube_auth import get_authenticated_service
            from peanut_reacts.upload.youtube_upload import UploadMetadata, YouTubeUploader

            secrets = Path(self._cfg.youtube_client_secrets)
            service = get_authenticated_service(secrets)
            uploader = YouTubeUploader(service)

            metadata = UploadMetadata(
                title=title[:100],
                description=description[:5000],
                tags=tags[:30],
                category_id="24",  # Entertainment
                privacy_status=self._cfg.privacy,
                thumbnail_path=thumbnail_path,
            )

            result = uploader.upload_video(video_path, metadata)
            self._log.info("Uploaded: %s", result.url)
            return result.url
        except Exception as e:
            self._log.error("Upload failed: %s", e)
            return ""
