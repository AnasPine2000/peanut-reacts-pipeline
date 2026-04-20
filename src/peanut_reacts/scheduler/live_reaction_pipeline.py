"""Live reaction pipeline — Peanut watches a FULL source video and reacts
at intervals, H3H3 / WillNE / Daz Games style.

This is structurally different from the TNTL pipeline:

  TNTL                                   LIVE REACTION
  ----                                   -------------
  Chop source into 15-18 short clips     Keep source intact (20-30min)
  Peanut PIP during each clip            Peanut PIP throughout episode
  One verdict per clip (at end of clip)  ~15-25 interjections spread out
  Score counter per clip                 No score mechanic
  Output: ~10min clips+verdicts          Output: source_length + intro/outro
  Source audio ducked + verdict overlay  Source plays at full volume
    per-clip                             EXCEPT for a brief duck at each
                                         reaction beat (2-4s window)

Use case: Peanut reacts to a full Sidemen Among Us game (or any
long-form content where clipping would destroy the joke — Among Us
meetings, reddit story arcs, streamer segments where timing matters).

Key sub-steps:

  1. Download ONE full video from the channel's source_urls pool
  2. Compute N reaction trigger points (evenly spaced, skipping intros)
  3. At each trigger: extract frames -> Groq vision describe -> DeepSeek
     verdict (reusing the same persona + history mechanism from TNTL)
  4. Synthesize TTS (ElevenLabs -> Edge TTS fallback)
  5. Build a facecam timeline that spans the FULL source duration —
     idle clip loop most of the time, emotion clips at each reaction
     window
  6. Composite: source video (full audio + video) + chroma-keyed
     Peanut PIP overlay + audio mix where source is ducked during each
     reaction window and verdict TTS is amixed in at the correct times
  7. Prepend intro, append outro, upload

Reuses existing helpers:
  - source_playlist_clips (for the playlist-listing logic — but we use
    a different download path that keeps the full video)
  - describe_clip / generate_verdict from clip_context
  - dynamic_facecam.build_facecam_timeline + render_dynamic_facecam
  - elevenlabs_tts / edge_tts for verdict voice
  - thumbnail_tntl for the episode thumbnail
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Themed fallback "descriptions" used when the Groq vision layer can't
# return a real description (quota exhausted, network flake, tiny/short
# clip, etc.). The v3 smoke revealed that when every failed call returns
# the SAME fallback string ("A funny clip (vision API unreachable)"),
# DeepSeek anchors on that string and produces dozens of near-identical
# meta-verdicts about "vision being gone".
#
# Fix: rotate through a pool of Among-Us-specific beat descriptions
# indexed by clip position. DeepSeek gets enough variety in the
# incoming anchor to produce distinct, on-theme verdicts even with
# zero actual vision signal. Degraded mode but shipping-ready.
GENERIC_AMONG_US_BEATS = [
    "Sidemen Among Us — crewmates scatter to tasks as an impostor lurks",
    "Sidemen Among Us — an emergency meeting is called, accusations fly",
    "Sidemen Among Us — a body has been reported, chaos in discussion",
    "Sidemen Among Us — someone vents into the shadows and disappears",
    "Sidemen Among Us — the lights get sabotaged, everyone panics",
    "Sidemen Among Us — a crewmate finishes a task with triumphant flair",
    "Sidemen Among Us — the final vote is cast, ejection incoming",
    "Sidemen Among Us — a 4D-chess galaxy-brain accusation unfolds",
    "Sidemen Among Us — tense silence before someone makes a desperate move",
    "Sidemen Among Us — proximity chat erupts in screaming and laughter",
    "Sidemen Among Us — a perfectly timed self-report saves the impostor",
    "Sidemen Among Us — the impostor's mask is about to slip badly",
    "Sidemen Among Us — someone runs past a cooling body entirely unaware",
    "Sidemen Among Us — a clutch double-kill at the reactor, unseen",
    "Sidemen Among Us — the wrong person is about to get ejected",
    "Sidemen Among Us — a poorly-faked task betrays the impostor at once",
]


def _generic_description_for_index(idx: int) -> str:
    """Return a rotating themed fallback — keeps DeepSeek from anchoring."""
    return GENERIC_AMONG_US_BEATS[idx % len(GENERIC_AMONG_US_BEATS)]


def _is_vision_fallback(desc: str) -> bool:
    """Detect whether describe_clip returned a generic fallback string."""
    if not desc:
        return True
    lower = desc.lower()
    return (
        "vision api unreachable" in lower
        or "no vision api" in lower
        or "couldn't see" in lower
        or "very short clip" in lower
        or desc.startswith("A funny clip")
    )


# Tuning knobs
REACTION_INTERVAL_SECONDS = 55      # react roughly once a minute
REACTION_WINDOW_RADIUS = 2          # sample frames +/- 2s around trigger
DUCK_SOURCE_VOLUME = 0.22           # source drops to 22% during verdicts
VERDICT_AUDIO_BOOST = 1.8           # Peanut's TTS boosted so it cuts through
FACECAM_SIZE = 360                  # PIP size in px
FACECAM_MARGIN = 40                 # PIP margin from bottom-right corner
SOURCE_INTRO_SKIP_SECONDS = 45      # skip first 45s (intro + sponsors)
SOURCE_OUTRO_SKIP_SECONDS = 30      # skip last 30s (outro + end screens)


def _probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    try:
        return float(r.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _ytdlp_cookie_args() -> list[str]:
    """Mirror of the TNTL pipeline helper — find a cookies.txt."""
    candidates = [
        os.environ.get("YTDLP_COOKIES_FILE", ""),
        str(Path.home() / ".peanut_reacts" / "youtube_cookies.txt"),
    ]
    for path in candidates:
        if path and Path(path).exists():
            return ["--cookies", path]
    return []


def download_full_playlist_video(
    playlist_urls: list[str],
    output_dir: Path,
) -> Optional[Path]:
    """Download ONE full video from the pooled playlist URLs.

    Same pooling logic as tntl_pipeline.source_playlist_clips (combine
    all playlists -> filter 10-90min -> pick random video), but keeps
    the video intact instead of chopping into clips.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if not playlist_urls:
        log.error("[LIVE-RX] No playlist URLs provided")
        return None

    cookie_args = _ytdlp_cookie_args()
    if not cookie_args:
        log.warning("[LIVE-RX] No cookies file found — YT may hit bot-check. "
                    "See ~/.peanut_reacts/youtube_cookies.txt.")

    # Enumerate every playlist into one pool
    vids: list[dict] = []
    for url in playlist_urls:
        log.info("[LIVE-RX] Listing playlist: %s", url[:80])
        try:
            result = subprocess.run(
                ["yt-dlp", *cookie_args, "--flat-playlist", "--print",
                 "%(id)s\t%(title)s\t%(duration)s", url],
                capture_output=True, text=True, timeout=90,
            )
        except Exception as e:
            log.warning("[LIVE-RX] Playlist list failed for %s: %s", url[:50], e)
            continue

        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2:
                try:
                    dur = float(parts[2]) if len(parts) > 2 and parts[2] not in ("NA", "") else 0
                except ValueError:
                    dur = 0
                # 10-35min sweet spot for reaction format: long enough
                # for real content, short enough to get ~15-25 reactions
                # without exhausting viewer attention.
                if 600 < dur < 2100:
                    vids.append({
                        "id": parts[0], "title": parts[1], "duration": dur,
                    })

    if not vids:
        log.error("[LIVE-RX] No viable videos found across %d playlist(s) "
                  "(filter: 10-35 min)", len(playlist_urls))
        return None

    log.info("[LIVE-RX] Pooled %d viable videos across %d playlist(s)",
             len(vids), len(playlist_urls))

    vid = random.choice(vids)
    dl_path = output_dir / f"{vid['id']}.mp4"
    if not dl_path.exists():
        log.info("[LIVE-RX] Downloading: %s (%dmin)",
                 vid["title"][:60], int(vid["duration"] / 60))
        try:
            subprocess.run([
                "yt-dlp", *cookie_args,
                "-f", "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/best[height<=1080]",
                "--merge-output-format", "mp4", "-o", str(dl_path),
                f"https://youtube.com/watch?v={vid['id']}",
            ], capture_output=True, timeout=1200)
        except subprocess.TimeoutExpired:
            log.error("[LIVE-RX] Download timed out for %s", vid["id"])
            return None

    if not dl_path.exists():
        log.error("[LIVE-RX] Download produced no file for %s (likely bot-check). "
                  "Check cookies + VPN.", vid["id"])
        return None

    log.info("[LIVE-RX] Downloaded %s -> %s (%d MB)",
             vid["title"][:50], dl_path.name, dl_path.stat().st_size // (1024*1024))
    return dl_path


def compute_reaction_triggers(video_path: Path, interval_s: int = REACTION_INTERVAL_SECONDS) -> list[float]:
    """Return a list of timestamps (in seconds) where Peanut should react.

    Evenly spaced across the usable body of the video (skipping intro +
    outro zones). With a 20min video at default 55s interval, that's
    ~20 reactions. With a 30min video, ~30 reactions.
    """
    total_dur = _probe_duration(video_path)
    usable_start = SOURCE_INTRO_SKIP_SECONDS
    usable_end = total_dur - SOURCE_OUTRO_SKIP_SECONDS
    if usable_end - usable_start < interval_s * 5:
        # Too short — shrink the margins
        usable_start = min(15, total_dur * 0.05)
        usable_end = total_dur - min(15, total_dur * 0.05)

    triggers = []
    t = usable_start + interval_s * 0.5   # first reaction at half-interval
    while t < usable_end:
        triggers.append(t)
        t += interval_s

    log.info("[LIVE-RX] %d reaction triggers across %.1fmin video (usable %.1fs-%.1fs)",
             len(triggers), total_dur / 60, usable_start, usable_end)
    return triggers


def extract_window_clip(video_path: Path, t: float, output_path: Path,
                       duration: float = 5.0) -> Optional[Path]:
    """Extract a small clip centered on t for vision analysis.

    We reuse clip_context.describe_clip which wants a full clip file
    (it extracts 3 frames internally). So we just create a short MP4
    covering the reaction window.
    """
    start = max(0.0, t - duration / 2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(video_path),
            "-t", f"{duration:.2f}", "-c:v", "libx264", "-preset", "veryfast",
            "-an", str(output_path),
        ], check=True, capture_output=True, timeout=60)
        return output_path if output_path.exists() else None
    except subprocess.CalledProcessError:
        return None


