"""HasanAbi Archive pipeline — downloads, segments, titles, uploads, and Shorts.

Source: https://www.youtube.com/@HasanAbiArchivefan0 (full-length stream archives)

Flow:
1. Discover latest video from the archive channel
2. Download video + auto-captions (SRT)
3. Segment by topic using LLM (10-60 min clips)
4. Generate clickable titles + descriptions per segment
5. Upload each segment to our HasanAbi channel
6. Extract Shorts from best moments across all segments
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The fan archive channel that posts full-length streams
ARCHIVE_CHANNEL_URL = "https://www.youtube.com/@HasanAbiArchivefan0/videos"


def discover_latest_archive(max_results: int = 3) -> list[dict]:
    """Get the latest videos from the HasanAbi Archive fan channel."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist",
             "--print", "%(id)s\t%(title)s\t%(duration)s\t%(upload_date)s",
             "--playlist-end", str(max_results),
             ARCHIVE_CHANNEL_URL],
            capture_output=True, text=True, timeout=60,
        )
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            videos.append({
                "id": parts[0],
                "title": parts[1],
                "duration": float(parts[2]) if len(parts) > 2 and parts[2] != "NA" else 0,
                "upload_date": parts[3] if len(parts) > 3 else "",
                "url": f"https://youtube.com/watch?v={parts[0]}",
            })
        log.info("Found %d archive videos", len(videos))
        return videos
    except Exception as e:
        log.error("Failed to discover archive: %s", e)
        return []


