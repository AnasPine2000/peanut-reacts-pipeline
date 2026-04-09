"""
Scary/Creepy Compilation pipeline orchestrator.

Generates script → fetches footage → narrates → compiles → extracts shorts → uploads.
Produces 1 long-form (8-15 min) + 3-5 shorts per run.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from peanut_reacts.character.reaction_generator import LLMConfig
from peanut_reacts.compilations.script_generator import (
    CREEPY_TOPICS,
    CompilationScript,
    generate_compilation_script,
)
from peanut_reacts.compilations.video_compiler import (
    CompiledSegment,
    compile_long_form,
    extract_shorts,
    render_intro,
    render_outro,
    render_segment,
)
from peanut_reacts.shorts.video_source import (
    UsedVideoTracker,
    fetch_satisfying_clips,
)

logger = logging.getLogger(__name__)


@dataclass
class CompilationConfig:
    """Configuration for the compilation pipeline."""
    # API keys
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    deepseek_api_key: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "TX3LPaxmHKxFdv7VOQHJ"  # Liam
    youtube_client_secrets: str = ""

    # Output
    output_dir: Path = Path("production/compilations")
    segment_count: int = 10       # "Top N" count
    max_shorts: int = 5           # shorts to extract
    upload: bool = True
    privacy: str = "public"

    # Music
    music_dir: Path = Path("assets/music")
    music_volume: float = 0.08


@dataclass
class CompilationResult:
    """Result of a compilation run."""
    long_form_path: Path
    shorts_paths: list[Path]
    title: str
    description: str
    tags: list[str]
    long_form_url: str = ""
    shorts_urls: list[str] = field(default_factory=list)


class ScaryCompilationPipeline:
    """Full pipeline for scary/creepy compilation videos."""

    def __init__(self, config: CompilationConfig, log: Optional[logging.Logger] = None):
        self._cfg = config
        self._log = log or logger
        self._tracker = UsedVideoTracker(config.output_dir / "used_videos.json")

    def generate(self, topic: str = "") -> CompilationResult:
        """Generate one compilation (long-form + shorts)."""
        cfg = self._cfg
        work_dir = cfg.output_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Generate script
        self._log.info("=== Step 1: Generating script ===")
        llm_config = LLMConfig(provider="deepseek", api_key=cfg.deepseek_api_key)
        script = generate_compilation_script(
            topic=topic or random.choice(CREEPY_TOPICS),
            count=cfg.segment_count,
            llm_config=llm_config,
        )
        self._log.info("Script: '%s' (%d segments)", script.video_title, len(script.segments))

        # Step 2: Fetch footage for each segment
        self._log.info("=== Step 2: Fetching footage ===")
        segment_clips = {}
        for seg in script.segments:
            self._log.info("  Fetching footage for #%d: '%s'", seg.number, seg.search_query)
            clips = fetch_satisfying_clips(
                pexels_key=cfg.pexels_api_key,
                pixabay_key=cfg.pixabay_api_key,
                count=1,
                query=seg.search_query,
                output_dir=work_dir / "clips" / f"seg_{seg.number:02d}",
                tracker=self._tracker,
            )
            if clips:
                segment_clips[seg.number] = clips[0]
            else:
                self._log.warning("  No footage found for #%d, using fallback", seg.number)
                # Try a more generic query
                fallback_clips = fetch_satisfying_clips(
                    pexels_key=cfg.pexels_api_key,
                    pixabay_key=cfg.pixabay_api_key,
                    count=1,
                    query="dark scary night",
                    output_dir=work_dir / "clips" / f"seg_{seg.number:02d}",
                    tracker=self._tracker,
                )
                if fallback_clips:
                    segment_clips[seg.number] = fallback_clips[0]

        # Step 3: Generate narration audio
        self._log.info("=== Step 3: Generating narration ===")
        narration_dir = work_dir / "narration"
        narration_dir.mkdir(exist_ok=True)

        narration_paths = {}
        if cfg.elevenlabs_api_key:
            from peanut_reacts.character.elevenlabs_tts import ElevenLabsConfig, ElevenLabsTTSEngine

            el_config = ElevenLabsConfig(
                api_key=cfg.elevenlabs_api_key,
                voice_id=cfg.elevenlabs_voice_id,
            )
            tts = ElevenLabsTTSEngine(el_config, self._log)

            # Intro
            intro_result = tts.synthesize(
                script.intro_narration,
                narration_dir / "intro.mp3",
                emotion="shocked",
            )
            narration_paths["intro"] = intro_result.audio_path
            self._log.info("  Intro: %.1fs", intro_result.duration)

            # Segments
            for seg in script.segments:
                emotion = "scared" if seg.intensity == "high" else "neutral"
                result = tts.synthesize(
                    seg.narration,
                    narration_dir / f"seg_{seg.number:02d}.mp3",
                    emotion=emotion,
                )
                narration_paths[seg.number] = result.audio_path
                self._log.info("  #%d: %.1fs (%s)", seg.number, result.duration, seg.intensity)

            # Outro
            outro_result = tts.synthesize(
                script.outro_narration,
                narration_dir / "outro.mp3",
                emotion="excited",
            )
            narration_paths["outro"] = outro_result.audio_path
        else:
            self._log.warning("No ElevenLabs API key — skipping narration")
            # Generate silent audio placeholders
            import subprocess
            for key in ["intro", "outro"] + [s.number for s in script.segments]:
                path = narration_dir / f"{'intro' if key == 'intro' else 'outro' if key == 'outro' else f'seg_{key:02d}'}.mp3"
                subprocess.run([
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", "5", "-c:a", "aac", str(path),
                ], check=True, capture_output=True)
                narration_paths[key] = path

        # Step 4: Render each segment
        self._log.info("=== Step 4: Rendering segments ===")
        rendered_dir = work_dir / "rendered"
        rendered_dir.mkdir(exist_ok=True)

        segment_videos: list[Path] = []
        compiled_segments: list[CompiledSegment] = []

        # Render intro
        intro_path = rendered_dir / "00_intro.mp4"
        render_intro(narration_paths["intro"], intro_path, title=script.video_title)
        segment_videos.append(intro_path)

        # Render each numbered segment
        for seg in script.segments:
            if seg.number not in segment_clips:
                self._log.warning("  Skipping #%d — no footage", seg.number)
                continue
            if seg.number not in narration_paths:
                continue

            seg_path = rendered_dir / f"seg_{seg.number:02d}.mp4"
            compiled = render_segment(
                seg,
                segment_clips[seg.number].local_path,
                narration_paths[seg.number],
                seg_path,
            )
            segment_videos.append(seg_path)
            compiled_segments.append(compiled)

        # Render outro
        outro_path = rendered_dir / "99_outro.mp4"
        render_outro(narration_paths["outro"], outro_path)
        segment_videos.append(outro_path)

        # Step 5: Compile long-form
        self._log.info("=== Step 5: Compiling long-form ===")
        long_form = cfg.output_dir / f"{script.video_title[:60].replace(' ', '_')}.mp4"

        music_files = list(cfg.music_dir.glob("*.mp3")) + list(cfg.music_dir.glob("*.wav"))
        music = random.choice(music_files) if music_files else None

        compile_long_form(segment_videos, long_form, music_path=music, music_volume=cfg.music_volume)

        # Step 6: Extract shorts
        self._log.info("=== Step 6: Extracting shorts ===")
        shorts_dir = cfg.output_dir / "shorts"
        shorts = extract_shorts(long_form, compiled_segments, shorts_dir, max_shorts=cfg.max_shorts)
        self._log.info("Extracted %d shorts", len(shorts))

        # Step 7: Upload
        long_form_url = ""
        shorts_urls: list[str] = []

        if cfg.upload and cfg.youtube_client_secrets:
            self._log.info("=== Step 7: Uploading ===")
            long_form_url, shorts_urls = self._upload_all(
                long_form, shorts, script,
            )

        result = CompilationResult(
            long_form_path=long_form,
            shorts_paths=shorts,
            title=script.video_title,
            description=script.description,
            tags=script.tags,
            long_form_url=long_form_url,
            shorts_urls=shorts_urls,
        )

        self._log.info("=== DONE ===")
        self._log.info("Long-form: %s %s", long_form.name, long_form_url or "(local)")
        self._log.info("Shorts: %d uploaded", len(shorts_urls))
        return result

    def _upload_all(
        self,
        long_form: Path,
        shorts: list[Path],
        script: CompilationScript,
    ) -> tuple[str, list[str]]:
        """Upload long-form + all shorts to YouTube."""
        from peanut_reacts.upload.youtube_auth import get_authenticated_service
        from peanut_reacts.upload.youtube_upload import UploadMetadata, YouTubeUploader
        from peanut_reacts.shorts.editor import extract_thumbnail

        secrets = Path(self._cfg.youtube_client_secrets)
        service = get_authenticated_service(secrets)
        uploader = YouTubeUploader(service)

        # Upload long-form
        thumb = long_form.with_suffix(".jpg")
        extract_thumbnail(long_form, thumb)

        lf_meta = UploadMetadata(
            title=script.video_title[:100],
            description=script.description[:5000],
            tags=script.tags[:30],
            category_id="24",
            privacy_status=self._cfg.privacy,
            thumbnail_path=thumb,
        )

        self._log.info("Uploading long-form: %s", script.video_title)
        try:
            lf_result = uploader.upload_video(long_form, lf_meta)
            long_form_url = lf_result.url
            self._log.info("Long-form uploaded: %s", long_form_url)
        except Exception as e:
            self._log.error("Long-form upload failed: %s", e)
            long_form_url = ""

        # Upload shorts
        shorts_urls = []
        for i, short in enumerate(shorts):
            short_meta = UploadMetadata(
                title=f"{script.video_title[:50]} Part {i + 1} #shorts",
                description=f"{script.description}\n\nFull video: {long_form_url}\n\n#shorts #scary #creepy",
                tags=script.tags[:15] + ["shorts"],
                category_id="24",
                privacy_status=self._cfg.privacy,
            )
            try:
                result = uploader.upload_video(short, short_meta)
                shorts_urls.append(result.url)
                self._log.info("Short %d uploaded: %s", i + 1, result.url)
            except Exception as e:
                self._log.error("Short %d upload failed: %s", i + 1, e)

        return long_form_url, shorts_urls
