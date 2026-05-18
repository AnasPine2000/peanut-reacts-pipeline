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


def _parse_reddit_listing(data: dict, subreddit: str, min_score: int) -> list[dict]:
    """Shared shape parser for Reddit listing JSON (used by both Scrape.do
    and the direct-httpx fallback)."""
    posts = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
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
            "url": f"https://reddit.com{post.get('permalink', '')}",
            "author": post.get("author", "[deleted]"),
            "num_comments": post.get("num_comments", 0),
        })
    return posts


def fetch_top_stories(subreddit: str, min_score: int = 3000, limit: int = 10) -> list[dict]:
    """Fetch top stories from a subreddit.

    Resolution order:
      1. Scrape.do managed proxy (SCRAPEDO_API_TOKEN) — residential IP
         rotation, works from any origin including blocked datacenter IPs.
         Preferred when the env var is set.
      2. PRAW / Reddit OAuth (REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET) —
         authenticated endpoint, works from datacenter IPs.
      3. Direct httpx against www.reddit.com/*.json — only usable from
         residential IPs; Reddit 403s most clouds.
    """
    import os
    import urllib.parse
    import httpx

    scrapedo_token = os.environ.get("SCRAPEDO_API_TOKEN", "").strip()
    client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    user_agent = os.environ.get(
        "REDDIT_USER_AGENT", "peanut-reacts-bot/1.0 by /u/peanut-reacts"
    )

    # ── 1. Scrape.do managed proxy ─────────────────────────────────────
    if scrapedo_token:
        target_url = (
            f"https://www.reddit.com/r/{subreddit}/top.json"
            f"?t=week&limit={limit}"
        )
        proxy_url = (
            "https://api.scrape.do/?"
            f"token={scrapedo_token}"
            f"&url={urllib.parse.quote(target_url, safe='')}"
        )
        try:
            resp = httpx.get(proxy_url, timeout=60, follow_redirects=True)
            resp.raise_for_status()
            posts = _parse_reddit_listing(resp.json(), subreddit, min_score)
            log.info("r/%s: %d stories (score >= %d) via Scrape.do",
                     subreddit, len(posts), min_score)
            return posts
        except Exception as e:
            log.error("Scrape.do fetch failed for r/%s: %s", subreddit, e)
            # Fall through to next option

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
                  rate: str = "+0%") -> list[dict]:
    """Convert narration script to TTS audio + word timings, split by paragraphs.

    Returns a list of dicts: [{audio_path, duration, words: [(start, end, text), ...]}]
    where `words` timing is relative to the start of that chunk. Edge TTS gives
    us word-level offsets for free — we capture them here so the compositor
    can burn TikTok-style karaoke subtitles.
    """
    from peanut_reacts.character.tts import EdgeTTSEngine, TTSConfig

    output_dir.mkdir(parents=True, exist_ok=True)
    tts = EdgeTTSEngine(TTSConfig(voice=voice, rate=rate))

    # Split script into chunks (by paragraph or emotion cue)
    # Remove emotion cues for TTS but note them
    clean = re.sub(r'\[PAUSE\]', '.  ', script)
    clean = re.sub(r'\[(shocked|laughing|sarcastic|serious|excited|confused)\]', '', clean)
    paragraphs = [p.strip() for p in clean.split('\n\n') if p.strip() and len(p.strip()) > 20]

    chunks = []
    for i, para in enumerate(paragraphs):
        out = output_dir / f"narration_{i:03d}.mp3"
        try:
            if out.exists():
                # Cached audio — re-synthesize just to recover word timings (fast)
                out.unlink()
            result = asyncio.run(tts.synthesize(para, out))
        except Exception as e:
            log.warning("TTS chunk %d failed: %s", i, e)
            continue

        words = [
            (wt.offset_ms / 1000.0,
             (wt.offset_ms + wt.duration_ms) / 1000.0,
             wt.text)
            for wt in result.word_timings
        ]
        chunks.append({
            "audio_path": out,
            "duration": result.duration,
            "words": words,
        })

    log.info("Generated %d TTS chunks from script (%d total words)",
             len(chunks), sum(len(c["words"]) for c in chunks))
    return chunks


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


