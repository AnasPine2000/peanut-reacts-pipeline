"""Complete pipeline orchestrator — ties discovery, processing, and posting together.

Flow per channel:
1. DISCOVER: find new videos from source (playlist/channel/twitch/reddit)
2. FILTER: skip already-processed, apply duration/score filters
3. FETCH: download video + subtitles + comments
4. CACHE CHECK: reuse existing reactions if available
5. PROCESS: generate reactions / compile / extract peaks
6. POST: upload to YouTube (long) + extract clips + post to Shorts/TikTok

Each step has retry and state tracking in SQLite.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .channel_config import ChannelConfig, GlobalSettings
from .db import PipelineDB
from .discovery import discover_for_channel, filter_new_videos, filter_by_duration
from .notify import notify_job_complete, notify_job_failed

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def get_processed_video_ids(db: PipelineDB, channel_id: str) -> set[str]:
    """Get set of video IDs already processed for a channel."""
    jobs = db.get_channel_jobs(channel_id, limit=1000)
    return {j["video_id"] for j in jobs if j["video_id"] and j["status"] in ("done", "running")}


def fetch_video_and_comments(
    video_id: str,
    source_type: str,
    output_dir: Path,
    youtube_api_key: str = "",
) -> dict:
    """Download video, subtitles, and fetch comments.

    Returns: {video_path, srt_path, comments}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {"video_path": None, "srt_path": None, "comments": []}

    if source_type == "youtube" or source_type == "playlist":
        url = f"https://youtube.com/watch?v={video_id}"
        video_path = output_dir / f"{video_id}.mp4"

        if not video_path.exists():
            log.info("Downloading video + subtitles: %s", video_id)
            try:
                subprocess.run([
                    "yt-dlp",
                    "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                    "--merge-output-format", "mp4",
                    "--write-auto-sub", "--sub-lang", "en",
                    "--convert-subs", "srt",
                    "-o", str(video_path),
                    url,
                ], check=True, capture_output=True, timeout=1800)
            except Exception as e:
                log.error("Download failed: %s", e)
                return result

        result["video_path"] = str(video_path)

        # Find SRT
        srts = list(output_dir.glob(f"{video_id}*.srt"))
        if srts:
            result["srt_path"] = str(srts[0])

        # Fetch comments
        if youtube_api_key:
            try:
                from peanut_reacts.download.youtube_comments import YouTubeCommentFetcher
                fetcher = YouTubeCommentFetcher(api_key=youtube_api_key)
                comments = fetcher.fetch_comments(video_id, max_results=500)
                result["comments"] = [
                    {"text": c.text, "like_count": c.like_count, "author": c.author}
                    for c in comments
                ]
                log.info("Fetched %d comments", len(comments))
            except Exception as e:
                log.warning("Comment fetch failed: %s", e)

    elif source_type == "twitch":
        # Twitch handled in twitch.py pipeline
        pass

    return result


def generate_or_reuse_reactions(
    video_id: str,
    srt_path: Path,
    duration: float,
    channel: ChannelConfig,
    settings: GlobalSettings,
) -> list[dict]:
    """Generate reactions, using cache when possible to save API costs."""
    from peanut_reacts.character.reaction_cache import get_cache
    cache = get_cache()

    # Try full video cache first
    cached = cache.get_video_reactions(video_id, character=channel.character)
    if cached:
        return cached

    # Generate fresh
    from peanut_reacts.character.reaction_generator import create_llm_provider, LLMConfig
    from peanut_reacts.core.srt import parse_srt
    from peanut_reacts.core.config import load_config

    cfg = load_config()
    llm = create_llm_provider(LLMConfig(
        provider=settings.llm_provider,
        api_key=cfg.deepseek_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    ))

    segments = parse_srt(srt_path) if srt_path and srt_path.exists() else []
    if not segments:
        log.warning("No transcript available for %s", video_id)
        return []

    reactions = []
    chunk_size = 30

    for chunk_start in range(0, int(duration), chunk_size):
        chunk_end = min(chunk_start + chunk_size, int(duration))
        chunk_segs = [s for s in segments if chunk_start <= s.get("start", 0) < chunk_end]
        if not chunk_segs:
            continue

        transcript_text = " ".join(s.get("text", "") for s in chunk_segs)

        # Check chunk cache
        chunk_cached = cache.get_chunk_reactions(transcript_text, channel.character)
        if chunk_cached:
            for r in chunk_cached:
                r["start"] = r.get("start", 0) + chunk_start
            reactions.extend(chunk_cached)
            continue

        # Generate fresh
        target_count = max(1, int(channel.reactions_per_minute * chunk_size / 60))
        prompt = (
            f"{channel.llm_personality}\n\n"
            f"React to this transcript chunk:\n\"{transcript_text[:500]}\"\n\n"
            f"Give exactly {target_count} short reactions. "
            f"Return ONLY a JSON array: [{{\"start\": seconds_offset, \"text\": \"...\", \"emotion\": \"shocked|laughing|excited|mind_blown|neutral\", \"intensity\": 1.0-3.0}}]"
        )

        try:
            response = llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=settings.llm_temperature, max_tokens=800,
            )
            text = response.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            chunk_reactions = json.loads(text)

            # Normalize
            for r in chunk_reactions:
                r["start"] = float(r.get("start", 0)) + chunk_start

            # Cache chunk
            cache.store_chunk_reactions(transcript_text, channel.character, chunk_reactions)
            reactions.extend(chunk_reactions)
        except Exception as e:
            log.warning("Chunk %d LLM failed: %s", chunk_start, e)

    # Cache the full video result
    if reactions:
        cache.store_video_reactions(
            video_id=video_id, reactions=reactions,
            channel_id=channel.id, character=channel.character,
            duration=duration,
        )

    return reactions


