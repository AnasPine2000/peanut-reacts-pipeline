"""Pipeline runners for each content source type.

Each function takes a ChannelConfig + GlobalSettings and runs the full
pipeline: source -> process -> upload. Returns (videos_processed, errors).
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from typing import Optional
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .channel_config import ChannelConfig, GlobalSettings
    from .db import PipelineDB

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"

# Ensure src is importable
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _nvenc_available() -> bool:
    """Deprecated wrapper — delegates to core.ffmpeg.nvenc_available."""
    from peanut_reacts.core.ffmpeg import nvenc_available
    return nvenc_available()


def _get_encoder(settings: GlobalSettings) -> str:
    if _nvenc_available():
        return settings.ffmpeg_encoder
    return settings.ffmpeg_fallback


# ═══════════════════════════════════════════════════════════════
# PLAYLIST REACTION PIPELINE (Sidemen, KSI, etc.)
# ═══════════════════════════════════════════════════════════════

def run_reaction_pipeline(
    channel: ChannelConfig,
    settings: GlobalSettings,
    db: PipelineDB,
    overrides=None,
    applied_recs: Optional[list] = None,
) -> tuple[int, int]:
    """Download playlist videos, generate reactions, composite, upload."""
    from peanut_reacts.character.reaction_generator import create_llm_provider, LLMConfig
    from peanut_reacts.character.tts import EdgeTTSEngine, TTSConfig
    from peanut_reacts.core.ffmpeg import get_video_duration, concatenate
    from peanut_reacts.core.srt import parse_srt
    from peanut_reacts.upload.youtube_auth import get_authenticated_service
    from peanut_reacts.upload.youtube_upload import YouTubeUploader, UploadMetadata

    videos_done, errors = 0, 0
    out_dir = PROJECT_ROOT / settings.output_dir / channel.id
    out_dir.mkdir(parents=True, exist_ok=True)

    # LLM setup
    from peanut_reacts.core.config import load_config
    cfg = load_config()
    llm = create_llm_provider(LLMConfig(
        provider=settings.llm_provider,
        api_key=cfg.deepseek_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    ))

    # TTS engine
    tts = EdgeTTSEngine(TTSConfig(voice=channel.tts_voice, rate=channel.tts_rate))

    # Find videos to process (yt-dlp list playlist)
    for source_url in channel.source_urls:
        try:
            result = subprocess.run(
                ["yt-dlp", "--flat-playlist", "--print", "%(id)s %(title)s", source_url],
                capture_output=True, text=True, timeout=60,
            )
            video_lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        except Exception as e:
            log.error("Failed to list playlist %s: %s", source_url, e)
            errors += 1
            continue

        # Check which videos are already processed
        existing = {j["video_id"] for j in db.get_channel_jobs(channel.id, limit=500) if j["status"] == "done"}

        new_videos = []
        for line in video_lines:
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[0] not in existing:
                new_videos.append((parts[0], parts[1]))

        if not new_videos:
            log.info("[%s] No new videos to process", channel.id)
            continue

        # Process up to max_videos_per_run
        for vid_id, vid_title in new_videos[:channel.max_videos_per_run]:
            job_id = db.create_job(channel.id, vid_id, vid_title)
            try:
                db.start_job(job_id, "download")
                log.info("[%s] Downloading %s: %s", channel.id, vid_id, vid_title)

                # Download video + subtitles
                dl_dir = out_dir / "downloads"
                dl_dir.mkdir(exist_ok=True)
                video_path = dl_dir / f"{vid_id}.mp4"
                srt_path = dl_dir / f"{vid_id}.srt"

                if not video_path.exists():
                    # stdout=DEVNULL + -q to avoid the classic
                    # capture_output pipe-buffer deadlock on long downloads.
                    subprocess.run([
                        "yt-dlp",
                        "-q", "--no-warnings", "--newline",
                        # Prefer mp4-compatible H.264 + AAC streams to avoid merge issues
                        "-f", "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]/best[height<=1080]",
                        "--merge-output-format", "mp4",
                        "--write-auto-sub", "--sub-lang", "en",
                        "--convert-subs", "srt",
                        "-o", str(video_path),
                        f"https://youtube.com/watch?v={vid_id}",
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=7200)

                # Parse transcript
                db.update_job(job_id, step="transcribe")
                srt_candidates = list(dl_dir.glob(f"{vid_id}*.srt"))
                if not srt_candidates:
                    log.warning("[%s] No SRT found for %s, skipping", channel.id, vid_id)
                    db.fail_job(job_id, "No subtitles found", "transcribe")
                    errors += 1
                    continue

                segments = parse_srt(srt_candidates[0])
                duration = get_video_duration(video_path)

                # Generate reactions
                db.update_job(job_id, step="react")
                log.info("[%s] Generating reactions for %s (%.1f min)", channel.id, vid_id, duration / 60)

                chunk_size = 30  # seconds
                reactions = []
                for chunk_start in range(0, int(duration), chunk_size):
                    chunk_end = min(chunk_start + chunk_size, int(duration))
                    chunk_segs = [s for s in segments if chunk_start <= s.get("start", 0) < chunk_end]
                    if not chunk_segs:
                        continue

                    transcript_text = " ".join(s.get("text", "") for s in chunk_segs)
                    prompt = (
                        f"{channel.llm_personality}\n\n"
                        f"React to this segment ({chunk_start}s-{chunk_end}s):\n"
                        f"\"{transcript_text[:500]}\"\n\n"
                        f"Give exactly {int(channel.reactions_per_minute * chunk_size / 60)} reactions. "
                        f"Format: JSON array of objects with 'start' (seconds), 'text', 'emotion'."
                    )

                    try:
                        response = llm.complete(
                            [{"role": "user", "content": prompt}],
                            temperature=settings.llm_temperature,
                            max_tokens=1000,
                        )
                        # Parse JSON from response
                        text = response.strip()
                        if "```json" in text:
                            text = text.split("```json")[1].split("```")[0]
                        elif "```" in text:
                            text = text.split("```")[1].split("```")[0]
                        chunk_reactions = json.loads(text)
                        for r in chunk_reactions:
                            r["start"] = float(r.get("start", chunk_start)) + chunk_start
                        reactions.extend(chunk_reactions)
                    except Exception as e:
                        log.warning("LLM chunk %d failed: %s", chunk_start, e)

                log.info("[%s] Generated %d reactions", channel.id, len(reactions))

                # TTS
                db.update_job(job_id, step="tts")
                tts_dir = out_dir / "tts" / vid_id
                tts_dir.mkdir(parents=True, exist_ok=True)

                for i, rx in enumerate(reactions):
                    tts_path = tts_dir / f"rx_{i:04d}.mp3"
                    if not tts_path.exists():
                        try:
                            asyncio.run(tts.synthesize(rx["text"], tts_path))
                        except Exception as e:
                            log.warning("TTS %d failed: %s", i, e)

                # Composite (simplified - uses FFmpeg directly)
                db.update_job(job_id, step="composite")
                final_path = out_dir / f"{vid_id}_reaction.mp4"

                if not final_path.exists():
                    # Build audio mix with reactions
                    encoder = _get_encoder(settings)
                    _build_reaction_audio_and_composite(
                        video_path, tts_dir, reactions, final_path,
                        channel.gameplay_volume, channel.reaction_boost,
                        encoder, duration,
                    )

                # Upload
                db.update_job(job_id, step="upload")
                if channel.oauth_token and channel.client_secrets:
                    try:
                        token_path = Path(channel.oauth_token).expanduser()
                        secrets_path = Path(channel.client_secrets)
                        service = get_authenticated_service(secrets_path, token_path=token_path)
                        uploader = YouTubeUploader(service)

                        # Generate optimized title using learned patterns
                        base_subject = f"Peanut Reacts to {vid_title}"
                        if overrides is not None:
                            from peanut_reacts.learning.title_generator import generate_title
                            title = generate_title(
                                base_subject=base_subject,
                                channel=channel,
                                overrides=overrides,
                                duration_seconds=duration,
                                use_llm=True,
                                llm_provider=llm,
                            )
                        else:
                            title = f"{base_subject} | {channel.character.title()} Reacts"

                        meta = UploadMetadata(
                            title=title[:100],
                            description=f"{channel.character.title()} reacts to {vid_title}\n\n#{'#'.join(channel.tags)}",
                            tags=channel.tags[:15],
                            category_id="20",
                            privacy_status=channel.upload_privacy,
                        )
                        result = uploader.upload_video(final_path, meta)
                        db.complete_job(job_id, str(final_path), result.url)
                        log.info("[%s] Uploaded: %s — title: %s", channel.id, result.url, title[:80])

                        # Record the experiment
                        if applied_recs:
                            try:
                                from peanut_reacts.learning.experiments import record_experiment
                                note_list = overrides.notes if overrides else []
                                record_experiment(
                                    db_path="learning.db",
                                    video_id=result.video_id,
                                    channel_id=channel.id,
                                    applied_recommendations=applied_recs,
                                    overrides_notes=note_list,
                                )
                                # Mark recommendations as applied
                                from peanut_reacts.learning.applicator import mark_applied
                                rec_ids = [r["id"] for r in applied_recs if r.get("id")]
                                if rec_ids:
                                    mark_applied("learning.db", rec_ids)
                            except Exception as e:
                                log.warning("Experiment recording failed: %s", e)

                        videos_done += 1
                    except Exception as e:
                        db.fail_job(job_id, str(e), "upload")
                        errors += 1
                else:
                    db.complete_job(job_id, str(final_path))
                    videos_done += 1

            except Exception as e:
                log.error("[%s] Pipeline failed for %s: %s", channel.id, vid_id, e)
                db.fail_job(job_id, str(e))
                errors += 1

    return videos_done, errors


def _build_reaction_audio_and_composite(
    video_path, tts_dir, reactions, output_path,
    gameplay_vol, reaction_boost, encoder, duration,
):
    """Build reaction audio track, dynamic facecam, and composite.

    The final video contains:
    - Source gameplay/reaction at reduced volume
    - Boosted character voice reactions mixed in
    - A DYNAMIC character facecam in the corner that switches clips
      based on each reaction's emotion (happy/shocked/confused/etc.)
    """
    tts_files = sorted(tts_dir.glob("rx_*.mp3"))
    if not tts_files:
        # No reactions - just copy video
        import shutil
        shutil.copy2(video_path, output_path)
        return

    # Build in batches of 50 to avoid command line length limits
    batch_size = 50
    batch_outputs = []

    for batch_start in range(0, len(reactions), batch_size):
        batch_end = min(batch_start + batch_size, len(reactions))
        batch_reactions = reactions[batch_start:batch_end]
        batch_tts = tts_files[batch_start:batch_end]
        batch_out = output_path.parent / f"audio_batch_{batch_start:04d}.aac"

        if batch_out.exists():
            batch_outputs.append(batch_out)
            continue

        inputs = ["-i", str(video_path)]
        for tf in batch_tts:
            inputs.extend(["-i", str(tf)])

        filters = [f"[0:a]volume={gameplay_vol}[bg]"]
        mix_inputs = ["[bg]"]

        for i, rx in enumerate(batch_reactions):
            idx = i + 1
            delay_ms = int(rx.get("start", 0) * 1000)
            filters.append(f"[{idx}:a]volume={reaction_boost},adelay={delay_ms}|{delay_ms}[r{i}]")
            mix_inputs.append(f"[r{i}]")

        filters.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first[out]")

        cmd = (
            ["ffmpeg", "-y"] + inputs +
            ["-filter_complex", ";".join(filters), "-map", "[out]",
             "-c:a", "aac", "-b:a", "192k", "-t", str(duration), str(batch_out)]
        )

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            batch_outputs.append(batch_out)
        except Exception as e:
            log.warning("Audio batch %d failed: %s", batch_start, e)
            time.sleep(1)

    if not batch_outputs:
        import shutil
        shutil.copy2(video_path, output_path)
        return

    # Mix all batches
    if len(batch_outputs) == 1:
        mixed_audio = batch_outputs[0]
    else:
        mixed_audio = output_path.parent / "mixed_audio.aac"
        if not mixed_audio.exists():
            inputs = []
            for bo in batch_outputs:
                inputs.extend(["-i", str(bo)])
            filter_str = f"{''.join(f'[{i}:a]' for i in range(len(batch_outputs)))}amix=inputs={len(batch_outputs)}:duration=longest[out]"
            subprocess.run(
                ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_str,
                 "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(mixed_audio)],
                check=True, capture_output=True, timeout=600,
            )

    # Final composite: source video + dynamic facecam overlay + mixed audio
    try:
        from peanut_reacts.character.dynamic_facecam import build_and_composite
        result = build_and_composite(
            source_video=video_path,
            audio_track=mixed_audio,
            reactions=reactions,
            output_path=output_path,
            video_duration=duration,
            facecam_size=360,
            position="bottom-right",
        )
        if result is None:
            raise RuntimeError("Dynamic facecam returned None, falling back to simple composite")
        log.info("Composite with dynamic facecam: %s", output_path.name)
    except Exception as e:
        log.warning("Dynamic facecam failed (%s), falling back to plain composite", e)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path), "-i", str(mixed_audio),
            "-c:v", encoder, "-c:a", "aac",
            "-map", "0:v", "-map", "1:a",
            "-shortest", str(output_path),
        ], check=True, capture_output=True, timeout=3600)


# ═══════════════════════════════════════════════════════════════
# TWITCH VOD PIPELINE (HasanAbi, etc.)
# ═══════════════════════════════════════════════════════════════

def run_twitch_pipeline(
    channel: ChannelConfig,
    settings: GlobalSettings,
    db: PipelineDB,
) -> tuple[int, int]:
    """Download Twitch VOD, segment by topic, upload clips."""
    from peanut_reacts.character.reaction_generator import create_llm_provider, LLMConfig
    from peanut_reacts.core.config import load_config

    videos_done, errors = 0, 0
    out_dir = PROJECT_ROOT / settings.output_dir / channel.id
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    llm = create_llm_provider(LLMConfig(
        provider=settings.llm_provider,
        api_key=cfg.deepseek_api_key,
        model=settings.llm_model,
        temperature=0.3,
    ))

    streamer = channel.source_urls[0] if channel.source_urls else channel.id

    # Download latest VOD using yt-dlp (supports Twitch)
    vod_dir = out_dir / "vods"
    vod_dir.mkdir(exist_ok=True)

    try:
        # Get latest VOD URL
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--print", "%(id)s %(title)s",
             f"https://www.twitch.tv/{streamer}/videos?filter=archives&sort=time"],
            capture_output=True, text=True, timeout=60,
        )
        vod_lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if not vod_lines:
            log.warning("[%s] No VODs found for %s", channel.id, streamer)
            return 0, 0

        existing = {j["video_id"] for j in db.get_channel_jobs(channel.id, limit=100) if j["status"] == "done"}

        for vod_line in vod_lines[:1]:  # Latest VOD only
            parts = vod_line.split(" ", 1)
            vod_id = parts[0]
            vod_title = parts[1] if len(parts) > 1 else vod_id

            if vod_id in existing:
                log.info("[%s] VOD %s already processed", channel.id, vod_id)
                continue

            vod_path = vod_dir / f"{vod_id}.mp4"
            job_id = db.create_job(channel.id, vod_id, vod_title)

            try:
                # Download VOD
                db.start_job(job_id, "download")
                if not vod_path.exists():
                    subprocess.run([
                        "yt-dlp", "-f", "bestvideo[height<=1080]+bestaudio/best",
                        "--merge-output-format", "mp4",
                        "-o", str(vod_path),
                        f"https://www.twitch.tv/videos/{vod_id}",
                    ], check=True, capture_output=True, timeout=7200)

                # Transcribe with Whisper
                db.update_job(job_id, step="transcribe")
                transcript_path = vod_dir / f"{vod_id}_transcript.json"
                if not transcript_path.exists():
                    from peanut_reacts.core.transcription import extract_transcript
                    segments = extract_transcript(vod_path, model_name="base")
                    transcript_path.write_text(json.dumps(segments, indent=2))

                transcript = json.loads(transcript_path.read_text())

                # Segment by topic using LLM
                db.update_job(job_id, step="segment")
                segments_list = _segment_by_topic(transcript, llm, channel)

                # Cut and upload each segment
                clips_done = 0
                for seg in segments_list[:channel.max_segments_per_run]:
                    try:
                        clip_path = out_dir / f"{vod_id}_seg_{seg['index']:02d}.mp4"
                        if not clip_path.exists():
                            subprocess.run([
                                "ffmpeg", "-y", "-i", str(vod_path),
                                "-ss", str(seg["start"]), "-to", str(seg["end"]),
                                "-c:v", _get_encoder(settings), "-c:a", "aac",
                                str(clip_path),
                            ], check=True, capture_output=True, timeout=600)
                        clips_done += 1
                    except Exception as e:
                        log.warning("[%s] Segment %d failed: %s", channel.id, seg["index"], e)
                        errors += 1

                db.complete_job(job_id, str(out_dir))
                videos_done += clips_done

            except Exception as e:
                log.error("[%s] Twitch pipeline failed: %s", channel.id, e)
                db.fail_job(job_id, str(e))
                errors += 1

    except Exception as e:
        log.error("[%s] Failed to list VODs: %s", channel.id, e)
        errors += 1

    return videos_done, errors


def _segment_by_topic(transcript, llm, channel) -> list[dict]:
    """Use LLM to detect topic changes in transcript."""
    segments = []
    chunk_minutes = 5
    chunk_size = chunk_minutes * 60

    current_topic = ""
    seg_start = 0
    seg_index = 0

    for chunk_start in range(0, int(transcript[-1].get("end", 0)) if transcript else 0, chunk_size):
        chunk_end = chunk_start + chunk_size
        chunk_text = " ".join(
            s.get("text", "") for s in transcript
            if chunk_start <= s.get("start", 0) < chunk_end
        )[:1000]

        if not chunk_text.strip():
            continue

        try:
            response = llm.complete([
                {"role": "user", "content": (
                    f"What is the main topic of this transcript segment? "
                    f"Reply with ONLY a short topic label (3-8 words):\n\n\"{chunk_text}\""
                )}
            ], temperature=0.2, max_tokens=50)
            topic = response.strip().strip('"').strip("'")
        except Exception:
            topic = current_topic or "Unknown"

        if topic != current_topic and current_topic:
            duration = chunk_start - seg_start
            if duration >= channel.min_segment_minutes * 60:
                segments.append({
                    "index": seg_index,
                    "topic": current_topic,
                    "start": seg_start,
                    "end": chunk_start,
                    "duration": duration,
                })
                seg_index += 1
            seg_start = chunk_start

        current_topic = topic

    # Final segment
    if current_topic and transcript:
        end_time = transcript[-1].get("end", seg_start + 60)
        if end_time - seg_start >= channel.min_segment_minutes * 60:
            segments.append({
                "index": seg_index,
                "topic": current_topic,
                "start": seg_start,
                "end": end_time,
                "duration": end_time - seg_start,
            })

    # Enforce max segment length
    trimmed = []
    for seg in segments:
        if seg["duration"] > channel.max_segment_minutes * 60:
            seg["end"] = seg["start"] + channel.max_segment_minutes * 60
            seg["duration"] = channel.max_segment_minutes * 60
        trimmed.append(seg)

    log.info("[HasanAbi] Found %d topic segments", len(trimmed))
    return trimmed


# ═══════════════════════════════════════════════════════════════
# LOFI PIPELINE
# ═══════════════════════════════════════════════════════════════

def run_lofi_pipeline(
    channel: ChannelConfig,
    settings: GlobalSettings,
    db: PipelineDB,
) -> tuple[int, int]:
    """Generate AI lofi tracks, compile mix, upload."""
    out_dir = PROJECT_ROOT / settings.output_dir / channel.id
    out_dir.mkdir(parents=True, exist_ok=True)

    job_id = db.create_job(channel.id, "", f"Lofi Mix {time.strftime('%Y-%m-%d')}")
    db.start_job(job_id, "generate")

    # For now: compile existing tracks into a mix
    # TODO: integrate Suno API for AI music generation
    tracks_dir = out_dir / "tracks"
    tracks_dir.mkdir(exist_ok=True)

    tracks = sorted(tracks_dir.glob("*.mp3")) + sorted(tracks_dir.glob("*.wav"))
    if len(tracks) < 3:
        log.warning("[%s] Not enough tracks (%d). Add tracks to %s", channel.id, len(tracks), tracks_dir)
        db.fail_job(job_id, f"Not enough tracks ({len(tracks)}). Need at least 3.", "generate")
        return 0, 1

    # Compile mix with crossfades
    db.update_job(job_id, step="compile")
    mix_path = out_dir / f"lofi_mix_{time.strftime('%Y%m%d')}.mp4"

    if not mix_path.exists():
        # Simple concatenation with a static background image
        bg_image = out_dir / "background.png"
        if not bg_image.exists():
            # Generate a simple gradient background
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=#1a1a2e:s=1920x1080:d=1",
                "-frames:v", "1", str(bg_image),
            ], capture_output=True)

        # Concat audio tracks
        concat_list = out_dir / "concat.txt"
        concat_list.write_text("\n".join(f"file '{t}'" for t in tracks[:channel.tracks_per_mix]))

        concat_audio = out_dir / "concat_audio.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list), "-c:a", "libmp3lame", str(concat_audio),
        ], capture_output=True, timeout=300)

        if concat_audio.exists():
            subprocess.run([
                "ffmpeg", "-y",
                "-loop", "1", "-i", str(bg_image),
                "-i", str(concat_audio),
                "-c:v", _get_encoder(settings), "-c:a", "aac",
                "-shortest", "-pix_fmt", "yuv420p",
                str(mix_path),
            ], capture_output=True, timeout=600)

    if mix_path.exists():
        db.complete_job(job_id, str(mix_path))
        return 1, 0
    else:
        db.fail_job(job_id, "Mix compilation failed", "compile")
        return 0, 1


# ═══════════════════════════════════════════════════════════════
# REDDIT STORIES PIPELINE
# ═══════════════════════════════════════════════════════════════

def run_reddit_pipeline(
    channel: ChannelConfig,
    settings: GlobalSettings,
    db: PipelineDB,
) -> tuple[int, int]:
    """Scrape Reddit stories, narrate with Edge TTS, composite, upload.

    Reads `subreddits`, `min_score`, `stories_per_video`, `character`,
    `tts_voice`, `tts_rate`, `tags`, `upload_privacy` from the channel
    config. Delegates the heavy lifting to
    `peanut_reacts.scheduler.reddit_pipeline.run_reddit_pipeline`.
    """
    from peanut_reacts.character.reaction_generator import create_llm_provider, LLMConfig
    from peanut_reacts.core.config import load_config
    from peanut_reacts.scheduler.reddit_pipeline import (
        run_reddit_pipeline as run_reddit_engine,
    )

    out_dir = PROJECT_ROOT / settings.output_dir / channel.id
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    llm = create_llm_provider(LLMConfig(
        provider=settings.llm_provider,
        api_key=cfg.deepseek_api_key,
        model=settings.llm_model,
        temperature=0.8,
    ))

    if not channel.subreddits:
        log.error("[%s] No subreddits configured", channel.id)
        return 0, 1

    log.info("[%s] Reddit pipeline — subs=%s stories=%d min_score=%d",
             channel.id, channel.subreddits,
             channel.stories_per_video, channel.min_score)

    job_id = db.create_job(channel.id, "", f"Reddit Stories {time.strftime('%Y-%m-%d')}")
    db.start_job(job_id, "scrape")

    # Resolve YouTube upload service if creds exist
    upload_service = None
    if channel.oauth_token and channel.client_secrets:
        try:
            from peanut_reacts.upload.youtube_auth import get_authenticated_service
            token_path = Path(channel.oauth_token).expanduser()
            secrets_path = Path(channel.client_secrets).expanduser()
            if token_path.exists() and secrets_path.exists():
                upload_service = get_authenticated_service(secrets_path, token_path=token_path)
                log.info("[%s] YouTube auth OK — uploads enabled", channel.id)
            else:
                log.warning(
                    "[%s] OAuth files missing (token=%s exists=%s, secrets=%s exists=%s) — "
                    "will render without uploading",
                    channel.id, token_path, token_path.exists(),
                    secrets_path, secrets_path.exists(),
                )
        except Exception as e:
            log.warning("[%s] YouTube auth failed: %s — will render without uploading",
                        channel.id, e)

    try:
        result = run_reddit_engine(
            output_dir=out_dir,
            llm_provider=llm,
            subreddits=channel.subreddits,
            stories_per_video=channel.stories_per_video,
            min_score=channel.min_score,
            voice=channel.tts_voice,
            rate=channel.tts_rate,
            character=channel.character.title() if channel.character else "Narrator",
            upload_service=upload_service,
            upload_privacy=channel.upload_privacy,
            tags=channel.tags,
        )

        if result.get("errors"):
            log.warning("[%s] Reddit pipeline partial errors: %s",
                        channel.id, result["errors"])

        if result.get("video_path"):
            db.complete_job(job_id, str(result["video_path"]))
            return 1, len(result.get("errors", []))
        else:
            db.fail_job(job_id, "; ".join(result.get("errors", ["unknown"])), "render")
            return 0, 1

    except Exception as e:
        log.exception("[%s] Reddit pipeline crashed", channel.id)
        db.fail_job(job_id, str(e)[:500], "crash")
        return 0, 1


# ═══════════════════════════════════════════════════════════════
# DISPATCHER
# ═══════════════════════════════════════════════════════════════

def run_ambient_pipeline(
    channel: ChannelConfig,
    settings: GlobalSettings,
    db: PipelineDB,
) -> tuple[int, int]:
    """Run the cozy ambient pipeline (Pixabay CC0 + Suno + PD classical).

    Picks one theme from the channel's themes list and produces a single
    long-form ambient video.
    """
    from peanut_reacts.ambient.pipeline import run_ambient_pipeline as run_ap, AmbientJob
    from pathlib import Path
    import random
    import os

    themes = channel.themes or ["rainy_cafe"]
    theme = random.choice(themes)
    log.info("[%s] Producing ambient video for theme: %s", channel.id, theme)

    # YouTube auth
    youtube_service = None
    if channel.client_secrets and channel.oauth_token:
        try:
            from peanut_reacts.upload.youtube_auth import get_authenticated_service
            token_path = Path(channel.oauth_token).expanduser()
            secrets_path = Path(channel.client_secrets)
            if token_path.exists() and secrets_path.exists():
                youtube_service = get_authenticated_service(secrets_path, token_path=token_path)
        except Exception as e:
            log.warning("[%s] YouTube auth failed: %s", channel.id, e)

    job_id = db.create_job(channel.id, "", f"Ambient {theme}")
    db.start_job(job_id, "compile")

    try:
        result = run_ap(
            job=AmbientJob(
                theme=theme,
                target_hours=channel.target_hours,
                tags=channel.tags,
                use_ai_visuals=channel.use_ai_visuals,
            ),
            output_dir=PROJECT_ROOT / settings.output_dir / channel.id,
            upload=youtube_service is not None,
            youtube_service=youtube_service,
            privacy=channel.upload_privacy,
            fal_api_key=os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY", ""),
        )

        if result.get("youtube_url"):
            db.complete_job(job_id, result.get("final_path", ""), result["youtube_url"])
            return 1, 0
        elif result.get("final_path"):
            db.complete_job(job_id, result["final_path"])
            return 1, 0
        else:
            errs = "; ".join(result.get("errors", [])) or "Unknown failure"
            db.fail_job(job_id, errs)
            return 0, 1
    except Exception as e:
        log.error("[%s] Ambient pipeline crashed: %s", channel.id, e)
        db.fail_job(job_id, str(e))
        return 0, 1


def run_hasan_archive_pipeline(
    channel: ChannelConfig,
    settings: GlobalSettings,
    db: PipelineDB,
) -> tuple[int, int]:
    """Run the HasanAbi archive pipeline (download from fan archive → segment → upload)."""
    from peanut_reacts.scheduler.hasan_pipeline import run_hasan_pipeline
    return run_hasan_pipeline(channel, settings, db, max_segments=10, extract_shorts=True)


def run_tntl_channel_pipeline(
    channel: ChannelConfig,
    settings: GlobalSettings,
    db: PipelineDB,
) -> tuple[int, int]:
    """Run the Try Not To Laugh pipeline (hybrid KSI+ format / Richard voice).

    Sources funny clips → builds Peanut-PIP episode with verdict TTS in the
    last ~2s of each clip → uploads to the channel's YouTube. Returns
    (uploaded, errors).
    """
    from peanut_reacts.scheduler.tntl_pipeline import run_tntl_pipeline

    # Resolve YouTube auth — same pattern as the other pipelines, with
    # .expanduser() on both paths (class-bug-of-3 we've patched elsewhere).
    youtube_service = None
    if channel.client_secrets and channel.oauth_token:
        try:
            from peanut_reacts.upload.youtube_auth import get_authenticated_service
            token_path = Path(channel.oauth_token).expanduser()
            secrets_path = Path(channel.client_secrets).expanduser()
            if token_path.exists() and secrets_path.exists():
                youtube_service = get_authenticated_service(
                    secrets_path,
                    token_path=token_path,
                    interactive=False,
                )
                log.info("[%s] YouTube auth OK (TNTL)", channel.id)
            else:
                log.warning("[%s] TNTL auth paths missing: token=%s(%s) secrets=%s(%s)",
                            channel.id, token_path, token_path.exists(),
                            secrets_path, secrets_path.exists())
        except Exception as e:
            log.warning("[%s] YouTube auth failed (TNTL): %s", channel.id, e)

    job_id = db.create_job(channel.id, "", "TNTL episode")
    db.start_job(job_id, "compile")

    try:
        result = run_tntl_pipeline(
            output_dir=PROJECT_ROOT / settings.output_dir / channel.id,
            num_clips=18,
            character=(channel.character or "Peanut").capitalize(),
            voice=channel.tts_voice or "en-GB-RyanNeural",
            upload_service=youtube_service,
            upload_privacy=channel.upload_privacy,
            tags=channel.tags,
        )

        if result.get("youtube_url"):
            db.complete_job(job_id, result.get("video_path", ""), result["youtube_url"])
            return 1, 0
        elif result.get("video_path"):
            db.complete_job(job_id, result["video_path"])
            return 1, 0
        else:
            errs = "; ".join(result.get("errors", [])) or "Unknown TNTL failure"
            db.fail_job(job_id, errs)
            return 0, 1
    except Exception as e:
        log.error("[%s] TNTL pipeline crashed: %s", channel.id, e)
        db.fail_job(job_id, str(e))
        return 0, 1


PIPELINE_MAP = {
    "playlist": run_reaction_pipeline,
    "twitch_vod": run_twitch_pipeline,
    "hasan_archive": run_hasan_archive_pipeline,
    "lofi": run_lofi_pipeline,
    "reddit": run_reddit_pipeline,
    "ambient": run_ambient_pipeline,
    "tntl": run_tntl_channel_pipeline,
}


def run_channel_pipeline(
    channel: ChannelConfig,
    settings: GlobalSettings,
    db: PipelineDB,
    overrides=None,
    applied_recs: Optional[list] = None,
) -> tuple[int, int]:
    """Dispatch to the correct pipeline based on channel source_type.

    If overrides/applied_recs are provided, they are passed to pipelines that
    support them (currently run_reaction_pipeline).
    """
    pipeline_fn = PIPELINE_MAP.get(channel.source_type)
    if pipeline_fn is None:
        log.error("Unknown source_type '%s' for channel '%s'", channel.source_type, channel.id)
        return 0, 1

    log.info("=" * 60)
    log.info("Starting pipeline for [%s] (%s)", channel.name, channel.source_type)
    log.info("=" * 60)

    run_id = db.start_run(channel.id)
    try:
        # Only run_reaction_pipeline currently accepts overrides
        if pipeline_fn is run_reaction_pipeline:
            videos, errs = pipeline_fn(channel, settings, db, overrides=overrides, applied_recs=applied_recs)
        else:
            videos, errs = pipeline_fn(channel, settings, db)
        db.finish_run(run_id, videos, errs)
        db.update_channel_stats(
            channel.id,
            last_run_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            total_uploads=f"total_uploads + {videos}",
        )
        log.info("[%s] Pipeline complete: %d videos, %d errors", channel.id, videos, errs)
        return videos, errs
    except Exception as e:
        log.error("[%s] Pipeline crashed: %s", channel.id, e)
        db.finish_run(run_id, 0, 1)
        return 0, 1