def generate_reactions_for_episode(
    video_path: Path,
    triggers: list[float],
    work_dir: Path,
) -> list[dict]:
    """For each trigger time, describe + verdict. Returns per-reaction dicts.

    Each dict has: {time, description, verdict_text, emotion, intensity, intensity}.
    """
    from peanut_reacts.character.clip_context import describe_clip, generate_verdict

    work_dir.mkdir(parents=True, exist_ok=True)
    reactions = []
    history: list[str] = []

    for i, t in enumerate(triggers):
        # Extract a 5s window around this trigger for vision
        window_clip = work_dir / f"window_{i:03d}.mp4"
        extracted = extract_window_clip(video_path, t, window_clip, duration=5.0)
        if not extracted:
            log.warning("[LIVE-RX] Skipping trigger %d at t=%.1fs (extract failed)", i, t)
            continue

        desc = describe_clip(window_clip)
        # Substitute the boring generic fallback with a rotating themed
        # beat so DeepSeek gets varied anchors even when vision is blind.
        # Log the substitution so we know the episode ran degraded.
        if _is_vision_fallback(desc):
            original_fallback = desc
            desc = _generic_description_for_index(i)
            log.info("[LIVE-RX] Trigger %d: vision fallback (%r) -> themed beat %r",
                     i, original_fallback[:50], desc[:60])

        verdict = generate_verdict(desc, history=history)
        history.append(verdict.text)

        reactions.append({
            "index": i,
            "time": t,
            "description": desc,
            "verdict_text": verdict.text,
            "outcome": verdict.outcome,
            "emotion": verdict.emotion,
            "intensity": verdict.intensity,
        })

        # Cleanup the window clip — we only needed it for the vision pass
        try:
            window_clip.unlink(missing_ok=True)
        except Exception:
            pass

    log.info("[LIVE-RX] Generated %d/%d reactions", len(reactions), len(triggers))
    return reactions