def _format_ass_time(seconds: float) -> str:
    """ASS time format: H:MM:SS.cs (centiseconds)."""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def build_tiktok_ass(
    chunks: list[dict],
    output_path: Path,
    *,
    words_per_line: int = 3,
    video_w: int = 1920,
    video_h: int = 1080,
    font_name: str = "DejaVu Sans",
    font_size: int = 96,
    margin_v: int = 380,
) -> Path:
    """Generate an ASS subtitle file with TikTok-style karaoke chunking.

    Consumes the list of TTS chunks produced by `script_to_tts()` and walks
    through them with a cumulative offset (since each chunk's word timings
    are 0-based local to that chunk). Groups `words_per_line` consecutive
    words into one event so the viewer sees a short, punchy line at a time
    rather than a wall of text.

    Styling: huge bold sans-serif, white fill, black outline, pop-in fade.
    Matches the dominant TikTok AITA/Reddit reader aesthetic. Edit the
    style line directly if you want yellow/neon/two-tone instead.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ASS colors are &HAABBGGRR (alpha inverted: 00 = opaque).
    primary_color = "&H00FFFFFF"   # white fill
    outline_color = "&H00000000"   # black outline
    shadow_color = "&H64000000"    # black with ~39% opacity

    header = f"""[Script Info]
Title: Reddit Story Subtitles
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTok,{font_name},{font_size},{primary_color},{primary_color},{outline_color},{shadow_color},1,0,0,0,100,100,0,0,1,8,3,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []
    offset = 0.0   # cumulative seconds into the final concat audio

    # Edge TTS's WordBoundary events are phrase-level for many voices
    # (e.g. ChristopherNeural). Explode each entry to true word granularity
    # so the karaoke chunks are readable 3-word flashes, not paragraphs.
    def _explode_to_words(entries):
        out = []
        for start, end, text in entries:
            words_in_entry = text.split()
            if not words_in_entry:
                continue
            dur = max(end - start, 0.01)
            per = dur / len(words_in_entry)
            for i, w in enumerate(words_in_entry):
                out.append((start + i * per, start + (i + 1) * per, w))
        return out

    for chunk in chunks:
        words = _explode_to_words(chunk["words"])
        if not words:
            offset += chunk["duration"]
            continue

        # Group consecutive words into lines of `words_per_line`
        for i in range(0, len(words), words_per_line):
            group = words[i:i + words_per_line]
            start_sec = offset + group[0][0]
            end_sec = offset + group[-1][1]
            # Clean and upper-case for TikTok pop aesthetic (optional — comment out for mixed case)
            line_text = " ".join(w[2] for w in group).strip()
            # Escape ASS special chars in the visible text
            line_text = (
                line_text.replace("\\", "\\\\")
                .replace("{", "\\{").replace("}", "\\}")
                .replace(",", "\u002C")   # ASS treats bare commas oddly
            )
            # \fad(in_ms, out_ms) = pop-in / fade-out for bounciness
            text_with_fx = f"{{\\fad(80,60)}}{line_text}"
            events.append(
                f"Dialogue: 0,{_format_ass_time(start_sec)},"
                f"{_format_ass_time(end_sec)},TikTok,,0,0,0,,{text_with_fx}"
            )

        offset += chunk["duration"]

    output_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    log.info("ASS subtitles: %s (%d lines, %.1fs total)",
             output_path.name, len(events), offset)
    return output_path


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
    subtitles_ass: Optional[Path] = None,
) -> Optional[Path]:
    """Composite final Reddit story video: background + audio + title + subs.

    If `subtitles_ass` is provided, the ASS file is burned into the frame via
    ffmpeg's `subtitles=` filter (libass). The ASS file should be produced by
    `build_tiktok_ass()` and uses its own styling — the subtitles filter just
    renders it, no extra style args needed.
    """
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

    # Build the video filter chain. Order: title drawtext first, subs on top.
    vf_parts = [
        f"drawtext=text='{safe_title}':"
        f"fontcolor=white:fontsize=48:box=1:boxcolor=black@0.6:boxborderw=16:"
        f"x=(w-text_w)/2:y=50:font=Arial:enable='between(t,0,5)'"
    ]
    if subtitles_ass and Path(subtitles_ass).exists():
        # libass needs forward slashes in the path; also escape the colon after
        # drive letter on Windows (subtitles filter parses ':' as separator).
        ass_path = str(Path(subtitles_ass).resolve()).replace("\\", "/")
        if len(ass_path) > 1 and ass_path[1] == ":":
            ass_path = ass_path[0] + "\\:" + ass_path[2:]
        vf_parts.append(f"subtitles='{ass_path}'")

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(background),
            "-i", str(audio),
            "-vf", ",".join(vf_parts),
            "-c:v", encoder, "-preset", preset,
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-t", str(duration),
            "-movflags", "+faststart",
            str(output),
        ], check=True, capture_output=True, timeout=1800)
        log.info("Reddit video: %s (%.1f min%s)",
                 output.name, duration / 60,
                 ", with TikTok subs" if subtitles_ass else "")
        return output
    except Exception as e:
        log.error("Composite failed: %s", e)
        return None