def extract_and_post_clips(
    video_path: Path,
    video_id: str,
    comments: list[dict],
    reactions: list[dict],
    output_dir: Path,
    channel: ChannelConfig,
    youtube_service=None,
    tiktok_uploader=None,
    max_clips: int = 5,
) -> tuple[int, int]:
    """Extract peak moments and post as Shorts/TikTok.

    Returns (posted_count, errors).
    """
    from peanut_reacts.clips.peak_detector import detect_peaks
    from peanut_reacts.clips.clip_extractor import process_peak_to_shorts

    log.info("Detecting peaks for %s", video_id)
    peaks = detect_peaks(video_id, comments=comments, reactions=reactions, top_n=max_clips)

    if not peaks:
        log.info("No peaks found for %s", video_id)
        return 0, 0

    log.info("Found %d peaks, processing clips...", len(peaks))
    clips_dir = output_dir / "clips" / video_id
    posted = 0
    errors = 0

    for i, peak in enumerate(peaks):
        try:
            result = process_peak_to_shorts(
                video_path, peak, clips_dir,
                character_name=channel.character.title(),
            )
            final_clip = result.get("captioned") or result.get("vertical")
            if not final_clip:
                errors += 1
                continue

            final_path = Path(final_clip)
            log.info("Clip %d/%d ready: %s", i + 1, len(peaks), final_path.name)

            # Generate caption
            caption = _generate_clip_caption(peak, channel)
            hashtags = channel.tags + ["shorts", "fyp", "viral"]

            # Post to YouTube Shorts
            if youtube_service:
                try:
                    from peanut_reacts.upload.youtube_upload import YouTubeUploader, UploadMetadata
                    yt_uploader = YouTubeUploader(youtube_service)
                    meta = UploadMetadata(
                        title=caption[:100],
                        description=caption + f"\n\n#shorts\n\nFull video: youtube.com/watch?v={video_id}",
                        tags=hashtags[:20],
                        category_id="20",
                        privacy_status=channel.upload_privacy,
                    )
                    result = yt_uploader.upload_video(final_path, meta)
                    log.info("YouTube Short: %s", result.url)
                    posted += 1
                except Exception as e:
                    log.error("YouTube Shorts upload failed: %s", e)
                    errors += 1

            # Post to TikTok
            if tiktok_uploader:
                try:
                    tiktok_uploader.upload(
                        video_path=final_path,
                        caption=caption,
                        hashtags=hashtags,
                    )
                    log.info("TikTok posted: %s", final_path.name)
                    posted += 1
                except Exception as e:
                    log.error("TikTok upload failed: %s", e)
                    errors += 1

        except Exception as e:
            log.error("Peak %d processing failed: %s", i, e)
            errors += 1

    return posted, errors


def _generate_clip_caption(peak, channel: ChannelConfig) -> str:
    """Auto-generate a caption for a peak clip."""
    hooks = []
    if peak.peak_emotion:
        emotion_hooks = {
            "shocked": "YOU WON'T BELIEVE THIS",
            "laughing": "I CAN'T STOP LAUGHING",
            "mind_blown": "MY MIND IS BLOWN",
            "screaming": "I HAD TO SCREAM",
        }
        hooks.append(emotion_hooks.get(peak.peak_emotion.lower(), ""))
    if peak.comment_count >= 5:
        hooks.append(f"{peak.comment_count}+ fans timestamped this")
    if peak.replay_intensity > 0.8:
        hooks.append("MOST REPLAYED MOMENT")

    hook = hooks[0] if hooks else f"{channel.character.title()} reacts"
    return f"{hook} | {channel.character.title()} Reacts"


# ═══════════════════════════════════════════════════════════════
# Main Orchestration Entry Point
# ═══════════════════════════════════════════════════════════════