def _synthesize_verdict_tts(text: str, out_path: Path, emotion: str,
                            edge_voice: str, edge_rate: str) -> bool:
    """ElevenLabs with Edge TTS fallback. Returns True on success."""
    try:
        from peanut_reacts.character.elevenlabs_tts import (
            ElevenLabsTTSEngine, ElevenLabsConfig,
        )
        if not os.environ.get("ELEVENLABS_API_KEY"):
            raise RuntimeError("ELEVENLABS_API_KEY missing")
        engine = ElevenLabsTTSEngine(ElevenLabsConfig(
            voice_id="JBFqnCBsd6RMkjVDRZzb",   # British "George" narrator
            model_id="eleven_multilingual_v2",
        ))
        el_emotion = {
            "laughing": "amused", "facepalm": "sarcastic",
            "scared": "shocked", "celebrating": "excited",
            "idle": "neutral",
        }.get(emotion, emotion if emotion in {
            "excited", "shocked", "amused", "sarcastic", "confused", "neutral"
        } else "neutral")
        engine.synthesize(text, out_path, emotion=el_emotion)
        return out_path.exists()
    except Exception as e:
        log.warning("[LIVE-RX] ElevenLabs failed (%s); falling back to Edge TTS",
                    str(e)[:120])

    # Edge TTS fallback
    try:
        from peanut_reacts.character.tts import EdgeTTSEngine, TTSConfig
        engine = EdgeTTSEngine(TTSConfig(voice=edge_voice, rate=edge_rate))
        asyncio.run(engine.synthesize(text, out_path))
        return out_path.exists()
    except Exception as e:
        log.error("[LIVE-RX] Both TTS engines failed: %s", e)
        return False