def download_archive_video(video_id: str, output_dir: Path) -> dict:
    """Download a video + auto-captions from the archive channel.

    Returns {video_path, srt_path, duration} or empty dict on failure.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = output_dir / f"{video_id}.mp4"
    srt_path = output_dir / f"{video_id}.en.srt"

    if video_path.exists() and srt_path.exists():
        log.info("Already downloaded: %s", video_id)
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=10,
        )
        return {
            "video_path": str(video_path),
            "srt_path": str(srt_path),
            "duration": float(r.stdout.strip()) if r.stdout.strip() else 0,
        }

    log.info("Downloading %s...", video_id)
    try:
        # Do NOT use capture_output=True — yt-dlp emits heavy progress output
        # for multi-hour downloads and fills the 64KB pipe buffer, deadlocking
        # the parent. Route stdout to DEVNULL and let stderr surface in logs.
        # -q silences per-frame progress; --no-warnings drops the noise we
        # don't care about.
        subprocess.run([
            "yt-dlp",
            "-q", "--no-warnings", "--newline",
            "-f", "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]/best[height<=1080]",
            "--merge-output-format", "mp4",
            "--write-auto-sub", "--sub-lang", "en",
            "--convert-subs", "srt",
            "-o", str(video_path),
            f"https://youtube.com/watch?v={video_id}",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=7200)

        # Find the SRT (yt-dlp adds .en before .srt)
        srt_candidates = list(output_dir.glob(f"{video_id}*.srt"))
        actual_srt = srt_candidates[0] if srt_candidates else None

        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(r.stdout.strip()) if r.stdout.strip() else 0

        return {
            "video_path": str(video_path),
            "srt_path": str(actual_srt) if actual_srt else None,
            "duration": duration,
        }
    except Exception as e:
        log.error("Download failed: %s", e)
        return {}


def parse_srt_to_segments(srt_path: str) -> list[dict]:
    """Parse SRT file into list of {start, end, text} segments."""
    if not srt_path or not Path(srt_path).exists():
        return []
    from peanut_reacts.core.srt import parse_srt
    return parse_srt(Path(srt_path))


def segment_by_topic(
    transcript: list[dict],
    total_duration: float,
    llm_provider,
    min_minutes: int = 10,
    max_minutes: int = 60,
) -> list[dict]:
    """Segment a long transcript into topic-based clips using LLM.

    Analyzes 5-minute chunks, detects topic shifts, groups into segments.
    Returns list of {index, topic, start, end, duration, title, description}.
    """
    if not transcript:
        log.warning("Empty transcript — using fixed 20-min chunks")
        return _fixed_chunks(total_duration, chunk_minutes=20)

    chunk_seconds = 300  # 5-minute analysis windows
    topic_timeline = []

    for chunk_start in range(0, int(total_duration), chunk_seconds):
        chunk_end = min(chunk_start + chunk_seconds, int(total_duration))
        chunk_text = " ".join(
            s.get("text", "") for s in transcript
            if chunk_start <= s.get("start", 0) < chunk_end and s.get("text")
        )[:2000]

        if not chunk_text.strip():
            topic_timeline.append({"time": chunk_start, "topic": "silence"})
            continue

        try:
            response = llm_provider.complete([
                {"role": "system", "content": "You label topics from Hasan Piker's stream. Reply with ONLY a concise topic label (5-10 words). No explanation."},
                {"role": "user", "content": f"What topic is being discussed?\n\n\"{chunk_text}\""},
            ], temperature=0.2, max_tokens=30)
            topic = response.strip().strip('"').strip("'")
        except Exception:
            topic = "general discussion"

        topic_timeline.append({"time": chunk_start, "topic": topic, "text": chunk_text[:200]})

    # Group consecutive same-topic chunks into segments
    segments = []
    if not topic_timeline:
        return _fixed_chunks(total_duration, chunk_minutes=20)

    current_topic = topic_timeline[0]["topic"]
    seg_start = 0
    seg_index = 0

    for i, entry in enumerate(topic_timeline[1:], 1):
        if entry["topic"] != current_topic:
            duration = entry["time"] - seg_start
            if duration >= min_minutes * 60:
                segments.append({
                    "index": seg_index,
                    "topic": current_topic,
                    "start": seg_start,
                    "end": entry["time"],
                    "duration": duration,
                })
                seg_index += 1
            seg_start = entry["time"]
            current_topic = entry["topic"]

    # Final segment
    if total_duration - seg_start >= min_minutes * 60:
        segments.append({
            "index": seg_index,
            "topic": current_topic,
            "start": seg_start,
            "end": total_duration,
            "duration": total_duration - seg_start,
        })

    # Enforce max length — split oversized segments
    final = []
    for seg in segments:
        if seg["duration"] > max_minutes * 60:
            # Split into max_minutes-sized chunks
            sub_start = seg["start"]
            sub_idx = 0
            while sub_start < seg["end"]:
                sub_end = min(sub_start + max_minutes * 60, seg["end"])
                if sub_end - sub_start >= min_minutes * 60:
                    final.append({
                        "index": len(final),
                        "topic": seg["topic"] + (f" (Part {sub_idx + 1})" if sub_idx > 0 else ""),
                        "start": sub_start,
                        "end": sub_end,
                        "duration": sub_end - sub_start,
                    })
                sub_start = sub_end
                sub_idx += 1
        else:
            seg["index"] = len(final)
            final.append(seg)

    log.info("Segmented into %d clips (%.1f hours total)", len(final), sum(s["duration"] for s in final) / 3600)

    # Fallback: if LLM grouping produces 0 segments (topics too similar),
    # use fixed 30-minute chunks instead
    if not final:
        log.warning("LLM segmentation found 0 clips — falling back to fixed 30-min chunks")
        final = _fixed_chunks(total_duration, chunk_minutes=30)
        # Use topic labels from the LLM for each chunk
        for seg in final:
            matching = [t for t in topic_timeline
                       if seg["start"] <= t["time"] < seg["end"] and t["topic"] != "silence"]
            if matching:
                seg["topic"] = matching[len(matching) // 2]["topic"]  # Middle chunk's topic

    return final


def _fixed_chunks(duration: float, chunk_minutes: int = 20) -> list[dict]:
    """Fallback: split into fixed-length chunks."""
    segments = []
    for i in range(0, int(duration), chunk_minutes * 60):
        end = min(i + chunk_minutes * 60, duration)
        if end - i >= 300:  # At least 5 min
            segments.append({
                "index": len(segments),
                "topic": f"Part {len(segments) + 1}",
                "start": i,
                "end": end,
                "duration": end - i,
            })
    return segments


def generate_segment_metadata(segment: dict, source_title: str, llm_provider) -> dict:
    """Generate a YouTube-optimized title + description for a segment."""
    topic = segment.get("topic", "Discussion")
    duration_min = int(segment["duration"] / 60)

    try:
        response = llm_provider.complete([
            {"role": "system", "content": (
                "You write YouTube titles for HasanAbi stream clips. Rules:\n"
                "- Clickbait but accurate\n"
                "- Under 80 characters\n"
                "- ALL CAPS for emphasis on key words\n"
                "- Include the topic clearly\n"
                "Reply as JSON: {\"title\": \"...\", \"description\": \"...\"}"
            )},
            {"role": "user", "content": f"Topic: {topic}\nDuration: {duration_min} min\nFrom: {source_title}"},
        ], temperature=0.7, max_tokens=200)

        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        meta = json.loads(text)
        return {
            "title": meta.get("title", f"HasanAbi - {topic}")[:80],
            "description": meta.get("description", f"HasanAbi discusses {topic}") +
                          f"\n\nOriginal stream archived from HasanAbi.\n\n"
                          f"#hasanabi #hasan #politics #twitch #clips",
        }
    except Exception as e:
        log.warning("Metadata gen failed: %s", e)
        return {
            "title": f"HasanAbi - {topic}"[:80],
            "description": f"HasanAbi discusses {topic}\n\n#hasanabi #hasan #politics #twitch",
        }


def cut_segment(video_path: str, segment: dict, output_dir: Path) -> Optional[Path]:
    """Cut a segment from the source video.

    Tries NVENC re-encode first (YouTube prefers uniformly-encoded mp4s),
    and falls back to stream-copy if the re-encode times out. Stream-copy
    gives keyframe-aligned boundaries which can be off by up to ~10 s, but
    it's instant and never fails on problematic source files.

    The April 21 "Trump Is In Trouble" stream showed exactly this failure
    mode: segments 0-5 re-encoded cleanly, seg 6 hit 1200 s timeout — the
    source's keyframe structure around the 100-min mark made NVENC seek
    pathologically slow. Stream-copy solves it.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_topic = re.sub(r'[^\w\s-]', '', segment.get("topic", "clip"))[:30].strip().replace(" ", "_")
    clip_path = output_dir / f"seg_{segment['index']:02d}_{safe_topic}.mp4"

    if clip_path.exists():
        log.info("Segment already cut: %s", clip_path.name)
        return clip_path

    encoder = "h264_nvenc" if _nvenc_available() else "libx264"
    preset = "fast" if encoder == "h264_nvenc" else "veryfast"

    # Attempt 1: NVENC re-encode (preferred — clean mp4, precise cuts)
    # Bumped timeout to 1800 s (30 min) since some 30-min chunks can take
    # up to 8-10 min on a loaded GPU. Anything longer we bail to fallback.
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(segment["start"]),
            "-to", str(segment["end"]),
            "-i", str(video_path),
            "-c:v", encoder, "-preset", preset,
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(clip_path),
        ], check=True, capture_output=True, timeout=1800)
        log.info("Cut: %s (%.0f min)", clip_path.name, segment["duration"] / 60)
        return clip_path
    except subprocess.TimeoutExpired:
        log.warning(
            "Cut NVENC timed out on %s (30 min), falling back to stream-copy",
            clip_path.name,
        )
        # Clean up the half-written output so the stream-copy starts fresh
        try:
            clip_path.unlink(missing_ok=True)
        except OSError:
            pass
    except subprocess.CalledProcessError as e:
        log.warning(
            "Cut NVENC failed on %s (%s), falling back to stream-copy",
            clip_path.name,
            (e.stderr or b"").decode("utf-8", errors="replace")[-200:],
        )
        try:
            clip_path.unlink(missing_ok=True)
        except OSError:
            pass
    except Exception as e:
        log.error("Cut NVENC crashed: %s — falling back to stream-copy", e)
        try:
            clip_path.unlink(missing_ok=True)
        except OSError:
            pass

    # Attempt 2: stream-copy. Keyframe-aligned, but instant and robust.
    try:
        duration = segment["end"] - segment["start"]
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(segment["start"]),
            "-i", str(video_path),
            "-t", str(duration),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            str(clip_path),
        ], check=True, capture_output=True, timeout=300)
        log.info(
            "Cut (stream-copy fallback): %s (%.0f min)",
            clip_path.name, duration / 60,
        )
        return clip_path
    except Exception as e:
        log.error("Cut stream-copy also failed: %s", e)
        return None