def _nvenc():
    """Delegate to canonical check that actually tests nvenc, not just compile-in."""
    from peanut_reacts.core.ffmpeg import nvenc_available
    return nvenc_available()


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
    tiktok_cookies: str = "",
    narrator_avatar: str = "",
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

    # Step 3: TTS (captures word timings for TikTok-style subtitles)
    log.info("[Reddit] Running TTS...")
    tts_dir = output_dir / f"tts_{timestamp}"
    tts_chunks = script_to_tts(script, tts_dir, voice=voice, rate=rate)
    if not tts_chunks:
        result["errors"].append("TTS failed")
        return result
    audio_files = [c["audio_path"] for c in tts_chunks]

    # Step 4: Concat audio
    full_audio = output_dir / f"audio_{timestamp}.mp3"
    audio = concat_audio(audio_files, full_audio)
    if not audio:
        result["errors"].append("Audio concat failed")
        return result

    # Step 4b: Build TikTok-style karaoke ASS from captured word timings
    subs_ass = output_dir / f"subs_{timestamp}.ass"
    try:
        build_tiktok_ass(tts_chunks, subs_ass, words_per_line=3)
    except Exception as e:
        log.warning("[Reddit] ASS build failed (continuing without subs): %s", e)
        subs_ass = None

    # Step 5: Background
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
        capture_output=True, text=True, timeout=10,
    )
    duration = float(r.stdout.strip()) if r.stdout.strip() else 600

    bg = output_dir / "background.mp4"
    background = get_background_gameplay(duration, bg)

    # Step 6: Composite with burned-in subs
    log.info("[Reddit] Compositing video%s...",
             " with TikTok subs" if subs_ass else "")
    title_parts = [s["title"][:30] for s in selected[:2]]
    video_title = f"{character} Reads: {' | '.join(title_parts)}"

    final = output_dir / f"reddit_{timestamp}_unpolished.mp4"
    video = composite_reddit_video(audio, background, final, video_title,
                                   subtitles_ass=subs_ass)
    if not video:
        result["errors"].append("Composite failed")
        return result

    # Step 6a: PNGtuber narrator avatar (opt-in via channel.narrator_avatar).
    # Overlays a 2-state lip-flap character driven by the narration
    # audio. Runs BEFORE the polish layer so the avatar is part of the
    # main content; intro/outro cards wrap around it cleanly. Failure
    # is non-fatal — the faceless video ships if the avatar can't render.
    if narrator_avatar:
        avatar_dir = Path(narrator_avatar).expanduser()
        closed = avatar_dir / "narrator_closed.png"
        opened = avatar_dir / "narrator_open.png"
        if closed.exists() and opened.exists():
            try:
                from peanut_reacts.character.pngtuber import (
                    PngTuberStyle, add_narrator_avatar,
                )
                style = PngTuberStyle(
                    closed_sprite=closed,
                    open_sprite=opened,
                    position="bottom-left",
                    size_frac=0.26,
                )
                avatared = output_dir / f"reddit_{timestamp}_avatar.mp4"
                res = add_narrator_avatar(video, audio, style, avatared)
                if res and res.exists():
                    log.info("[Reddit] Narrator avatar composited: %s", res.name)
                    video = res
                else:
                    log.info("[Reddit] Avatar skipped/failed — faceless video kept")
            except Exception as e:
                log.warning("[Reddit] Avatar crashed (non-fatal): %s", e)
        else:
            log.warning(
                "[Reddit] narrator_avatar set but sprites missing in %s "
                "(need narrator_closed.png + narrator_open.png)", avatar_dir,
            )

    # Step 6b: Polish layer (SFX + intro + outro). Failure here falls
    # back to the unpolished video so the pipeline always has an
    # output. Adds ~30-40 s on a CPU runner (one re-encode for SFX
    # mix + one re-encode for intro/outro concat).
    try:
        from peanut_reacts.character.video_polish import (
            VideoStyle, apply_full_polish,
        )
        polished_path = output_dir / f"reddit_{timestamp}_final.mp4"
        style = VideoStyle(
            title="REDDIT STORIES",
            subtitle=f"narrated by {character}",
            character=character,
            resolution=(1920, 1080),
            fps=30,
            use_nvenc=_nvenc(),
        )
        polished = apply_full_polish(
            video, polished_path, style,
            raw_script=script,                     # enables emotion-cue SFX
            num_stories=len(selected),             # story-transition whooshes
            work_dir=output_dir,
        )
        if polished and polished.exists() and polished != video:
            log.info("[Reddit] Polished with SFX + intro + outro: %s",
                     polished.name)
            video = polished
        else:
            log.info("[Reddit] Polish layer skipped/partial — using best available")
            if polished and polished != video:
                video = polished
    except Exception as e:
        log.warning("[Reddit] Polish crashed (non-fatal): %s", e)

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

    # Step 8 (optional): TikTok cross-post. Opt-in per channel via
    # channels.yaml `tiktok_cookies:` field. Runs AFTER YouTube (so a
    # flaky TikTok never blocks the primary upload) and its failure is
    # logged as a warning, never added to result["errors"]. The
    # downstream pipeline contract is: "YouTube success = overall
    # success"; TikTok is a bonus.
    if tiktok_cookies and result.get("youtube_url"):
        tiktok_path = Path(tiktok_cookies).expanduser()
        if not tiktok_path.exists():
            log.warning(
                "[Reddit] TikTok cookies configured but file missing: %s "
                "— skipping cross-post. Re-export cookies to enable.",
                tiktok_path,
            )
        else:
            try:
                from peanut_reacts.upload.tiktok_upload import (
                    TikTokMetadata, TikTokUploader,
                )
                # Build a short TikTok-optimized caption. Full YouTube
                # description is way too long for TikTok — viewers tap
                # away when a caption doesn't have an immediate hook.
                hook_story = selected[0] if selected else {}
                hook_title = (hook_story.get("title") or "Reddit drama").strip()
                # First 80 chars of the top story title — already a
                # clickbait hook (that's why the post got 19k upvotes).
                tt_caption = f"😱 {hook_title[:80]}"
                tt_hashtags = [
                    "reddit", "redditstories", "storytime", "fyp",
                    character.lower(),
                ] + subreddit_tags[:2]
                tt_meta = TikTokMetadata(
                    caption=tt_caption,
                    hashtags=tt_hashtags,
                    privacy="public",
                )
                log.info("[Reddit] Cross-posting to TikTok...")
                tt_res = TikTokUploader(cookies_path=tiktok_path).upload(
                    video, tt_meta,
                )
                if tt_res.success:
                    result["tiktok_url"] = tt_res.url
                    log.info("[Reddit] TikTok upload OK: %s",
                             tt_res.url or "(URL not captured)")
                else:
                    log.warning("[Reddit] TikTok upload failed (non-fatal): %s",
                                tt_res.error)
            except Exception as e:
                log.warning("[Reddit] TikTok cross-post crashed (non-fatal): %s", e)

    return result
