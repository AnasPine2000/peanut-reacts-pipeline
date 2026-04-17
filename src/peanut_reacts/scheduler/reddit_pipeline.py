"""Reddit Stories pipeline — Cashew narrates top Reddit posts.

Flow:
1. Scrape top posts from subreddits (PRAW or JSON API)
2. LLM rewrites into narration script with Cashew commentary
3. Edge TTS generates voice
4. FFmpeg composites: background gameplay + subtitles + character overlay
5. Upload to YouTube + extract Shorts

Produces 10-15 min videos with 3-4 stories each.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

SUBREDDITS = ["AmItheAsshole", "tifu", "MaliciousCompliance", "ProRevenge", "relationship_advice"]


def fetch_top_stories(subreddit: str, min_score: int = 3000, limit: int = 10) -> list[dict]:
    """Fetch top stories from a subreddit.

    Prefers the authenticated OAuth endpoint (via PRAW) when REDDIT_CLIENT_ID
    and REDDIT_CLIENT_SECRET are set — the public www.reddit.com/*.json path
    blocks most datacenter/cloud IPs (Hetzner, AWS, GCP) with HTTP 403.
    Falls back to unauthenticated JSON when running from a residential IP.

    Register an app at https://www.reddit.com/prefs/apps (type: "script") to
    get a client_id/secret — no user login required for read-only access.
    """
    import os
    client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    user_agent = os.environ.get(
        "REDDIT_USER_AGENT", "peanut-reacts-bot/1.0 by /u/peanut-reacts"
    )

    if client_id and client_secret:
        # OAuth path — works from datacenter IPs
        try:
            import praw
            reddit = praw.Reddit(
                client_id=client_id,
                client_secret=client_secret,
                user_agent=user_agent,
                # Read-only: no username/password needed
            )
            reddit.read_only = True
            posts = []
            for post in reddit.subreddit(subreddit).top(time_filter="week", limit=limit):
                if post.score < min_score:
                    continue
                if post.stickied or post.over_18:
                    continue
                if len(post.selftext or "") < 200:
                    continue
                posts.append({
                    "id": post.id,
                    "title": post.title,
                    "text": (post.selftext or "")[:5000],
                    "score": post.score,
                    "subreddit": subreddit,
                    "url": f"https://reddit.com{post.permalink}",
                    "author": str(post.author) if post.author else "[deleted]",
                    "num_comments": post.num_comments,
                })
            log.info("r/%s: %d stories (score >= %d) via OAuth",
                     subreddit, len(posts), min_score)
            return posts
        except Exception as e:
            log.error("Reddit OAuth fetch failed for r/%s: %s", subreddit, e)
            return []

    # Fallback: unauthenticated JSON (works from residential IPs only)
    log.warning(
        "REDDIT_CLIENT_ID/SECRET not set — using public JSON endpoint. "
        "Cloud VPS IPs are often blocked; register an app at "
        "https://www.reddit.com/prefs/apps for reliable access."
    )
    import httpx
    try:
        resp = httpx.get(
            f"https://www.reddit.com/r/{subreddit}/top.json?t=week&limit={limit}",
            headers={"User-Agent": user_agent},
            timeout=30, follow_redirects=True,
        )
        resp.raise_for_status()
        posts = []
        for child in resp.json().get("data", {}).get("children", []):
            post = child["data"]
            if post.get("score", 0) < min_score:
                continue
            if post.get("stickied") or post.get("over_18"):
                continue
            if len(post.get("selftext", "")) < 200:
                continue
            posts.append({
                "id": post["id"],
                "title": post["title"],
                "text": post["selftext"][:5000],
                "score": post.get("score", 0),
                "subreddit": subreddit,
                "url": f"https://reddit.com{post['permalink']}",
                "author": post.get("author", "[deleted]"),
                "num_comments": post.get("num_comments", 0),
            })
        log.info("r/%s: %d stories (score >= %d) via public JSON",
                 subreddit, len(posts), min_score)
        return posts
    except Exception as e:
        log.error("Reddit fetch failed for r/%s: %s", subreddit, e)
        return []


def generate_narration_script(stories: list[dict], llm_provider, character: str = "Cashew") -> str:
    """LLM generates a narration script with character commentary between stories."""
    stories_text = ""
    for i, s in enumerate(stories):
        stories_text += f"\n--- STORY {i+1}: r/{s['subreddit']} (score: {s['score']}) ---\n"
        stories_text += f"Title: {s['title']}\n"
        stories_text += f"{s['text'][:2000]}\n"

    prompt = (
        f"You are {character}, a sarcastic narrator with dry humor. "
        f"Rewrite these Reddit stories into an engaging YouTube narration script.\n\n"
        f"Rules:\n"
        f"- Start with a hook: \"Oh, THIS should be good...\"\n"
        f"- Between each story, add {character}'s commentary (2-3 sentences, sarcastic)\n"
        f"- Paraphrase the story (don't copy word-for-word)\n"
        f"- Add dramatic pauses marked as [PAUSE]\n"
        f"- Add emotion cues in brackets: [shocked], [laughing], [sarcastic], [serious]\n"
        f"- End with a verdict and call to action\n"
        f"- Total script should take about 10-12 minutes to read\n\n"
        f"Stories:\n{stories_text}\n\n"
        f"Write the full narration script:"
    )

    try:
        response = llm_provider.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.8, max_tokens=3000,
        )
        return response.strip()
    except Exception as e:
        log.error("Script generation failed: %s", e)
        return ""


def script_to_tts(script: str, output_dir: Path, voice: str = "en-US-ChristopherNeural",
                  rate: str = "+0%") -> list[Path]:
    """Convert narration script to TTS audio files, split by paragraphs."""
    from peanut_reacts.character.tts import EdgeTTSEngine, TTSConfig

    output_dir.mkdir(parents=True, exist_ok=True)
    tts = EdgeTTSEngine(TTSConfig(voice=voice, rate=rate))

    # Split script into chunks (by paragraph or emotion cue)
    # Remove emotion cues for TTS but note them
    clean = re.sub(r'\[PAUSE\]', '.  ', script)
    clean = re.sub(r'\[(shocked|laughing|sarcastic|serious|excited|confused)\]', '', clean)
    paragraphs = [p.strip() for p in clean.split('\n\n') if p.strip() and len(p.strip()) > 20]

    audio_files = []
    for i, para in enumerate(paragraphs):
        out = output_dir / f"narration_{i:03d}.mp3"
        if not out.exists():
            try:
                asyncio.run(tts.synthesize(para, out))
            except Exception as e:
                log.warning("TTS chunk %d failed: %s", i, e)
                continue
        audio_files.append(out)

    log.info("Generated %d TTS chunks from script", len(audio_files))
    return audio_files


def concat_audio(audio_files: list[Path], output: Path) -> Optional[Path]:
    """Concatenate TTS audio files with short gaps."""
    if not audio_files:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)

    list_file = output.parent / "audio_concat.txt"
    list_file.write_text(
        "\n".join(f"file '{f.resolve().as_posix()}'" for f in audio_files),
        encoding="utf-8",
    )
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c:a", "libmp3lame", "-b:a", "192k",
            str(output),
        ], check=True, capture_output=True, timeout=300)
        list_file.unlink(missing_ok=True)
        return output
    except Exception as e:
        log.error("Audio concat failed: %s", e)
        return None


def get_background_gameplay(duration_seconds: float, output: Path) -> Optional[Path]:
    """Get or generate background gameplay footage (Minecraft parkour / satisfying)."""
    if output.exists():
        return output
    output.parent.mkdir(parents=True, exist_ok=True)

    # Generate a simple animated gradient background as fallback
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"color=c=#1a1a2e:s=1920x1080:d={duration_seconds},format=yuv420p",
            "-vf", (
                "drawtext=text='r/ Stories':fontcolor=white@0.3:fontsize=120:"
                "x=(w-text_w)/2:y=(h-text_h)/2:font=Arial"
            ),
            "-c:v", "libx264", "-preset", "veryfast", "-t", str(duration_seconds),
            str(output),
        ], check=True, capture_output=True, timeout=600)
        return output
    except Exception as e:
        log.error("Background gen failed: %s", e)
        return None


def composite_reddit_video(
    audio: Path,
    background: Path,
    output: Path,
    title_text: str = "",
) -> Optional[Path]:
    """Composite final Reddit story video: background + audio + title overlay."""
    output.parent.mkdir(parents=True, exist_ok=True)

    # Get audio duration
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
        capture_output=True, text=True, timeout=10,
    )
    duration = float(r.stdout.strip()) if r.stdout.strip() else 600

    encoder = "h264_nvenc" if _nvenc() else "libx264"
    preset = "fast" if "nvenc" in encoder else "veryfast"

    # Escape title for FFmpeg
    safe_title = title_text.replace("'", "").replace(":", " -")[:60]

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(background),
            "-i", str(audio),
            "-vf", (
                f"drawtext=text='{safe_title}':"
                f"fontcolor=white:fontsize=48:box=1:boxcolor=black@0.6:boxborderw=16:"
                f"x=(w-text_w)/2:y=50:font=Arial:enable='between(t,0,5)'"
            ),
            "-c:v", encoder, "-preset", preset,
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-t", str(duration),
            "-movflags", "+faststart",
            str(output),
        ], check=True, capture_output=True, timeout=1800)
        log.info("Reddit video: %s (%.1f min)", output.name, duration / 60)
        return output
    except Exception as e:
        log.error("Composite failed: %s", e)
        return None


def _nvenc():
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10)
        return "h264_nvenc" in r.stdout
    except:
        return False


def run_reddit_pipeline(
    output_dir: Path,
    llm_provider,
    subreddits: list[str] = None,
    stories_per_video: int = 3,
    min_score: int = 3000,
    voice: str = "en-US-ChristopherNeural",
    rate: str = "+0%",
    character: str = "Cashew",
    upload_service=None,
    upload_privacy: str = "public",
    tags: list[str] = None,
) -> dict:
    """Full Reddit stories pipeline. Returns {video_path, youtube_url, shorts_count}."""
    output_dir.mkdir(parents=True, exist_ok=True)
    subreddits = subreddits or SUBREDDITS
    result = {"video_path": None, "youtube_url": None, "errors": []}

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M")

    # Step 1: Fetch stories
    log.info("[Reddit] Fetching stories from %d subreddits...", len(subreddits))
    all_stories = []
    for sub in subreddits:
        stories = fetch_top_stories(sub, min_score=min_score, limit=5)
        all_stories.extend(stories)

    if not all_stories:
        log.warning("[Reddit] No stories found")
        result["errors"].append("No stories found")
        return result

    # Pick best stories
    all_stories.sort(key=lambda s: s["score"], reverse=True)
    selected = all_stories[:stories_per_video]
    log.info("[Reddit] Selected %d stories (top scores: %s)",
             len(selected), [s["score"] for s in selected])

    # Step 2: Generate script
    log.info("[Reddit] Generating narration script...")
    script = generate_narration_script(selected, llm_provider, character)
    if not script:
        result["errors"].append("Script generation failed")
        return result

    script_path = output_dir / f"script_{timestamp}.txt"
    script_path.write_text(script, encoding="utf-8")

    # Step 3: TTS
    log.info("[Reddit] Running TTS...")
    tts_dir = output_dir / f"tts_{timestamp}"
    audio_files = script_to_tts(script, tts_dir, voice=voice, rate=rate)
    if not audio_files:
        result["errors"].append("TTS failed")
        return result

    # Step 4: Concat audio
    full_audio = output_dir / f"audio_{timestamp}.mp3"
    audio = concat_audio(audio_files, full_audio)
    if not audio:
        result["errors"].append("Audio concat failed")
        return result

    # Step 5: Background
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
        capture_output=True, text=True, timeout=10,
    )
    duration = float(r.stdout.strip()) if r.stdout.strip() else 600

    bg = output_dir / "background.mp4"
    background = get_background_gameplay(duration, bg)

    # Step 6: Composite
    log.info("[Reddit] Compositing video...")
    title_parts = [s["title"][:30] for s in selected[:2]]
    video_title = f"{character} Reads: {' | '.join(title_parts)}"

    final = output_dir / f"reddit_{timestamp}_final.mp4"
    video = composite_reddit_video(audio, background, final, video_title)
    if not video:
        result["errors"].append("Composite failed")
        return result

    result["video_path"] = str(video)
    log.info("[Reddit] Video ready: %s (%.1f min)", video.name, duration / 60)

    # Step 7: Upload
    if upload_service:
        try:
            from peanut_reacts.upload.youtube_upload import YouTubeUploader, UploadMetadata
            uploader = YouTubeUploader(upload_service)

            subreddit_tags = list(set(s["subreddit"].lower() for s in selected))
            yt_title = f"{character.upper()} READS THE MOST INSANE REDDIT STORIES | r/{selected[0]['subreddit']}"

            meta = UploadMetadata(
                title=yt_title[:100],
                description=(
                    f"{character} narrates the wildest Reddit stories this week!\n\n"
                    f"Stories from: {', '.join(f'r/{s}' for s in subreddit_tags)}\n\n"
                    f"Subscribe for daily Reddit stories!\n\n"
                    f"#reddit #stories #{'#'.join(subreddit_tags[:3])} #narration #storytime"
                ),
                tags=(tags or []) + subreddit_tags + ["reddit", "stories", "narration"],
                category_id="24",
                privacy_status=upload_privacy,
            )
            up_result = uploader.upload_video(video, meta)
            result["youtube_url"] = up_result.url
            log.info("[Reddit] Uploaded: %s", up_result.url)
        except Exception as e:
            log.error("[Reddit] Upload failed: %s", e)
            result["errors"].append(f"Upload: {e}")

    return result