def _nvenc_available() -> bool:
    """Delegate to the canonical, functionally-tested nvenc check."""
    from peanut_reacts.core.ffmpeg import nvenc_available
    return nvenc_available()


# ═══════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════

def run_hasan_pipeline(
    channel_config,
    settings,
    db,
    max_segments: int = 10,
    extract_shorts: bool = True,
) -> tuple[int, int]:
    """Full HasanAbi pipeline: discover → download → segment → upload → Shorts.

    Returns (segments_uploaded, errors).
    """
    from peanut_reacts.character.reaction_generator import create_llm_provider, LLMConfig
    from peanut_reacts.core.config import load_config

    cfg = load_config()
    llm = create_llm_provider(LLMConfig(
        provider=settings.llm_provider,
        api_key=cfg.deepseek_api_key,
        model=settings.llm_model,
        temperature=0.3,
    ))

    output_dir = PROJECT_ROOT / settings.output_dir / channel_config.id
    downloads_dir = output_dir / "downloads"
    clips_dir = output_dir / "clips"

    uploaded = 0
    errors = 0

    # Step 1: Discover latest from archive channel
    log.info("=" * 60)
    log.info("[HasanAbi] Step 1: Discovering latest archive videos...")
    log.info("=" * 60)
    archive_videos = discover_latest_archive(max_results=2)
    if not archive_videos:
        log.warning("[HasanAbi] No archive videos found")
        return 0, 1

    # Filter already-processed
    existing_ids = {j["video_id"] for j in db.get_channel_jobs(channel_config.id, limit=500)
                    if j["video_id"] and j["status"] in ("done", "running")}

    new_videos = [v for v in archive_videos if v["id"] not in existing_ids]
    if not new_videos:
        log.info("[HasanAbi] All archive videos already processed")
        return 0, 0

    video = new_videos[0]  # Process the latest unprocessed
    log.info("[HasanAbi] Processing: %s (%.1f hours)", video["title"][:60], video["duration"] / 3600)

    job_id = db.create_job(channel_config.id, video["id"], video["title"])

    try:
        # Step 2: Download
        db.start_job(job_id, "download")
        log.info("[HasanAbi] Step 2: Downloading...")
        dl = download_archive_video(video["id"], downloads_dir)
        if not dl or not dl.get("video_path"):
            db.fail_job(job_id, "Download failed")
            return 0, 1

        video_path = dl["video_path"]
        srt_path = dl.get("srt_path")
        duration = dl.get("duration", 0)

        # Step 3: Parse transcript + segment by topic
        db.update_job(job_id, step="segment")
        log.info("[HasanAbi] Step 3: Segmenting by topic...")
        transcript = parse_srt_to_segments(srt_path)
        segments = segment_by_topic(transcript, duration, llm,
                                    min_minutes=10, max_minutes=60)
        log.info("[HasanAbi] Found %d segments", len(segments))

        # Step 4: Cut + generate metadata + upload each segment
        db.update_job(job_id, step="upload")
        youtube_service = None
        if channel_config.client_secrets and channel_config.oauth_token:
            try:
                from peanut_reacts.upload.youtube_auth import get_authenticated_service
                token_path = Path(channel_config.oauth_token).expanduser()
                secrets_path = Path(channel_config.client_secrets).expanduser()
                if token_path.exists() and secrets_path.exists():
                    youtube_service = get_authenticated_service(
                        secrets_path,
                        token_path=token_path,
                    )
                else:
                    log.warning(
                        "[HasanAbi] OAuth files missing: token=%s(%s), secrets=%s(%s)",
                        token_path, token_path.exists(),
                        secrets_path, secrets_path.exists(),
                    )
            except Exception as e:
                log.warning("[HasanAbi] YouTube auth failed: %s", e)

        for seg in segments[:max_segments]:
            try:
                # Cut segment
                clip = cut_segment(video_path, seg, clips_dir)
                if not clip:
                    errors += 1
                    continue

                # Generate title/description
                meta = generate_segment_metadata(seg, video["title"], llm)
                log.info("[HasanAbi] Segment %d: %s", seg["index"], meta["title"][:60])

                # Upload
                if youtube_service:
                    from peanut_reacts.upload.youtube_upload import YouTubeUploader, UploadMetadata
                    uploader = YouTubeUploader(youtube_service)
                    upload_meta = UploadMetadata(
                        title=meta["title"][:100],
                        description=meta["description"],
                        tags=channel_config.tags[:15],
                        category_id="24",  # Entertainment
                        privacy_status=channel_config.upload_privacy,
                    )
                    result = uploader.upload_video(clip, upload_meta)
                    log.info("[HasanAbi] Uploaded: %s", result.url)
                    uploaded += 1

                    # Extract Shorts from this segment
                    if extract_shorts:
                        try:
                            from .shorts_pipeline import extract_shorts_from_video
                            extract_shorts_from_video(
                                video_path=clip,
                                video_id=f"{video['id']}_seg{seg['index']}",
                                video_title=meta["title"],
                                reactions=[],  # No character reactions for HasanAbi
                                comments=[],
                                channel_config=channel_config,
                                settings=settings,
                                output_dir=output_dir,
                                max_clips=3,
                                llm_provider=llm,
                            )
                        except Exception as e:
                            log.warning("[HasanAbi] Shorts extraction failed: %s", e)
                else:
                    log.info("[HasanAbi] Clip saved (no YT auth): %s", clip.name)
                    uploaded += 1

            except Exception as e:
                log.error("[HasanAbi] Segment %d failed: %s", seg["index"], e)
                errors += 1

        db.complete_job(job_id, str(output_dir))

    except Exception as e:
        log.error("[HasanAbi] Pipeline crashed: %s", e)
        db.fail_job(job_id, str(e))
        errors += 1

    log.info("[HasanAbi] Done: %d uploaded, %d errors", uploaded, errors)
    return uploaded, errors