def composite_live_reaction_episode(
    source_video: Path,
    facecam_track: Path,
    reactions_with_tts: list[dict],
    output_path: Path,
    facecam_size: int = FACECAM_SIZE,
    margin: int = FACECAM_MARGIN,
) -> Optional[Path]:
    """Single ffmpeg pass: source video + Peanut PIP overlay + mixed audio.

    Audio mix:
      - Source audio plays at full volume by default
      - Ducked to DUCK_SOURCE_VOLUME during each reaction's window
        (reaction start -> reaction start + tts duration + 0.3s)
      - Verdict TTS overlaid at its trigger time, boosted VERDICT_AUDIO_BOOST

    Video:
      - Source plays full-frame
      - Peanut facecam chroma-keyed (teal AI bg 0x008C52) and composited
        into the bottom-right corner at facecam_size
    """
    if not source_video.exists() or not facecam_track.exists():
        log.error("[LIVE-RX] Missing inputs for composite")
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Video overlay (chromakey + PIP)
    video_filter = (
        f"[1:v]chromakey=0x008C52:0.20:0.12,"
        f"scale={facecam_size}:{facecam_size}[fc];"
        f"[0:v][fc]overlay=W-w-{margin}:H-h-{margin}[vout]"
    )

    # Audio: source ducked via enable-gated volume, then amixed with each TTS
    duck_windows = "+".join([
        f"between(t,{r['time']:.3f},{r['time'] + r['tts_duration'] + 0.3:.3f})"
        for r in reactions_with_tts
    ])
    audio_parts = []
    if duck_windows:
        audio_parts.append(
            f"[0:a]volume='{DUCK_SOURCE_VOLUME}':"
            f"enable='{duck_windows}'[src_ducked]"
        )
    else:
        audio_parts.append("[0:a]volume=1.0[src_ducked]")

    tts_labels = []
    for i, r in enumerate(reactions_with_tts):
        # Input index: 0=source, 1=facecam, 2+=TTS files in order
        src_idx = i + 2
        label = f"v{i}"
        tts_labels.append(label)
        delay_ms = int(r["time"] * 1000)
        audio_parts.append(
            f"[{src_idx}:a]adelay={delay_ms}|{delay_ms},volume={VERDICT_AUDIO_BOOST}[{label}]"
        )

    amix_inputs = "[src_ducked]" + "".join(f"[{l}]" for l in tts_labels)
    audio_parts.append(
        f"{amix_inputs}amix=inputs={len(tts_labels) + 1}:"
        f"duration=first:dropout_transition=0[aout]"
    )

    filter_complex = video_filter + ";" + ";".join(audio_parts)

    # Build ffmpeg inputs: source, facecam, then each TTS file
    inputs = ["-i", str(source_video), "-i", str(facecam_track)]
    for r in reactions_with_tts:
        inputs.extend(["-i", str(r["tts_path"])])

    try:
        subprocess.run([
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-shortest",
            str(output_path),
        ], check=True, capture_output=True, timeout=7200)   # 2 hr cap for long episodes
    except subprocess.CalledProcessError as e:
        log.error("[LIVE-RX] Composite failed: %s",
                  e.stderr.decode("utf-8", errors="replace")[-500:])
        return None

    return output_path if output_path.exists() else None


