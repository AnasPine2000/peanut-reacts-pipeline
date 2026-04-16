"""Build an 8-hour Sidemen Among Us mega compilation with DYNAMIC peanut reactions.

This is the new production script that:
1. Picks videos from the existing AMONGUS library
2. Concatenates them into a single ~8 hour source
3. Generates dense reactions with emotion labels
4. Runs TTS on all reactions
5. Builds batched reaction audio
6. Renders dynamic facecam (emotion-driven clip switching)
7. Composites final video with character overlay
8. Uploads with the learned title pattern (ALL CAPS, HOURS, year)
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

# Ensure src is importable
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import io
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / "logs" / "mega_8hr.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("mega_8hr")

# ── Config ──
AMONGUS_DIR = PROJECT_ROOT / "downloads" / "AMONGUS"
OUTPUT_DIR = PROJECT_ROOT / "production" / "mega_8hr_dynamic"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(PROJECT_ROOT / "logs").mkdir(exist_ok=True)

TARGET_HOURS = 8.0
REACTIONS_PER_MIN = 6
TTS_VOICE = "en-GB-RyanNeural"
TTS_RATE = "+15%"
GAMEPLAY_VOLUME = 0.12
REACTION_BOOST = 5.0
FACECAM_SIZE = 360

CLIPS_DIR = PROJECT_ROOT / "data" / "ai_peanut_clips"


def nvenc_available() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10)
        return "h264_nvenc" in r.stdout
    except Exception:
        return False


ENCODER = "h264_nvenc" if nvenc_available() else "libx264"
PRESET = "fast" if ENCODER == "h264_nvenc" else "veryfast"
log.info("Using encoder: %s", ENCODER)


def pick_source_videos() -> list[Path]:
    """Pick Sidemen Among Us videos summing to ~TARGET_HOURS.

    Prefer .webm (higher quality) and skip files already in use (MKV series).
    """
    all_webm = sorted(AMONGUS_DIR.glob("*.webm"),
                     key=lambda p: p.stat().st_size, reverse=True)
    log.info("Found %d .webm candidates", len(all_webm))

    # Also allow .mkv if needed
    mkvs = sorted(AMONGUS_DIR.glob("*.mkv"),
                 key=lambda p: p.stat().st_size, reverse=True)

    selected: list[Path] = []
    total_seconds = 0
    target = TARGET_HOURS * 3600

    for candidate in all_webm + mkvs:
        if total_seconds >= target:
            break
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(candidate)],
                capture_output=True, text=True, timeout=10,
            )
            duration = float(r.stdout.strip())
        except Exception:
            continue
        selected.append(candidate)
        total_seconds += duration
        log.info("  Selected: [%d] %.0f min — %s", len(selected), duration / 60, candidate.name[:70])

    log.info("Total selected: %d videos, %.1f hours", len(selected), total_seconds / 3600)
    return selected


def convert_and_concatenate(sources: list[Path], output_path: Path) -> tuple[Path, float]:
    """Convert each source to uniform mp4 then concatenate. Returns (path, duration)."""
    if output_path.exists():
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(output_path)],
            capture_output=True, text=True, timeout=10,
        )
        dur = float(r.stdout.strip())
        log.info("Concatenated source already exists: %.1f hours", dur / 3600)
        return output_path, dur

    tmp_dir = output_path.parent / "_tmp_episodes"
    tmp_dir.mkdir(exist_ok=True)

    # Step 1: Convert each source to uniform format
    converted_files: list[Path] = []
    for i, src in enumerate(sources):
        ep_path = tmp_dir / f"ep_{i:02d}.mp4"
        if not ep_path.exists():
            log.info("Converting [%d/%d]: %s...", i + 1, len(sources), src.name[:60])
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(src),
                    "-c:v", ENCODER, "-preset", PRESET,
                    "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1:1,fps=30",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                    "-pix_fmt", "yuv420p",
                    str(ep_path),
                ], check=True, capture_output=True, timeout=1800)
            except subprocess.CalledProcessError as e:
                log.warning("Conversion failed for %s: %s", src.name, e.stderr.decode("utf-8", errors="replace")[-200:])
                continue

        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(ep_path)],
            capture_output=True, text=True, timeout=10,
        )
        log.info("  ep_%02d.mp4: %.1f min", i, float(r.stdout.strip()) / 60)
        converted_files.append(ep_path)

    # Step 2: Concatenate
    log.info("Concatenating %d episodes...", len(converted_files))
    list_file = tmp_dir / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in converted_files),
        encoding="utf-8",
    )

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path),
    ], check=True, capture_output=True, timeout=3600)

    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(output_path)],
        capture_output=True, text=True, timeout=10,
    )
    dur = float(r.stdout.strip())
    log.info("Concatenated: %s (%.1f hours)", output_path.name, dur / 3600)
    return output_path, dur


def extract_transcript(video_path: Path) -> list[dict]:
    """Extract embedded/auto subtitles from the video or fall back to YT download."""
    # Check if we already have a cached transcript
    tpath = video_path.with_suffix(".transcript.json")
    if tpath.exists():
        return json.loads(tpath.read_text(encoding="utf-8"))

    # Try to extract embedded subs via ffmpeg
    srt_path = video_path.with_suffix(".srt")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-map", "0:s:0", str(srt_path)],
            capture_output=True, timeout=60,
        )
    except Exception:
        pass

    if not srt_path.exists() or srt_path.stat().st_size == 0:
        log.warning("No embedded subs. Transcribing with Whisper might be too slow for 8h — using empty transcript.")
        return []

    # Parse SRT
    from peanut_reacts.core.srt import parse_srt
    segs = parse_srt(srt_path)
    tpath.write_text(json.dumps(segs, default=str), encoding="utf-8")
    return segs


def extract_transcript_from_srts(sources: list[Path], total_duration: float) -> list[dict]:
    """Build a single combined transcript from each source video's .srt.

    Since we concatenated the videos, we need to shift each source's timestamps
    by the cumulative duration of the preceding videos.
    """
    from peanut_reacts.core.srt import parse_srt

    combined: list[dict] = []
    cumulative = 0.0

    for src in sources:
        # Look for matching .srt in same dir
        srt = src.with_suffix(".en.srt")
        if not srt.exists():
            srt = src.with_suffix(".srt")
        if not srt.exists():
            log.warning("No SRT for %s, skipping transcript for this segment", src.name[:40])
            # Still advance cumulative using probe
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(src)],
                capture_output=True, text=True, timeout=10,
            )
            cumulative += float(r.stdout.strip()) if r.stdout.strip() else 0
            continue

        segs = parse_srt(srt)
        # Get the source video duration
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(src)],
            capture_output=True, text=True, timeout=10,
        )
        dur = float(r.stdout.strip()) if r.stdout.strip() else 0

        for s in segs:
            shifted = dict(s)
            shifted["start"] = s.get("start", 0) + cumulative
            shifted["end"] = s.get("end", 0) + cumulative
            combined.append(shifted)

        cumulative += dur
        log.info("  +%d segs from %s", len(segs), src.name[:50])

    log.info("Combined transcript: %d segments across %.1f hours", len(combined), cumulative / 3600)
    return combined


def generate_reactions(transcript: list[dict], duration: float) -> list[dict]:
    """Generate dense reactions via DeepSeek in 60-second chunks."""
    cache_path = OUTPUT_DIR / "reactions.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        log.info("Reactions cached: %d", len(cached))
        return cached

    from peanut_reacts.character.reaction_generator import create_llm_provider, LLMConfig
    from peanut_reacts.core.config import load_config

    cfg = load_config()
    llm = create_llm_provider(LLMConfig(
        provider="deepseek",
        api_key=cfg.deepseek_api_key,
        temperature=0.85,
        max_tokens=900,
    ))

    reactions = []
    chunk_size = 60  # seconds per LLM call
    total_chunks = int(duration / chunk_size) + 1

    for chunk_start in range(0, int(duration), chunk_size):
        chunk_end = min(chunk_start + chunk_size, int(duration))
        chunk_segs = [s for s in transcript if chunk_start <= s.get("start", 0) < chunk_end]
        if not chunk_segs:
            continue

        text = " ".join(s.get("text", "") for s in chunk_segs)[:600]
        target = REACTIONS_PER_MIN  # ~6 reactions per minute

        prompt = (
            "You are Peanut, an excited animated peanut fan watching Sidemen Among Us. "
            "React with hype, shock, humor, and hot takes. Keep reactions SHORT (1-5 words). "
            f"Segment ({chunk_start}s-{chunk_end}s): \"{text}\"\n\n"
            f"Return ONLY a JSON array with exactly {target} reactions: "
            '[{"start": seconds_from_chunk_start, "text": "...", "emotion": "shocked|laughing|excited|confused|scared|sarcastic|celebrating|amused|mind_blown", "intensity": 1.0-3.0}]'
        )

        try:
            response = llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.85, max_tokens=700,
            )
            text_out = response.strip()
            if "```json" in text_out:
                text_out = text_out.split("```json")[1].split("```")[0]
            elif "```" in text_out:
                text_out = text_out.split("```")[1].split("```")[0]

            chunk_rxs = json.loads(text_out)
            for r in chunk_rxs:
                r["start"] = float(r.get("start", 0)) + chunk_start
            reactions.extend(chunk_rxs)

            if chunk_start % 600 == 0:
                log.info("  Reactions at %ds: %d total", chunk_start, len(reactions))
        except Exception as e:
            log.warning("Chunk %d failed: %s", chunk_start, str(e)[:80])

    cache_path.write_text(json.dumps(reactions, indent=2), encoding="utf-8")
    log.info("Generated %d reactions (%.1f per minute)", len(reactions), len(reactions) / (duration / 60))
    return reactions


def run_tts(reactions: list[dict]) -> Path:
    """Run TTS for every reaction. Returns the tts directory."""
    tts_dir = OUTPUT_DIR / "tts"
    tts_dir.mkdir(exist_ok=True)

    from peanut_reacts.character.tts import EdgeTTSEngine, TTSConfig
    tts = EdgeTTSEngine(TTSConfig(voice=TTS_VOICE, rate=TTS_RATE))

    pending = []
    for i, rx in enumerate(reactions):
        path = tts_dir / f"rx_{i:04d}.mp3"
        if not path.exists():
            pending.append((i, rx, path))

    log.info("TTS: %d pending (of %d total)", len(pending), len(reactions))

    for idx, (i, rx, path) in enumerate(pending):
        try:
            asyncio.run(tts.synthesize(rx["text"], path))
        except Exception as e:
            log.warning("TTS %d failed: %s", i, str(e)[:80])

        if (idx + 1) % 200 == 0:
            log.info("  TTS: %d/%d", idx + 1, len(pending))

    return tts_dir


def build_reaction_audio(
    source_video: Path,
    tts_dir: Path,
    reactions: list[dict],
    output: Path,
    duration: float,
):
    """Build reaction audio track via batched FFmpeg mixing."""
    if output.exists():
        log.info("Audio already built: %s", output.name)
        return output

    tts_files = sorted(tts_dir.glob("rx_*.mp3"))
    log.info("Building audio with %d reactions...", len(tts_files))

    # Batch size of 50 to avoid command-line length limits
    batch_size = 50
    batch_dir = OUTPUT_DIR / "audio_batches"
    batch_dir.mkdir(exist_ok=True)

    batch_files: list[Path] = []
    for batch_start in range(0, len(reactions), batch_size):
        batch_end = min(batch_start + batch_size, len(reactions))
        b_rxs = reactions[batch_start:batch_end]
        b_tts = tts_files[batch_start:batch_end]
        b_out = batch_dir / f"batch_{batch_start:04d}.aac"

        if b_out.exists():
            batch_files.append(b_out)
            continue

        inputs = ["-i", str(source_video)]
        for t in b_tts:
            inputs.extend(["-i", str(t)])

        filters = [f"[0:a]volume={GAMEPLAY_VOLUME}[bg]"]
        mix_inputs = ["[bg]"]
        for i, rx in enumerate(b_rxs):
            idx = i + 1
            delay_ms = int(rx.get("start", 0) * 1000)
            filters.append(f"[{idx}:a]volume={REACTION_BOOST},adelay={delay_ms}|{delay_ms}[r{i}]")
            mix_inputs.append(f"[r{i}]")
        filters.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first[out]")

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", ";".join(filters), "-map", "[out]",
            "-c:a", "aac", "-b:a", "192k", "-t", str(duration), str(b_out),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=900)
            batch_files.append(b_out)
            log.info("  Audio batch %d-%d: done", batch_start, batch_end)
        except Exception as e:
            log.warning("Batch %d failed: %s", batch_start, str(e)[:100])
            time.sleep(1)

    # Mix all batches
    log.info("Mixing %d audio batches...", len(batch_files))
    if len(batch_files) == 1:
        import shutil
        shutil.copy2(batch_files[0], output)
    else:
        inputs = []
        for bf in batch_files:
            inputs.extend(["-i", str(bf)])
        fstr = f"{''.join(f'[{i}:a]' for i in range(len(batch_files)))}amix=inputs={len(batch_files)}:duration=longest[out]"
        subprocess.run(
            ["ffmpeg", "-y"] + inputs + ["-filter_complex", fstr,
             "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(output)],
            check=True, capture_output=True, timeout=1800,
        )
    log.info("Audio built: %s", output.name)
    return output


def build_facecam(reactions: list[dict], duration: float) -> Path:
    """Render the dynamic facecam track."""
    facecam_path = OUTPUT_DIR / "facecam.mp4"
    if facecam_path.exists():
        log.info("Facecam already exists: %s", facecam_path.name)
        return facecam_path

    from peanut_reacts.character.dynamic_facecam import (
        build_facecam_timeline, render_dynamic_facecam,
    )

    timeline = build_facecam_timeline(reactions, duration, clips_dir=CLIPS_DIR)
    log.info("Facecam timeline: %d segments", len(timeline))

    result = render_dynamic_facecam(timeline, facecam_path, size=FACECAM_SIZE)
    if not result:
        raise RuntimeError("Facecam render failed")
    return result


def composite_final(source_video: Path, facecam: Path, audio: Path, output: Path):
    """Final composite with chroma-keyed facecam overlay."""
    from peanut_reacts.character.dynamic_facecam import composite_with_facecam
    return composite_with_facecam(
        source_video=source_video,
        facecam_track=facecam,
        audio_track=audio,
        output_path=output,
        position="bottom-right",
        facecam_size=FACECAM_SIZE,
        use_chromakey=True,
        encoder=ENCODER,
    )


def upload_to_youtube(video_path: Path, duration: float, reaction_count: int):
    """Upload with optimized title following learned patterns."""
    import sys, io
    from peanut_reacts.upload.youtube_auth import get_authenticated_service
    from peanut_reacts.upload.youtube_upload import YouTubeUploader, UploadMetadata

    hours = round(duration / 3600)
    client_secrets = Path(
        r"C:\Users\anasm\Downloads\client_secret_914737972580-9pd0j8k5601tk4p4mmq4e5jvnevonppa.apps.googleusercontent.com.json"
    )

    service = get_authenticated_service(client_secrets)
    uploader = YouTubeUploader(service)

    # Learned title pattern: ALL CAPS + HOURS + year + no hype word needed
    title = f"{hours} HOURS OF SIDEMEN AMONG US 2026 | DYNAMIC PEANUT REACTS (NO ADS)"

    description = (
        f"{hours} HOURS of Sidemen Among Us with Peanut reacting LIVE to every moment! "
        f"Dynamic character animation that switches emotions in real-time — "
        f"{reaction_count} reactions across {int(duration/60)} minutes of gameplay.\n\n"
        "Peanut Reacts is an AI-generated animated reaction character.\n"
        "Original content by the Sidemen.\n\n"
        "#sidemen #amongus #peanutreacts #reaction #sidemenamongus #compilation "
        "#gaming #2026 #8hours #noads #megacompilation"
    )

    # Learned recommendation: exactly 4 tags
    tags = ["sidemen", "among us", "peanut reacts", "sidemen among us"]

    meta = UploadMetadata(
        title=title[:100],
        description=description,
        tags=tags,
        category_id="20",  # Gaming
        privacy_status="public",
    )

    log.info("Uploading %.2f GB video...", video_path.stat().st_size / (1024**3))
    log.info("Title: %s", title)
    log.info("Tags: %s", tags)

    result = uploader.upload_video(video_path, meta)
    log.info("UPLOAD COMPLETE: %s", result.url)
    return result


def main():
    log.info("=" * 60)
    log.info("8-HOUR SIDEMEN AMONG US MEGA COMPILATION (DYNAMIC PEANUT)")
    log.info("=" * 60)

    # Step 1: Pick source videos
    log.info("[Step 1/8] Picking source videos...")
    sources = pick_source_videos()
    if not sources:
        log.error("No source videos found")
        return 1

    # Step 2: Convert + concatenate
    log.info("[Step 2/8] Converting and concatenating...")
    source_video, duration = convert_and_concatenate(
        sources, OUTPUT_DIR / "source_concat.mp4",
    )

    # Step 3: Build combined transcript from individual srt files
    log.info("[Step 3/8] Building combined transcript from SRTs...")
    transcript = extract_transcript_from_srts(sources, duration)
    if not transcript:
        log.error("No transcript — can't generate reactions")
        return 1

    # Step 4: Generate reactions
    log.info("[Step 4/8] Generating dense reactions...")
    reactions = generate_reactions(transcript, duration)

    # Step 5: TTS
    log.info("[Step 5/8] Running TTS on %d reactions...", len(reactions))
    tts_dir = run_tts(reactions)

    # Step 6: Build reaction audio
    log.info("[Step 6/8] Building batched reaction audio...")
    audio_path = build_reaction_audio(
        source_video, tts_dir, reactions,
        OUTPUT_DIR / "reaction_audio.aac", duration,
    )

    # Step 7: Build dynamic facecam
    log.info("[Step 7/8] Rendering dynamic peanut facecam...")
    facecam_path = build_facecam(reactions, duration)

    # Step 8: Final composite
    log.info("[Step 8/8] Final composite...")
    final_path = OUTPUT_DIR / "MEGA_8HR_DYNAMIC_FINAL.mp4"
    if not final_path.exists():
        result = composite_final(source_video, facecam_path, audio_path, final_path)
        if not result or not result.exists():
            log.error("Composite failed")
            return 1

    log.info("=" * 60)
    log.info("BUILD DONE: %s", final_path)
    log.info("Duration: %.1f hours, Reactions: %d", duration / 3600, len(reactions))
    log.info("Size: %.2f GB", final_path.stat().st_size / (1024**3))
    log.info("=" * 60)

    # Upload
    try:
        upload_to_youtube(final_path, duration, len(reactions))
    except Exception as e:
        log.error("Upload failed: %s", e)
        log.info("Video ready at: %s — can be uploaded manually", final_path)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