def run_full_channel_pipeline(
    channel: ChannelConfig,
    settings: GlobalSettings,
    db: PipelineDB,
    post_shorts: bool = True,
    post_tiktok: bool = False,
    apply_learning: bool = True,
) -> dict:
    """Complete end-to-end pipeline for one channel.

    Returns summary dict: {discovered, processed, uploaded, clips, errors, applied_recs}
    """
    summary = {"discovered": 0, "processed": 0, "uploaded": 0, "clips": 0,
               "errors": 0, "applied_recs": 0, "override_notes": []}
    log.info("=" * 60)
    log.info("PIPELINE START: [%s] (%s)", channel.name, channel.source_type)
    log.info("=" * 60)

    output_base = PROJECT_ROOT / settings.output_dir / channel.id
    output_base.mkdir(parents=True, exist_ok=True)

    run_id = db.start_run(channel.id)

    # ── Apply learned recommendations (closes the feedback loop) ──
    overrides = None
    pending_recs = []
    if apply_learning:
        try:
            from peanut_reacts.learning.applicator import (
                load_pending_recommendations, build_overrides, apply_overrides_to_channel,
            )
            pending_recs = load_pending_recommendations("learning.db", channel.id, max_recs=10)
            if pending_recs:
                overrides = build_overrides(pending_recs)
                apply_overrides_to_channel(channel, overrides)
                summary["applied_recs"] = len(overrides.applied_recommendation_ids)
                summary["override_notes"] = overrides.notes
                log.info("[%s] Applied %d recommendations: %s",
                         channel.id, summary["applied_recs"], overrides.notes)
        except Exception as e:
            log.warning("Learning applicator failed: %s", e)

    try:
        # Step 1: Discovery
        log.info("[1/6] Discovering new content...")
        discovered = discover_for_channel(channel)
        summary["discovered"] = len(discovered)

        processed_ids = get_processed_video_ids(db, channel.id)
        new_videos = filter_new_videos(discovered, processed_ids)
        new_videos = filter_by_duration(new_videos, min_minutes=5, max_minutes=600)
        new_videos = new_videos[:channel.max_videos_per_run]

        if not new_videos:
            log.info("No new videos to process")
            db.finish_run(run_id, 0, 0)
            return summary

        log.info("[2/6] Processing %d new videos...", len(new_videos))

        # Setup YouTube upload service if credentials available
        youtube_service = None
        if channel.client_secrets and Path(channel.client_secrets).exists():
            try:
                from peanut_reacts.upload.youtube_auth import get_authenticated_service
                token_path = Path(channel.oauth_token).expanduser() if channel.oauth_token else None
                youtube_service = get_authenticated_service(
                    Path(channel.client_secrets),
                    token_path=token_path,
                )
            except Exception as e:
                log.warning("YouTube auth failed: %s", e)

        # Setup TikTok uploader if requested
        tiktok = None
        if post_tiktok:
            try:
                from peanut_reacts.upload.tiktok_uploader import TikTokUploader
                tiktok = TikTokUploader()
                tiktok.login_if_needed()
            except Exception as e:
                log.warning("TikTok setup failed: %s", e)

        # Step 3-6: Process each video
        for i, video in enumerate(new_videos, 1):
            log.info("[%d/%d] Processing: %s", i, len(new_videos), video.title[:60])
            job_id = db.create_job(channel.id, video.id, video.title)

            try:
                # Fetch
                db.start_job(job_id, "download")
                from peanut_reacts.core.config import load_config
                cfg = load_config()
                fetched = fetch_video_and_comments(
                    video.id, video.source_type, output_base / "downloads",
                    youtube_api_key=cfg.youtube_api_key,
                )

                if not fetched["video_path"]:
                    db.fail_job(job_id, "Download failed", "download")
                    summary["errors"] += 1
                    continue

                video_path = Path(fetched["video_path"])

                # Generate reactions (with cache)
                db.update_job(job_id, step="react")
                from peanut_reacts.core.ffmpeg import get_video_duration
                duration = get_video_duration(video_path)

                reactions = generate_or_reuse_reactions(
                    video.id,
                    Path(fetched["srt_path"]) if fetched["srt_path"] else None,
                    duration, channel, settings,
                )

                # Extract peaks and post clips to Shorts/TikTok
                if post_shorts or post_tiktok:
                    db.update_job(job_id, step="clips")
                    clips_posted, clip_errors = extract_and_post_clips(
                        video_path, video.id, fetched["comments"], reactions,
                        output_base, channel,
                        youtube_service=youtube_service if post_shorts else None,
                        tiktok_uploader=tiktok,
                    )
                    summary["clips"] += clips_posted
                    summary["errors"] += clip_errors

                db.complete_job(job_id, str(video_path))
                summary["processed"] += 1

            except Exception as e:
                log.error("Video %s failed: %s", video.id, e)
                db.fail_job(job_id, str(e))
                summary["errors"] += 1

        # Cleanup
        if tiktok:
            tiktok.close()

        db.finish_run(run_id, summary["processed"], summary["errors"])

        log.info("=" * 60)
        log.info("PIPELINE DONE: %s", json.dumps(summary))
        log.info("=" * 60)

        # Notify
        if settings.discord_webhook_url:
            if summary["processed"] > 0:
                notify_job_complete(
                    settings.discord_webhook_url, channel.name,
                    f"Processed {summary['processed']}, {summary['clips']} clips, {summary['errors']} errors",
                )
            elif summary["errors"] > 0:
                notify_job_failed(
                    settings.discord_webhook_url, channel.name,
                    f"{summary['errors']} errors, 0 processed",
                )

        return summary

    except Exception as e:
        log.error("Pipeline crashed: %s", e)
        db.finish_run(run_id, 0, 1)
        summary["errors"] += 1
        return summary
