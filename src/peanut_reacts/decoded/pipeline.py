"""
Sidemen AmongUs Decoded pipeline orchestrator.

Generates data-driven analysis video essays:
Script → Narrate → Compile with clips + data cards → Upload.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from peanut_reacts.character.reaction_generator import LLMConfig
from peanut_reacts.decoded.data_analyzer import (
    generate_player_report,
    find_key_moments,
    parse_all_transcripts,
    SIDEMEN,
)
from peanut_reacts.decoded.script_writer import (
    AnalysisScript,
    ScriptSection,
    write_player_analysis,
    write_episode_breakdown,
)
from peanut_reacts.decoded.clip_extractor import (
    add_analysis_overlay,
    create_data_card,
    create_narration_segment,
    extract_clip,
    find_source_video,
)

logger = logging.getLogger(__name__)

# Directories where source videos live
VIDEO_DIRS = [
    Path("downloads/AMONGUS"),
    Path("downloads/KSIAMONGUS"),
    Path("downloads"),
    Path("downloads/FOLABI"),
]


@dataclass
class DecodedConfig:
    """Configuration for the Decoded pipeline."""
    deepseek_api_key: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "TX3LPaxmHKxFdv7VOQHJ"  # Liam
    youtube_client_secrets: str = ""

    transcript_dirs: list[Path] = field(default_factory=lambda: [
        Path("downloads/AMONGUS"),
        Path("downloads/KSIAMONGUS"),
    ])
    video_dirs: list[Path] = field(default_factory=lambda: VIDEO_DIRS)
    output_dir: Path = Path("production/decoded")
    upload: bool = True
    privacy: str = "public"

    music_dir: Path = Path("assets/music")
    music_volume: float = 0.06


@dataclass
class DecodedResult:
    """Result of generating an analysis video."""
    video_path: Path
    shorts_paths: list[Path]
    title: str
    youtube_url: str = ""
    shorts_urls: list[str] = field(default_factory=list)


class DecodedPipeline:
    """Full pipeline for Sidemen AmongUs Decoded channel."""

    def __init__(self, config: DecodedConfig, log: Optional[logging.Logger] = None):
        self._cfg = config
        self._log = log or logger
        self._transcripts: list[dict] = []

    def _load_transcripts(self) -> list[dict]:
        """Load and cache all transcripts."""
        if not self._transcripts:
            self._log.info("Loading transcripts...")
            for d in self._cfg.transcript_dirs:
                self._transcripts.extend(parse_all_transcripts(d))
            self._log.info("Loaded %d transcripts", len(self._transcripts))
        return self._transcripts

    def generate_player_analysis(self, player: str) -> DecodedResult:
        """Generate a player analysis video essay."""
        cfg = self._cfg
        work_dir = cfg.output_dir / f"player_{player.lower()}"
        work_dir.mkdir(parents=True, exist_ok=True)

        transcripts = self._load_transcripts()

        # Step 1: Analyze player data
        self._log.info("=== Step 1: Analyzing %s ===", player)
        report = generate_player_report(player, transcripts)

        if "error" in report:
            raise RuntimeError(f"Player analysis failed: {report['error']}")

        # Save report
        report_path = work_dir / "player_report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        # Step 2: Generate script
        self._log.info("=== Step 2: Generating script ===")
        llm_config = LLMConfig(provider="deepseek", api_key=cfg.deepseek_api_key)
        script = write_player_analysis(player, report, llm_config)

        # Save script
        script_path = work_dir / "script.json"
        script_data = {
            "title": script.title,
            "description": script.description,
            "tags": script.tags,
            "sections": [
                {
                    "type": s.section_type,
                    "narration": s.narration,
                    "visual": s.visual_note,
                    "duration": s.duration_estimate,
                    "clip_query": s.clip_query,
                    "clip_timestamp": s.clip_timestamp,
                    "emotion": s.emotion,
                }
                for s in script.sections
            ],
        }
        script_path.write_text(json.dumps(script_data, indent=2), encoding="utf-8")
        self._log.info("Script: '%s' (%d sections, ~%.0fs)",
                       script.title, len(script.sections), script.total_duration)

        # Step 3: Generate narration
        self._log.info("=== Step 3: Generating narration ===")
        narration_dir = work_dir / "narration"
        narration_paths = self._generate_narration(script, narration_dir)

        # Step 4: Build video segments
        self._log.info("=== Step 4: Building video segments ===")
        segments = self._build_segments(script, narration_paths, work_dir, report)

        # Step 5: Compile long-form
        self._log.info("=== Step 5: Compiling final video ===")
        final_path = cfg.output_dir / f"{script.title[:60].replace(' ', '_')}.mp4"
        self._compile_video(segments, final_path)

        # Step 6: Extract shorts
        self._log.info("=== Step 6: Extracting shorts ===")
        shorts = self._extract_shorts(segments, script, work_dir / "shorts")

        # Step 7: Upload
        youtube_url = ""
        shorts_urls: list[str] = []
        if cfg.upload and cfg.youtube_client_secrets:
            self._log.info("=== Step 7: Uploading ===")
            youtube_url, shorts_urls = self._upload(final_path, shorts, script)

        result = DecodedResult(
            video_path=final_path,
            shorts_paths=shorts,
            title=script.title,
            youtube_url=youtube_url,
            shorts_urls=shorts_urls,
        )

        self._log.info("=== DONE ===")
        self._log.info("Video: %s %s", final_path.name, youtube_url or "(local)")
        self._log.info("Shorts: %d", len(shorts))
        return result

    def _generate_narration(
        self, script: AnalysisScript, output_dir: Path,
    ) -> dict[int, Path]:
        """Generate TTS narration for each section."""
        output_dir.mkdir(parents=True, exist_ok=True)
        narration_paths: dict[int, Path] = {}

        if not self._cfg.elevenlabs_api_key:
            self._log.warning("No ElevenLabs API key — generating silent narration")
            import subprocess
            for i, section in enumerate(script.sections):
                path = output_dir / f"section_{i:02d}.mp3"
                subprocess.run([
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", str(section.duration_estimate), "-c:a", "aac", str(path),
                ], check=True, capture_output=True)
                narration_paths[i] = path
            return narration_paths

        from peanut_reacts.character.elevenlabs_tts import ElevenLabsConfig, ElevenLabsTTSEngine

        el_config = ElevenLabsConfig(
            api_key=self._cfg.elevenlabs_api_key,
            voice_id=self._cfg.elevenlabs_voice_id,
        )
        tts = ElevenLabsTTSEngine(el_config, self._log)

        for i, section in enumerate(script.sections):
            if not section.narration.strip():
                continue
            path = output_dir / f"section_{i:02d}.mp3"
            result = tts.synthesize(
                section.narration, path,
                emotion=section.emotion,
            )
            narration_paths[i] = result.audio_path
            self._log.info("  Section %d (%s): %.1fs", i, section.section_type, result.duration)

        return narration_paths

    def _build_segments(
        self,
        script: AnalysisScript,
        narration_paths: dict[int, Path],
        work_dir: Path,
        report: dict,
    ) -> list[Path]:
        """Build individual video segments for each script section."""
        segments_dir = work_dir / "segments"
        segments_dir.mkdir(exist_ok=True)
        segments: list[Path] = []

        for i, section in enumerate(script.sections):
            seg_path = segments_dir / f"seg_{i:02d}.mp4"

            if i not in narration_paths:
                continue

            narration = narration_paths[i]

            if section.section_type == "data_reveal":
                # Create a data card with stats
                stats = {
                    "Total Mentions": str(report.get("total_mentions", "N/A")),
                    "Videos Appeared In": str(report.get("videos_appeared_in", "N/A")),
                    "Times Accused": str(report.get("times_accused_by_others", "N/A")),
                }
                roles = report.get("roles_played", {})
                if roles:
                    top_role = max(roles, key=roles.get)
                    stats["Most Played Role"] = f"{top_role} ({roles[top_role]}x)"

                card_path = segments_dir / f"card_{i:02d}.mp4"
                create_data_card(stats, report.get("player", "Player"), card_path)

                # Overlay narration on the data card
                self._overlay_narration(card_path, narration, seg_path)

            elif section.section_type == "clip_moment" and section.clip_query:
                # Try to find and extract a source clip
                source = find_source_video(section.clip_query, self._cfg.video_dirs)
                if source:
                    clip = segments_dir / f"clip_{i:02d}_raw.mp4"
                    extract_clip(source, section.clip_timestamp,
                                section.clip_timestamp + 12, clip)
                    annotated = segments_dir / f"clip_{i:02d}_annotated.mp4"
                    add_analysis_overlay(clip, annotated,
                                        top_text=section.visual_note[:60])
                    self._overlay_narration(annotated, narration, seg_path)
                else:
                    # No source clip — narration over dark background
                    create_narration_segment(narration, seg_path,
                                           visual_text=section.visual_note[:100])
            else:
                # Analysis or intro/outro — narration over dark background with text
                create_narration_segment(narration, seg_path,
                                       visual_text=section.visual_note[:100])

            if seg_path.exists():
                segments.append(seg_path)
                self._log.info("  Segment %d (%s): %s", i, section.section_type, seg_path.name)

        return segments

    def _overlay_narration(self, video: Path, narration: Path, output: Path) -> Path:
        """Overlay narration audio on a video segment."""
        import subprocess

        # Get narration duration
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(narration)],
            capture_output=True, text=True,
        )
        narr_dur = float(probe.stdout.strip())

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-i", str(narration),
            "-filter_complex",
            "[0:a]volume=0.15[bg];[1:a]volume=1.5[narr];"
            "[bg][narr]amix=inputs=2:duration=longest:dropout_transition=2[a]",
            "-map", "0:v", "-map", "[a]",
            "-t", str(narr_dur + 1),
            "-c:v", "copy", "-c:a", "aac",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            # Fallback: video may not have audio
            subprocess.run([
                "ffmpeg", "-y",
                "-i", str(video), "-i", str(narration),
                "-map", "0:v", "-map", "1:a",
                "-t", str(narr_dur + 1),
                "-c:v", "copy", "-c:a", "aac",
                str(output),
            ], check=True, capture_output=True)
        return output

    def _compile_video(self, segments: list[Path], output: Path) -> Path:
        """Concatenate all segments into the final video."""
        from peanut_reacts.core.ffmpeg import concatenate

        output.parent.mkdir(parents=True, exist_ok=True)

        if len(segments) == 1:
            import shutil
            shutil.copy2(str(segments[0]), str(output))
        else:
            # Add background music if available
            music_files = list(self._cfg.music_dir.glob("*.mp3")) + list(self._cfg.music_dir.glob("*.wav"))

            if music_files:
                raw = output.with_name(output.stem + "_raw.mp4")
                concatenate(segments, raw)
                from peanut_reacts.shorts.editor import add_background_music
                add_background_music(raw, random.choice(music_files), output,
                                    music_volume=self._cfg.music_volume)
                raw.unlink(missing_ok=True)
            else:
                concatenate(segments, output)

        self._log.info("Final video: %s", output.name)
        return output

    def _extract_shorts(
        self, segments: list[Path], script: AnalysisScript, output_dir: Path,
    ) -> list[Path]:
        """Extract the most interesting segments as Shorts."""
        from peanut_reacts.shorts.editor import crop_to_portrait, add_text_overlay

        output_dir.mkdir(parents=True, exist_ok=True)
        shorts: list[Path] = []

        # Pick clip_moment and data_reveal sections as shorts
        interesting = [
            (i, s) for i, s in enumerate(script.sections)
            if s.section_type in ("clip_moment", "data_reveal")
        ][:3]

        for idx, (i, section) in enumerate(interesting):
            if i >= len(segments):
                continue
            source = segments[i]

            portrait = output_dir / f"short_{idx + 1:02d}_portrait.mp4"
            crop_to_portrait(source, portrait)

            final = output_dir / f"short_{idx + 1:02d}.mp4"
            add_text_overlay(
                portrait, final,
                hook_text="You won't believe this stat! 🔍",
                ending_text="Full analysis on our channel!",
            )
            shorts.append(final)
            portrait.unlink(missing_ok=True)

        return shorts

    def _upload(
        self, video: Path, shorts: list[Path], script: AnalysisScript,
    ) -> tuple[str, list[str]]:
        """Upload to YouTube."""
        from peanut_reacts.upload.youtube_auth import get_authenticated_service
        from peanut_reacts.upload.youtube_upload import UploadMetadata, YouTubeUploader
        from peanut_reacts.shorts.editor import extract_thumbnail

        secrets = Path(self._cfg.youtube_client_secrets)
        service = get_authenticated_service(secrets)
        uploader = YouTubeUploader(service)

        # Upload long-form
        thumb = video.with_suffix(".jpg")
        extract_thumbnail(video, thumb)

        meta = UploadMetadata(
            title=script.title[:100],
            description=script.description[:5000],
            tags=script.tags[:30],
            category_id="24",
            privacy_status=self._cfg.privacy,
            thumbnail_path=thumb,
        )

        try:
            result = uploader.upload_video(video, meta)
            url = result.url
            self._log.info("Uploaded: %s", url)
        except Exception as e:
            self._log.error("Upload failed: %s", e)
            url = ""

        # Upload shorts
        shorts_urls = []
        for i, short in enumerate(shorts):
            try:
                s_meta = UploadMetadata(
                    title=f"{script.title[:50]} — Highlight #{i+1} #shorts",
                    description=f"Full analysis: {url}\n\n#sidemen #amongus #decoded #shorts",
                    tags=script.tags[:10] + ["shorts"],
                    category_id="24",
                    privacy_status=self._cfg.privacy,
                )
                r = uploader.upload_video(short, s_meta)
                shorts_urls.append(r.url)
            except Exception as e:
                self._log.error("Short upload failed: %s", e)

        return url, shorts_urls