def build_live_reaction_episode(
    source_video: Path,
    output_dir: Path,
    character: str = "Peanut",
    voice: str = "en-GB-RyanNeural",
    rate: str = "+5%",
) -> Optional[Path]:
    """Full live-reaction episode build: vision + verdicts + TTS + composite."""
    from peanut_reacts.character.dynamic_facecam import (
        build_facecam_timeline, render_dynamic_facecam,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "work"
    work_dir.mkdir(exist_ok=True)
    clips_dir = PROJECT_ROOT / "data" / "ai_peanut_clips"

    # Step 1: compute trigger points
    triggers = compute_reaction_triggers(source_video)
    if len(triggers) < 3:
        log.error("[LIVE-RX] Too few triggers (%d); source too short?", len(triggers))
        return None

    # Step 2: vision + verdict per trigger
    log.info("[LIVE-RX] Generating context-aware reactions for %d triggers...",
             len(triggers))
    reactions = generate_reactions_for_episode(source_video, triggers, work_dir)
    if not reactions:
        log.error("[LIVE-RX] No reactions generated")
        return None

    # Step 3: synthesize TTS for each reaction + record duration
    tts_dir = output_dir / "tts"
    tts_dir.mkdir(exist_ok=True)
    reactions_with_tts = []
    for r in reactions:
        tts_path = tts_dir / f"reaction_{r['index']:03d}.mp3"
        ok = _synthesize_verdict_tts(
            r["verdict_text"], tts_path, r["emotion"],
            edge_voice=voice, edge_rate=rate,
        )
        if not ok:
            log.warning("[LIVE-RX] TTS failed for reaction %d, skipping", r["index"])
            continue
        r["tts_path"] = tts_path
        r["tts_duration"] = _probe_duration(tts_path)
        reactions_with_tts.append(r)

    if not reactions_with_tts:
        log.error("[LIVE-RX] No reactions had TTS generated")
        return None

    # Step 4: build facecam timeline spanning full source duration
    total_dur = _probe_duration(source_video)
    facecam_reactions = [
        {"start": r["time"], "emotion": r["emotion"], "intensity": r["intensity"]}
        for r in reactions_with_tts
    ]
    # Hold each emotion for TTS duration + small buffer so Peanut "talks"
    # while his verdict is audible
    max_tts_dur = max(r["tts_duration"] for r in reactions_with_tts)
    timeline = build_facecam_timeline(
        facecam_reactions, total_dur,
        reaction_hold_seconds=max(3.0, max_tts_dur + 0.5),
        clips_dir=clips_dir,
    )

    facecam_path = work_dir / "facecam_full.mp4"
    log.info("[LIVE-RX] Rendering full-episode facecam (%.1fmin)...", total_dur / 60)
    rendered = render_dynamic_facecam(
        timeline, facecam_path, size=FACECAM_SIZE, xfade_duration=0.3,
    )
    if not rendered:
        log.error("[LIVE-RX] Facecam render failed")
        return None

    # Step 5: single ffmpeg pass compositing everything
    final_path = output_dir / f"LIVE_RX_EP_{datetime.utcnow().strftime('%Y%m%d')}.mp4"
    log.info("[LIVE-RX] Compositing final episode...")
    result = composite_live_reaction_episode(
        source_video=source_video,
        facecam_track=facecam_path,
        reactions_with_tts=reactions_with_tts,
        output_path=final_path,
    )
    if not result:
        log.error("[LIVE-RX] Composite returned None")
        return None

    log.info("[LIVE-RX] Episode ready: %s (%d MB)", final_path.name,
             final_path.stat().st_size // (1024 * 1024))
    return final_path


def run_live_reaction_pipeline(
    output_dir: Path,
    playlist_urls: list[str],
    character: str = "Peanut",
    voice: str = "en-GB-RyanNeural",
    upload_service=None,
    upload_privacy: str = "public",
    tags: Optional[list[str]] = None,
) -> dict:
    """Full live-reaction pipeline: download -> reactions -> composite -> upload.

    Returns {video_path, youtube_url, errors}.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {"video_path": None, "youtube_url": None, "errors": []}

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
    ep_dir = output_dir / f"live_rx_{timestamp}"

    # Step 1: download ONE full video
    log.info("[LIVE-RX] Sourcing full video from %d playlist(s)...", len(playlist_urls))
    source_video = download_full_playlist_video(playlist_urls, ep_dir / "source")
    if not source_video:
        result["errors"].append("No source video downloaded")
        return result

    # Step 2: build the reaction episode
    final = build_live_reaction_episode(
        source_video=source_video,
        output_dir=ep_dir,
        character=character,
        voice=voice,
    )
    if not final:
        result["errors"].append("Episode build failed")
        return result

    result["video_path"] = str(final)

    # Step 3 (optional): upload
    if upload_service:
        try:
            from peanut_reacts.upload.youtube_upload import YouTubeUploader, UploadMetadata
            from peanut_reacts.character.thumbnail_tntl import (
                generate_tntl_thumbnail_for_episode,
            )

            # Thumbnail: reuse TNTL generator — it picks a frame from a
            # clip, so we give it the full source video as a single
            # "clip" and pick a mid-video frame. Caption from a verdict.
            thumbnail_path = ep_dir / f"thumb_{timestamp}.jpg"
            try:
                generate_tntl_thumbnail_for_episode(
                    episode_clips=[source_video],
                    output_path=thumbnail_path,
                    caption="I NEED ANSWERS",
                )
            except Exception as e:
                log.warning("[LIVE-RX] Thumbnail failed: %s", e)
                thumbnail_path = None

            titles = [
                f"{character.upper()} REACTS TO SIDEMEN AMONG US (LIVE)",
                f"{character.upper()} WATCHES SIDEMEN AMONG US FOR THE FIRST TIME",
                f"A BRITISH PEANUT WATCHES SIDEMEN AMONG US",
                f"I NEED ANSWERS IMMEDIATELY — {character.upper()} REACTS TO SIDEMEN",
                f"NO GAPS. SIDEMEN AMONG US LIVE REACTION.",
            ]
            title = random.choice(titles)

            uploader = YouTubeUploader(upload_service)
            meta = UploadMetadata(
                title=title[:100],
                description=(
                    f"{character} watches a full Sidemen Among Us episode, "
                    f"British calm commentary throughout.\n\n"
                    f"New episodes weekly. Subscribe if you laughed, me.\n\n"
                    f"#sidemen #amongus #{character.lower()}reacts "
                    f"#animatedreaction #british"
                ),
                tags=(tags or []) + [
                    "sidemen", "among us", "reaction", "animated reaction",
                    "peanut reacts", "british", character.lower(),
                ],
                category_id="24",
                privacy_status=upload_privacy,
                thumbnail_path=thumbnail_path if thumbnail_path and thumbnail_path.exists() else None,
            )
            up_result = uploader.upload_video(final, meta)
            result["youtube_url"] = up_result.url
            log.info("[LIVE-RX] Uploaded: %s", up_result.url)
        except Exception as e:
            log.error("[LIVE-RX] Upload failed: %s", e)
            result["errors"].append(f"Upload: {e}")

    return result
