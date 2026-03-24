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
    render_reaction_video,
    word_timings_to_speech_events,
)
from peanut_reacts.character.tts import EdgeTTSEngine, TTSConfig, TTSResult
from peanut_reacts.compositing.layout import FacecamPosition, LayoutConfig
from peanut_reacts.core.ffmpeg import get_video_dimensions, get_video_duration
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
    max_reactions: int = 40

    # TTS
    tts_engine_type: str = "edge"  # "edge" or "elevenlabs"
    tts_config: TTSConfig = field(default_factory=TTSConfig)
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "TX3LPaxmHKxFdv7VOQHJ"  # Liam

    # Character rendering
    use_wav2lip: bool = False            # use AI lip-sync instead of PIL renderer
    peanut_face_image: Optional[Path] = None  # face image for Wav2Lip
    peanut_fps: int = 24
    peanut_canvas: int = 512
    peanut_char_size: int = 420
    peanut_seed: int = 0

    # Compositing layout
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    reaction_volume: float = 0.85
    original_duck: float = 0.25  # reduce original to 25% when peanut speaks

    # Speaker diarization (color-coded subtitles)
    enable_diarization: bool = False
    hf_token: str = ""
    num_speakers: int = 0  # 0 = auto-detect

    # Transcript
    transcript_path: Optional[Path] = None
    whisper_model: str = "base"
    whisper_device: str = "cuda"

    # Working directory
    work_dir: Optional[Path] = None


# ---------------------------------------------------------------------------
# Audio compositing helpers
_NVENC_AVAILABLE: bool | None = None


