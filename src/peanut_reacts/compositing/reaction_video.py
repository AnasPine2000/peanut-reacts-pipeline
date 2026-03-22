"""
Full reaction video pipeline orchestrator.

Combines all pipeline stages: transcript loading, comment extraction,
LLM reaction generation, TTS synthesis, speech-synced animation,
and final audio/video compositing.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from peanut_reacts.analysis.comments import (
    CommentHighlight,
    extract_comment_highlights,
)
from peanut_reacts.character.reaction_generator import (
    LLMConfig,
    ReactionLine,
    ReactionScript,
    ReactionScriptGenerator,
    create_llm_provider,
    script_to_dict,
)
from peanut_reacts.character.sync import (
    render_reaction_webm,
    word_timings_to_speech_events,
)
from peanut_reacts.character.tts import EdgeTTSEngine, TTSConfig, TTSResult
from peanut_reacts.core.ffmpeg import get_video_duration
from peanut_reacts.core.srt import parse_srt
from peanut_reacts.core.transcription import transcribe_video

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReactionJob:
    """Configuration for a full reaction video pipeline run."""
    video_path: Path
    output_path: Path

    # Comment extraction
    info_json_path: Optional[Path] = None
    cluster_window: float = 15.0
    max_highlights: int = 10

    # LLM
    llm_config: LLMConfig = field(default_factory=LLMConfig)
    max_reactions: int = 15

    # TTS
    tts_config: TTSConfig = field(default_factory=TTSConfig)

    # Character rendering
    peanut_fps: int = 24
    peanut_canvas: int = 512
    peanut_char_size: int = 420
    peanut_seed: int = 0

    # Compositing
    peanut_scale: float = 0.20
    peanut_x: str = "W-w-20"
    peanut_y: str = "H-h-20"
    reaction_volume: float = 0.85
    original_duck: float = 0.4

    # Transcript
    transcript_path: Optional[Path] = None
    whisper_model: str = "base"
    whisper_device: str = "cuda"

    # Working directory
    work_dir: Optional[Path] = None


# ---------------------------------------------------------------------------
# Audio compositing helpers
# ---------------------------------------------------------------------------

def _build_reaction_audio(
    tts_pairs: list[tuple[ReactionLine, TTSResult]],
    video_duration: float,
    output_path: Path,
) -> Path:
    """Create a single audio track with all TTS segments at correct offsets.

    Uses ffmpeg ``adelay`` filter to place each audio clip at the right time,
    then mixes them into a single track.
    """
    if not tts_pairs:
        # Create silent track
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={video_duration}",
            "-c:a", "aac", str(output_path),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return output_path

    inputs: list[str] = []
    filters: list[str] = []

    for idx, (line, tts) in enumerate(tts_pairs):
        if tts.duration <= 0:
            continue
        inputs.extend(["-i", str(tts.audio_path)])
        delay_ms = int(line.start * 1000)
        filters.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")

    if not filters:
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={video_duration}",
            "-c:a", "aac", str(output_path),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return output_path

    n = len(filters)
    mix_inputs = "".join(f"[a{i}]" for i in range(n))
    filters.append(f"{mix_inputs}amix=inputs={n}:duration=longest:dropout_transition=2[mixed]")
    # Pad/trim to video duration
    filters.append(f"[mixed]apad=whole_dur={video_duration}[out]")

    filter_complex = ";".join(filters)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(video_duration),
        str(output_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    logger.info("Built reaction audio track: %s", output_path.name)
    return output_path


def _build_ducking_expr(lines: list[ReactionLine], pad: float = 0.2) -> str:
    """Build an ffmpeg volume enable expression that ducks during reactions."""
    if not lines:
        return ""
    conditions = "+".join(
        f"between(t,{max(0, l.start - pad):.2f},{l.end + pad:.2f})" for l in lines
    )
    return conditions


def _final_composite(
    original_video: Path,
    peanut_segments: list[tuple[ReactionLine, Path]],
    reaction_audio: Path,
    output_path: Path,
    *,
    peanut_x: str = "W-w-20",
    peanut_y: str = "H-h-20",
    original_duck: float = 0.4,
    reaction_volume: float = 0.85,
) -> Path:
    """Final ffmpeg pass: overlay peanut segments + mix audio."""
    inputs = ["-i", str(original_video)]

    # Add each peanut WebM as an input
    for _, webm_path in peanut_segments:
        inputs.extend(["-i", str(webm_path)])

    inputs.extend(["-i", str(reaction_audio)])

    # Build video overlay chain
    n_segments = len(peanut_segments)
    reaction_audio_idx = n_segments + 1  # 0 = original, 1..N = peanut, N+1 = reaction audio

    video_filters: list[str] = []
    prev_label = "0:v"

    for i, (line, _) in enumerate(peanut_segments):
        input_idx = i + 1
        out_label = f"v{i}"
        enable = f"between(t,{line.start:.2f},{line.end:.2f})"
        video_filters.append(
            f"[{prev_label}][{input_idx}:v]overlay=x={peanut_x}:y={peanut_y}"
            f":enable='{enable}':format=auto[{out_label}]",
        )
        prev_label = out_label

    final_video_label = prev_label if video_filters else "0:v"

    # Audio mixing with ducking
    lines = [line for line, _ in peanut_segments]
    duck_expr = _build_ducking_expr(lines)

    audio_filters: list[str] = []
    if duck_expr:
        audio_filters.append(
            f"[0:a]volume={original_duck}:enable='{duck_expr}'[orig_a]",
        )
        audio_filters.append(f"[{reaction_audio_idx}:a]volume={reaction_volume}[react_a]")
        audio_filters.append(
            "[orig_a][react_a]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        )
        final_audio_label = "[aout]"
    else:
        final_audio_label = "0:a"

    all_filters = ";".join(video_filters + audio_filters)

    cmd = ["ffmpeg", "-y"] + inputs
    if all_filters:
        cmd.extend(["-filter_complex", all_filters])
        cmd.extend(["-map", f"[{final_video_label}]" if video_filters else "0:v"])
        cmd.extend(["-map", final_audio_label])
    else:
        cmd.extend(["-map", "0:v", "-map", "0:a"])

    cmd.extend([
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(output_path),
    ])

    logger.info("Running final composite ...")
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    logger.info("Final output: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

class ReactionPipeline:
    """Full reaction video pipeline."""

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self._log = log or logger

    def _load_transcript(self, job: ReactionJob) -> list[dict]:
        """Load transcript from file or generate via Whisper."""
        if job.transcript_path and job.transcript_path.exists():
            if job.transcript_path.suffix == ".srt":
                self._log.info("Loading transcript from SRT: %s", job.transcript_path)
                return parse_srt(job.transcript_path)
            self._log.info("Loading transcript from JSON: %s", job.transcript_path)
            return json.loads(job.transcript_path.read_text(encoding="utf-8"))

        # Auto-detect SRT
        srt_path = job.video_path.with_suffix(".en.srt")
        if srt_path.exists():
            self._log.info("Auto-detected SRT: %s", srt_path)
            return parse_srt(srt_path)

        # Auto-detect JSON transcript
        json_path = job.video_path.with_name(job.video_path.stem + "_full_transcript.json")
        if json_path.exists():
            self._log.info("Auto-detected transcript: %s", json_path)
            return json.loads(json_path.read_text(encoding="utf-8"))

        # Fall back to Whisper
        self._log.info("No existing transcript found. Transcribing with Whisper ...")
        return transcribe_video(
            job.video_path,
            model_name=job.whisper_model,
            device=job.whisper_device,
        )

    def _find_info_json(self, job: ReactionJob) -> Optional[Path]:
        """Find the yt-dlp info.json file."""
        if job.info_json_path and job.info_json_path.exists():
            return job.info_json_path
        # Auto-detect
        candidates = [
            job.video_path.with_suffix(".info.json"),
            job.video_path.with_name(job.video_path.stem + ".info.json"),
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    def run(self, job: ReactionJob) -> Path:
        """Execute the full reaction pipeline."""
        work_dir = job.work_dir or Path(tempfile.mkdtemp(prefix="peanut_react_"))
        work_dir.mkdir(parents=True, exist_ok=True)
        self._log.info("Working directory: %s", work_dir)

        video_duration = get_video_duration(job.video_path)
        self._log.info("Video duration: %.1fs", video_duration)

        # Step 1: Load transcript
        transcript = self._load_transcript(job)
        self._log.info("Transcript: %d segments", len(transcript))

        # Step 2: Extract comment highlights
        highlights: list[CommentHighlight] = []
        info_json = self._find_info_json(job)
        if info_json:
            highlights = extract_comment_highlights(
                info_json,
                cluster_window=job.cluster_window,
                max_highlights=job.max_highlights,
            )
            self._log.info("Comment highlights: %d", len(highlights))
        else:
            self._log.info("No info.json found — proceeding without comments.")

        # Step 3: Generate reaction script
        llm = create_llm_provider(job.llm_config)
        generator = ReactionScriptGenerator(llm, self._log)
        script = generator.generate(
            transcript, highlights,
            video_title=job.video_path.stem,
            video_duration=video_duration,
            max_reactions=job.max_reactions,
        )

        if not script.lines:
            self._log.warning("No reactions generated. Returning original video.")
            return job.video_path

        # Save script for reference
        script_path = work_dir / "reaction_script.json"
        script_path.write_text(json.dumps(script_to_dict(script), indent=2), encoding="utf-8")

        # Step 4: Synthesize TTS
        tts_engine = EdgeTTSEngine(job.tts_config, self._log)
        tts_dir = work_dir / "tts"
        line_dicts = [{"start": l.start, "end": l.end, "text": l.text, "emotion": l.emotion}
                      for l in script.lines]
        tts_results = tts_engine.synthesize_lines(line_dicts, tts_dir)

        # Map back to ReactionLine objects
        tts_pairs: list[tuple[ReactionLine, TTSResult]] = []
        for (line_dict, tts_result), reaction_line in zip(tts_results, script.lines):
            # Update end time with actual TTS duration
            reaction_line.end = reaction_line.start + tts_result.duration
            tts_pairs.append((reaction_line, tts_result))

        self._log.info("Synthesized %d TTS segment(s).", len(tts_pairs))

        # Step 5: Render speech-synced peanut animation
        peanut_segments: list[tuple[ReactionLine, Path]] = []
        webm_dir = work_dir / "peanut_webm"
        webm_dir.mkdir(exist_ok=True)

        for idx, (line, tts_result) in enumerate(tts_pairs):
            if tts_result.duration <= 0:
                continue

            speech_events = word_timings_to_speech_events(
                tts_result.word_timings, line_start=0.0,
            )
            webm_path = webm_dir / f"peanut_{idx + 1:03d}.webm"

            self._log.info("Rendering peanut segment %d (%.2fs) ...", idx + 1, tts_result.duration)
            render_reaction_webm(
                speech_events,
                tts_result.duration + 0.3,
                webm_path,
                fps=job.peanut_fps,
                canvas=job.peanut_canvas,
                char_size=job.peanut_char_size,
                seed=job.peanut_seed,
            )
            peanut_segments.append((line, webm_path))

        # Step 6: Build reaction audio track
        reaction_audio = work_dir / "reaction_audio.aac"
        _build_reaction_audio(tts_pairs, video_duration, reaction_audio)

        # Step 7: Final composite
        job.output_path.parent.mkdir(parents=True, exist_ok=True)
        _final_composite(
            job.video_path,
            peanut_segments,
            reaction_audio,
            job.output_path,
            peanut_x=job.peanut_x,
            peanut_y=job.peanut_y,
            original_duck=job.original_duck,
            reaction_volume=job.reaction_volume,
        )

        self._log.info("Pipeline complete! Output: %s", job.output_path)
        return job.output_path
