"""Shorts posting pipeline — extract peaks, compose clips, queue, and post.

Two modes:
A) EXTRACT: After a video is uploaded, extract peaks, compose Shorts, queue them
B) POST: On a cron schedule (5x daily), pick next clip from queue, post to YT + TT
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def extract_shorts_from_video(
    video_path: Path,
    video_id: str,
    video_title: str,
    reactions: list[dict],
    comments: list[dict],
    channel_config,
    settings,
    output_dir: Path,
    max_clips: int = 10,
    llm_provider=None,
) -> int:
    """MODE A: Extract peak moments, compose Shorts, enqueue for posting.

    Called after a video upload completes. Does NOT post — just fills the queue.
    Returns number of clips queued.
    """
    from peanut_reacts.clips.peak_detector import detect_peaks
    from peanut_reacts.clips.shorts_composer import compose_short
    from peanut_reacts.clips.shorts_queue import ShortsQueue

    log.info("[Shorts] Extracting peaks from %s...", video_id)

    # Detect peaks
    peaks = detect_peaks(video_id, comments=comments, reactions=reactions, top_n=max_clips)
    if not peaks:
        log.info("[Shorts] No peaks found for %s", video_id)
        return 0

    log.info("[Shorts] Found %d peaks, composing clips...", len(peaks))

    # Get niche from channel
    niche = "sidemen"
    if "hasan" in (channel_config.id or "").lower():
        niche = "hasanabi"
    elif "ksi" in (channel_config.id or "").lower():
        niche = "ksi"
    elif "reddit" in (channel_config.source_type or "").lower():
        niche = "reddit"

    custom_hashtags = channel_config.tags[:5] if channel_config.tags else []
    shorts_dir = output_dir / "shorts" / video_id
    shorts_dir.mkdir(parents=True, exist_ok=True)

    queue = ShortsQueue(str(PROJECT_ROOT / "shorts_queue.db"))
    queued = 0

    for i, peak in enumerate(peaks):
        try:
            composed = compose_short(
                source_video=video_path,
                peak=peak,
                reactions=reactions,
                output_dir=shorts_dir,
                channel_id=channel_config.id,
                character_name=channel_config.character.title(),
                source_video_id=video_id,
                source_video_title=video_title,
                niche=niche,
                facecam_enabled=True,
                facecam_size=300,
                facecam_position="bottom-left",
                custom_hashtags=custom_hashtags,
                llm_provider=llm_provider,
            )
            if not composed:
                continue

            # Enqueue
            queue.enqueue(
                channel_id=channel_config.id,
                clip_path=str(composed.clip_path),
                source_video_id=video_id,
                source_video_title=video_title,
                peak_start=composed.peak_start,
                peak_score=composed.peak_score,
                peak_emotion=composed.peak_emotion,
                caption_yt=composed.caption_yt,
                caption_tt=composed.caption_tt,
                hook_text=composed.hook_text,
                hashtags=composed.hashtags,
            )
            queued += 1
            log.info("[Shorts] Queued clip %d/%d: %s", i + 1, len(peaks), composed.clip_path.name)

        except Exception as e:
            log.warning("[Shorts] Peak %d failed: %s", i, e)

    # Schedule the queued clips across posting times
    posting_times = ["07:00", "12:00", "18:00", "21:00", "23:00"]
    queue.schedule_next_clips(
        channel_id=channel_config.id,
        clips_per_day=5,
        posting_times=posting_times,
    )

    queue.close()
    log.info("[Shorts] Queued %d/%d clips for [%s]", queued, len(peaks), channel_config.id)
    return queued


def post_due_shorts(
    channel_config,
    settings,
    youtube_service=None,
    tiktok_uploader=None,
) -> dict:
    """MODE B: Post clips that are due from the queue.

    Called by the scheduler 5x daily at optimal posting times.
    Returns {posted_yt, posted_tt, errors}.
    """
    from peanut_reacts.clips.shorts_queue import ShortsQueue
    from peanut_reacts.scheduler.notify import send_notification

    queue = ShortsQueue(str(PROJECT_ROOT / "shorts_queue.db"))
    result = {"posted_yt": 0, "posted_tt": 0, "errors": 0}

    # Post to YouTube Shorts
    if youtube_service:
        due = queue.get_due_for_posting("youtube_shorts", limit=1)
        for clip in due:
            try:
                clip_path = Path(clip["clip_path"])
                if not clip_path.exists():
                    queue.mark_failed(clip["id"], "Clip file not found")
                    result["errors"] += 1
                    continue

                from peanut_reacts.upload.youtube_upload import YouTubeUploader, UploadMetadata
                uploader = YouTubeUploader(youtube_service)
                meta = UploadMetadata(
                    title=clip["caption_yt"][:100],
                    description=(
                        f"{clip.get('caption_yt', '')}\n\n"
                        f"Full video: https://youtube.com/watch?v={clip.get('source_video_id', '')}\n"
                        f"#shorts"
                    ),
                    tags=["shorts"] + (eval(clip["hashtags"]) if clip.get("hashtags") else [])[:10],
                    category_id="20",
                    privacy_status=channel_config.upload_privacy,
                )
                up_result = uploader.upload_video(clip_path, meta)
                queue.mark_posted(clip["id"], "youtube_shorts", up_result.url)
                result["posted_yt"] += 1
                log.info("[Shorts] Posted to YT: %s", up_result.url)

            except Exception as e:
                log.error("[Shorts] YT upload failed: %s", e)
                queue.mark_failed(clip["id"], str(e)[:200])
                result["errors"] += 1

    # Post to TikTok
    if tiktok_uploader:
        due = queue.get_due_for_posting("tiktok", limit=1)
        for clip in due:
            try:
                clip_path = Path(clip["clip_path"])
                if not clip_path.exists():
                    queue.mark_failed(clip["id"], "Clip file not found")
                    result["errors"] += 1
                    continue

                import json
                hashtags = json.loads(clip.get("hashtags", "[]"))
                success = tiktok_uploader.upload(
                    video_path=clip_path,
                    caption=clip.get("caption_tt", ""),
                    hashtags=hashtags,
                )
                if success:
                    queue.mark_posted(clip["id"], "tiktok", "posted")
                    result["posted_tt"] += 1
                    log.info("[Shorts] Posted to TikTok: %s", clip_path.name)
                else:
                    queue.mark_failed(clip["id"], "TikTok upload returned False")
                    result["errors"] += 1

            except Exception as e:
                log.error("[Shorts] TT upload failed: %s", e)
                queue.mark_failed(clip["id"], str(e)[:200])
                result["errors"] += 1

    queue.close()

    # Notify
    total_posted = result["posted_yt"] + result["posted_tt"]
    if total_posted > 0 and settings.discord_webhook_url:
        send_notification(
            settings.discord_webhook_url,
            f"Shorts Posted: {channel_config.name}",
            f"YT: {result['posted_yt']}, TT: {result['posted_tt']}, Errors: {result['errors']}",
            color="success" if result["errors"] == 0 else "warning",
        )

    return result


def run_shorts_posting_for_all(pipeline_config, settings) -> dict:
    """Post due Shorts for all channels with shorts enabled.

    Called by the scheduler at posting times.
    """
    results = {}
    for ch in pipeline_config.channels:
        # Check if channel has YouTube credentials
        youtube_service = None
        if ch.client_secrets and ch.oauth_token:
            try:
                from peanut_reacts.upload.youtube_auth import get_authenticated_service
                token_path = Path(ch.oauth_token).expanduser()
                if token_path.exists() and Path(ch.client_secrets).exists():
                    youtube_service = get_authenticated_service(
                        Path(ch.client_secrets),
                        token_path=token_path,
                    )
            except Exception as e:
                log.warning("[Shorts] Auth failed for %s: %s", ch.id, e)

        result = post_due_shorts(
            channel_config=ch,
            settings=settings,
            youtube_service=youtube_service,
            tiktok_uploader=None,  # TikTok requires manual cookie setup first
        )
        results[ch.id] = result

    return results