def _check_nvenc() -> bool:
    """Check if NVIDIA NVENC encoder is available."""
    global _NVENC_AVAILABLE
    if _NVENC_AVAILABLE is not None:
        return _NVENC_AVAILABLE
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        _NVENC_AVAILABLE = "h264_nvenc" in result.stdout
        if _NVENC_AVAILABLE:
            logger.info("GPU encoding enabled (NVIDIA NVENC)")
        return _NVENC_AVAILABLE
    except Exception:
        _NVENC_AVAILABLE = False
        return False


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

    # Strategy: generate a silent base track, then mix each TTS clip on top.
    valid_pairs = [(line, tts) for line, tts in tts_pairs if tts.duration > 0]

    _silent_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(video_duration), "-c:a", "aac", str(output_path),
    ]

    if not valid_pairs:
        subprocess.run(_silent_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return output_path

    # Input 0 = silent base, inputs 1..N = TTS clips
    inputs: list[str] = [
        "-f", "lavfi", "-t", str(video_duration), "-i", "anullsrc=r=44100:cl=stereo",
    ]
    filters: list[str] = []

    for idx, (line, tts) in enumerate(valid_pairs):
        input_idx = idx + 1
        inputs.extend(["-i", str(tts.audio_path)])
        delay_ms = int(line.start * 1000)
        filters.append(
            f"[{input_idx}:a]aresample=44100,adelay={delay_ms}|{delay_ms}[a{idx}]",
        )

    # Mix: base + all delayed clips
    n = len(valid_pairs)
    mix_inputs = "[0:a]" + "".join(f"[a{i}]" for i in range(n))
    filters.append(f"{mix_inputs}amix=inputs={n + 1}:duration=first:dropout_transition=2[out]")

    filter_complex = ";".join(filters)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(video_duration),
        str(output_path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[-500:]
        logger.error("FFmpeg audio mix failed:\n%s", stderr)
        raise RuntimeError(f"FFmpeg audio mix failed: {stderr[-200:]}")
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
    idle_loop_path: Path | None = None,
    layout: LayoutConfig | None = None,
    speaker_subtitle_filters: list[str] | None = None,
    video_width: int = 1920,
    video_height: int = 1080,
    original_duck: float = 0.4,
    reaction_volume: float = 0.85,
) -> Path:
    """Final ffmpeg pass: facecam-style overlay + speech text + audio mixing.

    Layout inspired by popular reaction video formats:
    - Peanut character in a bordered PiP frame (like a webcam feed)
    - Name tag below the character
    - Speech text near the facecam
    - Audio ducking when peanut speaks
    """
    layout = layout or LayoutConfig()
    vw, vh = video_width, video_height

    # ── Pre-compute pixel coordinates ────────────────────────────────
    fc_size = int(vw * layout.facecam_scale)  # facecam box size (square)
    fc_size = fc_size if fc_size % 2 == 0 else fc_size + 1  # ensure even
    m = layout.facecam_margin
    pos = layout.facecam_position

    if pos == FacecamPosition.BOTTOM_RIGHT:
        fc_x, fc_y = vw - fc_size - m, vh - fc_size - m
    elif pos == FacecamPosition.TOP_RIGHT:
        fc_x, fc_y = vw - fc_size - m, m
    elif pos == FacecamPosition.TOP_LEFT:
        fc_x, fc_y = m, m
    else:  # BOTTOM_LEFT
        fc_x, fc_y = m, vh - fc_size - m

    inputs = ["-i", str(original_video)]

    # Input 1: idle peanut loop (looped to cover full video)
    has_idle = idle_loop_path is not None and idle_loop_path.exists()
    if has_idle:
        inputs.extend(["-stream_loop", "-1", "-i", str(idle_loop_path)])
        idle_input_idx = 1
        segment_offset = 2  # speaking segments start at input 2
    else:
        idle_input_idx = -1
        segment_offset = 1

    for _, webm_path in peanut_segments:
        inputs.extend(["-i", str(webm_path)])
    inputs.extend(["-i", str(reaction_audio)])

    n_segments = len(peanut_segments)
    reaction_audio_idx = segment_offset + n_segments

    video_filters: list[str] = []
    prev_label = "0:v"

    # ── Draw facecam background frame (ALWAYS visible) ─────────────
    video_filters.append(
        f"[{prev_label}]drawbox="
        f"x={fc_x}:y={fc_y}:w={fc_size}:h={fc_size}:"
        f"color={layout.facecam_bg_color}:t=fill[bg0]",
    )
    prev_label = "bg0"

    # Border (always visible)
    video_filters.append(
        f"[{prev_label}]drawbox="
        f"x={fc_x}:y={fc_y}:w={fc_size}:h={fc_size}:"
        f"color={layout.facecam_border_color}:t={layout.facecam_border_width}[frame0]",
    )
    prev_label = "frame0"

    # ── Name tag at bottom of facecam frame (always visible) ─────────
    if layout.name_tag_enabled:
        tag_text = layout.name_tag_text.replace("'", "\u2019").replace(":", "\\:")
        tag_center_x = fc_x + fc_size // 2
        tag_y = fc_y + fc_size - layout.name_tag_font_size - 10

        video_filters.append(
            f"[{prev_label}]drawtext="
            f"text='{tag_text}':"
            f"fontsize={layout.name_tag_font_size}:fontcolor={layout.name_tag_font_color}:"
            f"borderw=1:bordercolor=black:"
            f"box=1:boxcolor={layout.name_tag_bg_color}:boxborderw=6:"
            f"x={tag_center_x}-tw/2:y={tag_y}[nametag]",
        )
        prev_label = "nametag"

    # ── Overlay idle peanut (always visible, looping) ───────────────
    if has_idle:
        video_filters.append(
            f"[{idle_input_idx}:v]scale={fc_size}:{fc_size},"
            f"colorkey=0x00FF00:0.3:0.2[idle_ck]",
        )
        video_filters.append(
            f"[{prev_label}][idle_ck]overlay=x={fc_x}:y={fc_y}:shortest=1[idle_ov]",
        )
        prev_label = "idle_ov"

    # ── Overlay speaking peanut segments (with colorkey + scale) ──────
    for i, (line, _) in enumerate(peanut_segments):
        input_idx = i + segment_offset
        out_label = f"v{i}"
        enable = f"between(t,{line.start:.2f},{line.end:.2f})"

        video_filters.append(
            f"[{input_idx}:v]scale={fc_size}:{fc_size},"
            f"colorkey=0x00FF00:0.3:0.2[ck{i}]",
        )
        video_filters.append(
            f"[{prev_label}][ck{i}]overlay=x={fc_x}:y={fc_y}"
            f":enable='{enable}'[{out_label}]",
        )
        prev_label = out_label

    # ── Peanut speech text (above facecam, yellow with bg) ──────────
    for i, (line, _) in enumerate(peanut_segments):
        enable = f"between(t,{line.start:.2f},{line.end:.2f})"
        safe_text = (
            line.text
            .replace("\\", "\\\\")
            .replace("'", "\u2019")
            .replace(":", "\\:")
            .replace("%", "%%")
        )
        out_label = f"t{i}"

        # Position above facecam, right-aligned to facecam right edge
        right_edge = fc_x + fc_size
        speech_x = f"{right_edge}-tw"
        speech_y = str(max(10, fc_y - 50))  # above facecam with padding

        box_opts = ""
        if layout.speech_bg_enabled:
            box_opts = f"box=1:boxcolor={layout.speech_bg_color}:boxborderw=10:"

        font_size = layout.speech_font_size
        if len(line.text) > 40:
            font_size = max(18, font_size - 4)

        video_filters.append(
            f"[{prev_label}]drawtext="
            f"text='{safe_text}':"
            f"fontsize={font_size}:"
            f"fontcolor={layout.speech_font_color}:"
            f"borderw={layout.speech_border_width}:"
            f"bordercolor={layout.speech_border_color}:"
            f"{box_opts}"
            f"x={speech_x}:y={speech_y}:"
            f"enable='{enable}'[{out_label}]",
        )
        prev_label = out_label

    # ── Color-coded speaker subtitles (if diarization enabled) ─────
    if speaker_subtitle_filters:
        for i, filt in enumerate(speaker_subtitle_filters):
            out_label = f"sub{i}"
            video_filters.append(f"[{prev_label}]{filt}[{out_label}]")
            prev_label = out_label
        logger.info("Added %d color-coded subtitle segments", len(speaker_subtitle_filters))

    final_video_label = prev_label if video_filters else "0:v"

    # ── Audio mixing with ducking ────────────────────────────────────
    lines = [line for line, _ in peanut_segments]
    duck_expr = _build_ducking_expr(lines)

    audio_filters: list[str] = []
    if duck_expr:
        # Duck original audio during peanut speech (reduce to 20%)
        audio_filters.append(
            f"[0:a]volume={original_duck}:enable='{duck_expr}'[orig_a]",
        )
        # Boost reaction TTS audio significantly (TTS is much quieter than video audio)
        # volume=6.0 compensates for amix normalization (which halves both inputs)
        audio_filters.append(
            f"[{reaction_audio_idx}:a]volume=6.0[react_a]",
        )
        # amix halves each input by default (weights=1 1), so we double everything
        audio_filters.append(
            "[orig_a][react_a]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        )
        final_audio_label = "[aout]"
    else:
        final_audio_label = "0:a"

    all_filters = ";".join(video_filters + audio_filters)

    cmd = ["ffmpeg", "-y"] + inputs
    if all_filters:
        # Write filter_complex to a file to avoid Windows command line length limit
        # (color-coded subtitles can generate 1000+ drawtext filters)
        filter_script = output_path.parent / f"_filter_{output_path.stem}.txt"
        filter_script.write_text(all_filters, encoding="utf-8")
        cmd.extend(["-filter_complex_script", str(filter_script)])
        cmd.extend(["-map", f"[{final_video_label}]" if video_filters else "0:v"])
        cmd.extend(["-map", final_audio_label])
    else:
        cmd.extend(["-map", "0:v", "-map", "0:a"])

    # Try GPU encoding first (NVENC), fall back to CPU (libx264)
    use_nvenc = _check_nvenc()
    if use_nvenc:
        cmd.extend([
            "-c:v", "h264_nvenc", "-preset", "fast",
            "-b:v", "8M", "-maxrate", "12M",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ])
    else:
        cmd.extend([
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ])

    logger.info("Running final composite ...")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[-1000:]
        logger.error("FFmpeg composite failed (exit %d):\n%s", result.returncode, stderr)
        raise RuntimeError(f"FFmpeg composite failed: {stderr[-200:]}")
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

    def _fill_gaps(
        self,
        script_lines: list,
        transcript: list[dict],
        llm,
        video_duration: float,
        video_title: str,
        max_gap: float = 25.0,
    ) -> list:
        """Detect silent gaps > max_gap seconds and generate filler reactions.

        Makes a second LLM call specifically targeting the gap regions,
        using transcript segments from those time ranges for context.
        """
        if not script_lines:
            return script_lines

        # Find gaps
        gaps: list[tuple[float, float]] = []
        prev_end = 0.0
        for line in script_lines:
            if line.start - prev_end > max_gap:
                gaps.append((prev_end + 2.0, line.start - 2.0))
            prev_end = max(prev_end, line.end)
        # Check gap at end of video
        if video_duration - prev_end > max_gap:
            gaps.append((prev_end + 2.0, video_duration - 2.0))

        if not gaps:
            self._log.info("No significant gaps found.")
            return script_lines

        self._log.info("Found %d gap(s) to fill: %s",
                        len(gaps),
                        ", ".join(f"{s:.0f}-{e:.0f}s" for s, e in gaps))

        # Build context from transcript segments in gap regions
        gap_transcript = []
        for seg in transcript:
            seg_start = float(seg.get("start", 0))
            for gap_start, gap_end in gaps:
                if gap_start <= seg_start <= gap_end:
                    gap_transcript.append(seg)
                    break

        if not gap_transcript:
            return script_lines

        # Build a focused prompt for gap filling
        gap_context = "\n".join(
            f"[{seg['start']:.1f}s] {seg.get('text', '').strip()}"
            for seg in gap_transcript[:40]
        )

        gap_ranges = ", ".join(f"{s:.0f}s-{e:.0f}s" for s, e in gaps)
        prompt = (
            f'VIDEO: "{video_title}"\n\n'
            f"The following time ranges need reactions (currently silent):\n"
            f"{gap_ranges}\n\n"
            f"TRANSCRIPT for these sections:\n{gap_context}\n\n"
            f"Generate 1-2 short reactions for EACH gap listed above. "
            f"Place reactions in the MIDDLE of each gap."
        )

        from peanut_reacts.character.reaction_generator import (
            _SYSTEM_PROMPT, _parse_llm_response, ReactionLine,
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            response = llm.complete(messages)
            filler_lines = _parse_llm_response(response, video_duration)
            self._log.info("Gap-fill generated %d additional reaction(s).", len(filler_lines))

            # Merge and re-sort
            all_lines = list(script_lines) + filler_lines
            all_lines.sort(key=lambda l: l.start)

            # Remove any that overlap with existing reactions
            filtered: list[ReactionLine] = []
            for line in all_lines:
                if filtered and line.start < filtered[-1].end + 2.0:
                    continue
                filtered.append(line)

            return filtered
        except Exception as e:
            self._log.warning("Gap-fill failed (continuing without): %s", e)
            return script_lines

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
        video_w, video_h = get_video_dimensions(job.video_path)
        self._log.info("Video: %dx%d, %.1fs", video_w, video_h, video_duration)

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

        # Step 3b: Fill gaps — generate additional reactions for silent stretches
        filled_lines = self._fill_gaps(
            script.lines, transcript, llm,
            video_duration, job.video_path.stem,
        )
        if len(filled_lines) > len(script.lines):
            self._log.info(
                "Gap-fill: %d → %d reactions (added %d)",
                len(script.lines), len(filled_lines),
                len(filled_lines) - len(script.lines),
            )
            script = ReactionScript(
                lines=filled_lines[:job.max_reactions],
                video_title=script.video_title,
                generated_by=script.generated_by,
            )

        # Save script for reference
        script_path = work_dir / "reaction_script.json"
        script_path.write_text(json.dumps(script_to_dict(script), indent=2), encoding="utf-8")

        # Step 4: Synthesize TTS
        tts_dir = work_dir / "tts"
        line_dicts = [{"start": l.start, "end": l.end, "text": l.text, "emotion": l.emotion}
                      for l in script.lines]

        if job.tts_engine_type == "elevenlabs" and job.elevenlabs_api_key:
            from peanut_reacts.character.elevenlabs_tts import (
                ElevenLabsConfig, ElevenLabsTTSEngine,
            )
            el_config = ElevenLabsConfig(
                api_key=job.elevenlabs_api_key,
                voice_id=job.elevenlabs_voice_id,
            )
            el_engine = ElevenLabsTTSEngine(el_config, self._log)
            el_results = el_engine.synthesize_lines(line_dicts, tts_dir)

            # Convert ElevenLabs results to the same format as Edge TTS
            tts_results = []
            for line_dict, el_result in el_results:
                tts_result = TTSResult(
                    audio_path=el_result.audio_path,
                    duration=el_result.duration,
                    word_timings=[],  # ElevenLabs doesn't provide word timings
                )
                tts_results.append((line_dict, tts_result))
        else:
            tts_engine = EdgeTTSEngine(job.tts_config, self._log)
            tts_results = tts_engine.synthesize_lines(line_dicts, tts_dir)

        # Map back to ReactionLine objects with corrected timing.
        tts_pairs: list[tuple[ReactionLine, TTSResult]] = []
        for line_dict, tts_result in tts_results:
            if tts_result.duration <= 0:
                continue
            start = float(line_dict["start"])
            end = min(start + tts_result.duration, video_duration)
            line = ReactionLine(
                start=start,
                end=end,
                text=line_dict["text"],
                emotion=line_dict.get("emotion", "amused"),
            )
            tts_pairs.append((line, tts_result))

        # Prevent overlapping reactions: trim end to not exceed next start
        for i in range(len(tts_pairs) - 1):
            cur_line, _ = tts_pairs[i]
            next_line, _ = tts_pairs[i + 1]
            if cur_line.end > next_line.start - 0.5:
                trimmed = ReactionLine(
                    start=cur_line.start,
                    end=next_line.start - 0.5,
                    text=cur_line.text,
                    emotion=cur_line.emotion,
                )
                tts_pairs[i] = (trimmed, tts_pairs[i][1])

        self._log.info("Synthesized %d TTS segment(s).", len(tts_pairs))

        # Step 5: Render peanut animation segments
        use_wav2lip = job.use_wav2lip and job.peanut_face_image and job.peanut_face_image.exists()

        if use_wav2lip:
            from peanut_reacts.character.wav2lip_sync import wav2lip_available, render_lipsync_reaction
            if not wav2lip_available():
                self._log.warning("Wav2Lip not available — falling back to PIL renderer.")
                use_wav2lip = False

        # Step 5a: Idle peanut loop
        idle_path = work_dir / "peanut_idle_loop.mp4"
        if not idle_path.exists():
            if use_wav2lip:
                # For Wav2Lip: create an animated idle from the static face
                # Add breathing zoom, gentle sway, and subtle movement
                self._log.info("Creating animated idle peanut loop (4s) ...")
                canvas = job.peanut_canvas
                pad = canvas + 68  # extra space for sway + rotation
                # Dramatic head sway + tilt for a "living" character feel
                vf = (
                    f"scale={pad}:{pad}:force_original_aspect_ratio=decrease,"
                    f"pad={pad}:{pad}:(ow-iw)/2:(oh-ih)/2:color=0x00FF00,"
                    # Head sway (left/right + up/down bobbing)
                    f"crop=w={canvas}:h={canvas}"
                    f":x='{(pad-canvas)//2}+18*sin(2*PI*t/2.5)'"
                    f":y='{(pad-canvas)//2}+12*sin(2*PI*t/1.8)',"
                    # Head tilt rotation
                    f"rotate='0.03*sin(2*PI*t/3)':fillcolor=0x00FF00"
                    f":ow={canvas}:oh={canvas}"
                )
                subprocess.run([
                    "ffmpeg", "-y",
                    "-loop", "1", "-i", str(job.peanut_face_image.resolve()),
                    "-t", "4", "-r", "25",
                    "-vf", vf,
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                    str(idle_path),
                ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            else:
                self._log.info("Rendering idle peanut loop (4s) ...")
                render_reaction_video(
                    [],  # no speech events = mouth closed
                    4.0,
                    idle_path,
                    fps=job.peanut_fps,
                    canvas=job.peanut_canvas,
                    char_size=job.peanut_char_size,
                    seed=job.peanut_seed,
                    emotion="neutral",
                )

        # Step 5b: Render speech-synced peanut segments
        peanut_segments: list[tuple[ReactionLine, Path]] = []
        webm_dir = work_dir / "peanut_webm"
        webm_dir.mkdir(exist_ok=True)

        for idx, (line, tts_result) in enumerate(tts_pairs):
            if tts_result.duration <= 0:
                continue

            webm_path = webm_dir / f"peanut_{idx + 1:03d}.mp4"

            self._log.info(
                "Rendering peanut segment %d (%.2fs, %s) ...",
                idx + 1, tts_result.duration, line.emotion,
            )

            if use_wav2lip:
                # Wav2Lip: animate face image with TTS audio
                render_lipsync_reaction(
                    job.peanut_face_image,
                    tts_result.audio_path,
                    webm_path,
                    tts_result.duration,
                    canvas_size=job.peanut_canvas,
                    green_screen=True,
                )
            else:
                # PIL renderer: speech-synced cartoon peanut
                speech_events = word_timings_to_speech_events(
                    tts_result.word_timings, line_start=0.0,
                )
                render_reaction_video(
                    speech_events,
                    tts_result.duration + 0.3,
                    webm_path,
                    fps=job.peanut_fps,
                    canvas=job.peanut_canvas,
                    char_size=job.peanut_char_size,
                    seed=job.peanut_seed,
                    emotion=line.emotion,
                )

            peanut_segments.append((line, webm_path))

        # Step 6: Build reaction audio track
        reaction_audio = work_dir / "reaction_audio.aac"
        _build_reaction_audio(tts_pairs, video_duration, reaction_audio)

        # Step 6b: Speaker diarization (color-coded subtitles)
        speaker_filters: list[str] | None = None
        if job.enable_diarization:
            try:
                from peanut_reacts.analysis.diarization import (
                    diarize_video,
                    align_transcript_with_speakers,
                    build_subtitle_filters,
                )
                diarization = diarize_video(
                    job.video_path,
                    hf_token=job.hf_token,
                    num_speakers=job.num_speakers or None,
                    work_dir=work_dir,
                )
                colored_segs = align_transcript_with_speakers(transcript, diarization)
                speaker_filters = build_subtitle_filters(
                    colored_segs, font_size=30, y_position="h-80", x_position="20",
                )
                self._log.info(
                    "Diarization: %d speakers, %d subtitle segments",
                    diarization.num_speakers, len(colored_segs),
                )
            except Exception as e:
                self._log.warning("Diarization failed (continuing without): %s", e)

        # Step 7: Final composite with facecam layout
        job.output_path.parent.mkdir(parents=True, exist_ok=True)
        _final_composite(
            job.video_path,
            peanut_segments,
            reaction_audio,
            job.output_path,
            idle_loop_path=idle_path,
            layout=job.layout,
            speaker_subtitle_filters=speaker_filters,
            video_width=video_w,
            video_height=video_h,
            original_duck=job.original_duck,
            reaction_volume=job.reaction_volume,
        )

        self._log.info("Pipeline complete! Output: %s", job.output_path)
        return job.output_path
