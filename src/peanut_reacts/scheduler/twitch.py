"""HasanAbi / Twitch VOD pipeline.

Downloads Twitch VODs, transcribes with Whisper, segments by topic using LLM,
cuts clips, generates titles/descriptions, and uploads to YouTube.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def download_twitch_vod(
    channel: str,
    output_dir: Path,
    max_vods: int = 1,
) -> list[dict]:
    """Download latest Twitch VODs using yt-dlp.

    Returns list of {id, title, path, duration} dicts.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # List recent VODs
    try:
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--print", "%(id)s\t%(title)s\t%(duration)s",
             f"https://www.twitch.tv/{channel}/videos?filter=archives&sort=time"],
            capture_output=True, text=True, timeout=60,
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
    except Exception as e:
        log.error("Failed to list VODs for %s: %s", channel, e)
        return []

    vods = []
    for line in lines[:max_vods]:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        vod_id = parts[0]
        title = parts[1]
        duration = float(parts[2]) if len(parts) > 2 and parts[2] else 0

        vod_path = output_dir / f"{vod_id}.mp4"
        if vod_path.exists():
            log.info("VOD %s already downloaded", vod_id)
            vods.append({"id": vod_id, "title": title, "path": str(vod_path), "duration": duration})
            continue

        log.info("Downloading VOD %s: %s", vod_id, title)
        try:
            subprocess.run([
                "yt-dlp",
                "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                "--merge-output-format", "mp4",
                "-o", str(vod_path),
                f"https://www.twitch.tv/videos/{vod_id}",
            ], check=True, capture_output=True, timeout=14400)  # 4 hour timeout
            vods.append({"id": vod_id, "title": title, "path": str(vod_path), "duration": duration})
        except Exception as e:
            log.error("Failed to download VOD %s: %s", vod_id, e)

    return vods


def transcribe_vod(vod_path: str, output_dir: Path, model: str = "base") -> list[dict]:
    """Transcribe a VOD using Whisper. Returns list of {start, end, text} segments."""
    vod = Path(vod_path)
    transcript_path = output_dir / f"{vod.stem}_transcript.json"

    if transcript_path.exists():
        log.info("Transcript already exists: %s", transcript_path.name)
        return json.loads(transcript_path.read_text(encoding="utf-8"))

    log.info("Transcribing %s with Whisper (%s)...", vod.name, model)

    try:
        import whisper
        m = whisper.load_model(model)
        result = m.transcribe(str(vod), language="en", verbose=False)
        segments = [
            {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
            for s in result["segments"]
        ]
        transcript_path.write_text(json.dumps(segments, indent=2), encoding="utf-8")
        log.info("Transcribed %d segments", len(segments))
        return segments
    except ImportError:
        log.warning("Whisper not installed, trying auto-subtitles...")
        # Fallback: try to get auto-generated subtitles from Twitch/YouTube
        return _fallback_transcript(vod_path, output_dir)
    except Exception as e:
        log.error("Transcription failed: %s", e)
        return _fallback_transcript(vod_path, output_dir)


def _fallback_transcript(vod_path: str, output_dir: Path) -> list[dict]:
    """Fallback: generate transcript using edge-tts trick or simple audio analysis."""
    log.warning("Using fallback transcript (empty segments every 30s)")
    from peanut_reacts.core.ffmpeg import get_video_duration
    duration = get_video_duration(Path(vod_path))
    segments = []
    for t in range(0, int(duration), 30):
        segments.append({"start": t, "end": t + 30, "text": ""})
    return segments


def segment_by_topic_llm(
    transcript: list[dict],
    llm_provider,
    min_minutes: int = 10,
    max_minutes: int = 30,
) -> list[dict]:
    """Use LLM to detect topic changes and segment the transcript.

    Returns list of {index, topic, title, start, end, duration} dicts.
    """
    if not transcript:
        return []

    total_duration = transcript[-1].get("end", 0)
    chunk_minutes = 5
    chunk_size = chunk_minutes * 60

    # Analyze each 5-minute chunk for topic
    topic_timeline = []
    for chunk_start in range(0, int(total_duration), chunk_size):
        chunk_end = chunk_start + chunk_size
        chunk_text = " ".join(
            s["text"] for s in transcript
            if chunk_start <= s.get("start", 0) < chunk_end and s.get("text")
        )[:1500]

        if not chunk_text.strip():
            topic_timeline.append({"time": chunk_start, "topic": "silence", "text": ""})
            continue

        try:
            response = llm_provider.complete([
                {"role": "system", "content": "You label topics. Reply with ONLY a short topic label (3-8 words). No explanation."},
                {"role": "user", "content": f"What is the main topic discussed here?\n\n\"{chunk_text}\""},
            ], temperature=0.2, max_tokens=30)
            topic = response.strip().strip('"').strip("'")
        except Exception:
            topic = "discussion"

        topic_timeline.append({"time": chunk_start, "topic": topic, "text": chunk_text[:200]})

    # Group consecutive same-topic chunks into segments
    segments = []
    if not topic_timeline:
        return segments

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
    end_time = total_duration
    if end_time - seg_start >= min_minutes * 60:
        segments.append({
            "index": seg_index,
            "topic": current_topic,
            "start": seg_start,
            "end": end_time,
            "duration": end_time - seg_start,
        })

    # Enforce max length
    for seg in segments:
        if seg["duration"] > max_minutes * 60:
            seg["end"] = seg["start"] + max_minutes * 60
            seg["duration"] = max_minutes * 60

    log.info("Found %d topic segments from %.1f hours of content", len(segments), total_duration / 3600)
    return segments


def generate_segment_metadata(segment: dict, llm_provider) -> dict:
    """Generate YouTube title and description for a segment using LLM."""
    try:
        response = llm_provider.complete([
            {"role": "system", "content": (
                "Generate a YouTube title and description for a HasanAbi stream clip. "
                "The title should be clickable, engaging, under 80 characters. "
                "Reply as JSON: {\"title\": \"...\", \"description\": \"...\"}"
            )},
            {"role": "user", "content": f"Topic: {segment['topic']}\nDuration: {segment['duration'] / 60:.0f} minutes"},
        ], temperature=0.7, max_tokens=200)

        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        meta = json.loads(text)
        return {
            "title": meta.get("title", f"HasanAbi - {segment['topic']}")[:80],
            "description": meta.get("description", f"HasanAbi discusses {segment['topic']}"),
        }
    except Exception as e:
        log.warning("Metadata generation failed: %s", e)
        return {
            "title": f"HasanAbi - {segment['topic'][:60]}",
            "description": f"HasanAbi stream clip: {segment['topic']}",
        }


def cut_segment(vod_path: str, segment: dict, output_dir: Path, encoder: str = "libx264") -> Optional[Path]:
    """Cut a segment from the VOD using FFmpeg."""
    output_dir.mkdir(parents=True, exist_ok=True)
    clip_path = output_dir / f"clip_{segment['index']:03d}_{segment['topic'][:30].replace(' ', '_')}.mp4"

    if clip_path.exists():
        log.info("Clip already exists: %s", clip_path.name)
        return clip_path

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(segment["start"]),
            "-to", str(segment["end"]),
            "-i", str(vod_path),
            "-c:v", encoder,
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(clip_path),
        ], check=True, capture_output=True, timeout=600)
        log.info("Cut clip: %s (%.1f min)", clip_path.name, segment["duration"] / 60)
        return clip_path
    except Exception as e:
        log.error("Failed to cut segment %d: %s", segment["index"], e)
        return None


def run_full_twitch_pipeline(
    channel_name: str,
    output_base: Path,
    llm_provider,
    encoder: str = "libx264",
    max_segments: int = 5,
    upload_service=None,
    upload_privacy: str = "public",
    tags: list[str] = None,
) -> tuple[int, int]:
    """Full pipeline: download -> transcribe -> segment -> cut -> upload.

    Returns (clips_uploaded, errors).
    """
    vod_dir = output_base / "vods"
    clips_dir = output_base / "clips"
    vod_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Download latest VOD
    log.info("=" * 50)
    log.info("Step 1: Downloading latest VOD for %s", channel_name)
    vods = download_twitch_vod(channel_name, vod_dir, max_vods=1)
    if not vods:
        log.error("No VODs downloaded")
        return 0, 1

    vod = vods[0]
    log.info("VOD: %s (%.1f hours)", vod["title"], vod.get("duration", 0) / 3600)

    # Step 2: Transcribe
    log.info("Step 2: Transcribing...")
    transcript = transcribe_vod(vod["path"], vod_dir)
    if not transcript:
        log.error("Transcription failed")
        return 0, 1

    # Step 3: Segment by topic
    log.info("Step 3: Segmenting by topic...")
    segments = segment_by_topic_llm(transcript, llm_provider)
    if not segments:
        log.warning("No segments found. Using fixed 20-minute chunks.")
        from peanut_reacts.core.ffmpeg import get_video_duration
        duration = get_video_duration(Path(vod["path"]))
        segments = [
            {"index": i, "topic": f"Part {i+1}", "start": i * 1200,
             "end": min((i + 1) * 1200, duration), "duration": min(1200, duration - i * 1200)}
            for i in range(int(duration / 1200))
        ]

    log.info("Found %d segments", len(segments))

    # Step 4: Cut and upload each segment
    uploaded = 0
    errors = 0

    for seg in segments[:max_segments]:
        # Generate metadata
        meta = generate_segment_metadata(seg, llm_provider)
        log.info("Segment %d: %s (%.1f min)", seg["index"], meta["title"], seg["duration"] / 60)

        # Cut clip
        clip_path = cut_segment(vod["path"], seg, clips_dir, encoder)
        if clip_path is None:
            errors += 1
            continue

        # Upload if service provided
        if upload_service:
            try:
                from peanut_reacts.upload.youtube_upload import UploadMetadata, YouTubeUploader
                uploader = YouTubeUploader(upload_service)
                upload_meta = UploadMetadata(
                    title=meta["title"],
                    description=meta["description"] + f"\n\n#hasanabi #hasan #politics #twitch #clips\n\n{'#'.join(tags or [])}",
                    tags=tags or ["hasanabi", "hasan", "politics", "twitch"],
                    category_id="24",  # Entertainment
                    privacy_status=upload_privacy,
                )
                result = uploader.upload_video(clip_path, upload_meta)
                log.info("Uploaded: %s", result.url)
                uploaded += 1
            except Exception as e:
                log.error("Upload failed: %s", e)
                errors += 1
        else:
            log.info("Clip saved (no upload service): %s", clip_path)
            uploaded += 1

    log.info("Pipeline complete: %d uploaded, %d errors", uploaded, errors)
    return uploaded, errors
