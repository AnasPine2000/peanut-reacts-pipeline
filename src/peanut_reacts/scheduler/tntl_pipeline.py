"""Try Not To Laugh pipeline — Peanut watches funny clips and tries not to laugh.

Format (based on KSI's 6M avg / 19.4M peak view TNTL series):
1. Source funny clips from compilations
2. For each clip: Peanut starts neutral, escalates to breakdown
3. Score system: 1000 points, -200 per laugh, +100 per survive
4. Dynamic facecam switches emotions progressively
5. TTS commentary between clips
6. 18-22 min episodes, 12-15 clips each
7. Extract Shorts from best fail moments
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Clip sources — funny compilation search queries
FUNNY_QUERIES = [
    "funniest fails compilation 2026",
    "try not to laugh impossible 2026",
    "funny tiktok compilation 2026",
    "cute animals doing funny things",
    "instant regret compilation",
    "perfectly cut screams compilation",
    "unexpected meme compilation",
]

# Richard Sales Official voice applied to TNTL scoring context.
# Calm British gravitas > chaotic hype. Signature vocabulary: BRIM ME BABY,
# NO GAPS, VERY IMPRESSIVE, I NEED ANSWERS IMMEDIATELY, "me"-suffix tic.
# ALL CAPS mirrors Richard's caption-equals-spoken-line discipline.
PEANUT_SURVIVE_LINES = [
    "NO GAPS. FLAWLESS. MOVING ON.",
    "BRIM ME BABY. STILL STANDING, ME.",
    "VERY IMPRESSIVE. SHELL INTACT.",
    "I DO LOVE A CLEAN ROUND, ME.",
    "WELL HELLO TO YOU TOO. DIDN'T EVEN FLINCH.",
    "TO THE BRIM AND STILL NOT LAUGHING.",
    "I KNEW IT WAS COMING. I WAS READY.",
    "ABSOLUTELY FILLED WITH COMPOSURE.",
]

PEANUT_FAIL_LINES = [
    "AND THERE IT IS. GAP IN THE SHELL.",
    "I'M CRACKED. FULLY CRACKED. BRIM ME BABY.",
    "OH NO NO NO. THAT GOT ME, THAT DID.",
    "I NEED ANSWERS IMMEDIATELY. ALSO A TISSUE.",
    "LOST FOR WORDS. SHELL OFFICIALLY LOST.",
    "VERY IMPRESSIVE. VERY DEVASTATING. BOTH THINGS.",
    "I DO LOVE A GOOD BREAK, ME. UNFORTUNATELY.",
    "THAT'S A WHOLE SHIP OF LAUGHTER. I'M DONE.",
]

PEANUT_STRUGGLE_LINES = [
    "HOLD ON. HOLD ON. NOT YET.",
    "STILL HERE. BARELY. STILL HERE.",
    "THIS IS BORDERLINE BRIM TERRITORY.",
    "I'M WATCHING. I'M JUDGING. I'M CRACKING.",
    "TINY GAP. TINY GAP. DO NOT FILL IT.",
    "VERY IMPRESSIVE — AND I HATE THAT FOR ME.",
    "WE ARE SO BEHIND, HUMOUR-RESISTANCE WISE.",
    "I DO NOT LIKE WHERE THIS IS GOING, ME.",
]


def source_funny_clips(output_dir: Path, num_clips: int = 15) -> list[Path]:
    """Download funny compilation videos and extract individual clips."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Download 2-3 compilations
    query = random.choice(FUNNY_QUERIES)
    log.info("[TNTL] Searching: %s", query)

    try:
        result = subprocess.run(
            ["yt-dlp", f"ytsearch3:{query}",
             "--flat-playlist", "--print", "%(id)s\t%(title)s\t%(duration)s"],
            capture_output=True, text=True, timeout=60,
        )
        vids = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2:
                try:
                    dur = float(parts[2]) if len(parts) > 2 and parts[2] not in ("NA", "") else 0
                except ValueError:
                    dur = 0
                # Modern fail-compilation channels run 30-120 min; the old
                # 3-20 min filter rejected everything in 2026. We want a
                # long source so our 18 x 25s extracted clips are well-spaced
                # and don't repeat across the compilation.
                if 180 < dur < 7200:   # 3 min to 2 hours
                    vids.append({"id": parts[0], "title": parts[1], "duration": dur})
    except Exception as e:
        log.error("Search failed: %s", e)
        return []

    if not vids:
        return []

    # Download top pick
    vid = vids[0]
    dl_path = output_dir / f"{vid['id']}.mp4"
    if not dl_path.exists():
        log.info("[TNTL] Downloading: %s", vid["title"][:60])
        subprocess.run([
            "yt-dlp", "-f", "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/best[height<=1080]",
            "--merge-output-format", "mp4", "-o", str(dl_path),
            f"https://youtube.com/watch?v={vid['id']}",
        ], capture_output=True, timeout=600)

    if not dl_path.exists():
        return []

    # Get duration and extract clips at regular intervals
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(dl_path)],
        capture_output=True, text=True, timeout=10,
    )
    total_dur = float(r.stdout.strip())

    # KSI TNTL clip length: 15-35s (only punchline + 2-3s setup). We target
    # 25s — long enough for setup+payoff, short enough for 18 clips/episode
    # at ~10min runtime. Previous 60s made episodes drag and killed retention.
    clips = []
    clip_dur = 25
    spacing = max(clip_dur + 5, total_dur / (num_clips + 1))

    for i in range(num_clips):
        start = spacing * (i + 0.5)
        if start + clip_dur > total_dur:
            break

        clip_path = output_dir / f"clip_{i:02d}.mp4"
        if not clip_path.exists():
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(start), "-i", str(dl_path),
                "-t", str(clip_dur), "-c:v", "libx264", "-preset", "veryfast",
                "-c:a", "aac", str(clip_path),
            ], capture_output=True, timeout=120)

        if clip_path.exists():
            clips.append(clip_path)

    log.info("[TNTL] Extracted %d clips from %s", len(clips), vid["title"][:40])
    return clips


def _probe_duration(path: Path) -> float:
    """Get media duration in seconds via ffprobe."""
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _render_clip_with_peanut(
    clip_path: Path,
    verdict_tts: Optional[Path],
    emotion: str,
    score_text: str,
    output_path: Path,
    clips_dir: Path,
    facecam_size: int = 360,
    margin: int = 40,
) -> Optional[Path]:
    """Composite one TNTL clip with Peanut PIP + verdict TTS near end.

    Unlike the old sequential format (clip → dark screen with talking head),
    Peanut is VISIBLE during the clip (bottom-right corner PIP), the clip's
    original funny audio plays at reduced volume throughout, and Peanut's
    verdict TTS fires in the LAST 1.5-3s of the clip (Richard Sales grammar:
    verdict at end-of-clip, not between clips).

    Pipeline:
      1. Probe clip + tts durations
      2. Build facecam timeline: idle for most of clip, reaction emotion at
         verdict_start
      3. Render facecam track (green-bg, chroma-keyed on composite)
      4. Single ffmpeg: source clip video + PIP overlay + ducked source
         audio mixed with delayed verdict TTS + score overlay
    """
    from peanut_reacts.character.dynamic_facecam import (
        build_facecam_timeline, render_dynamic_facecam, get_clip_for_emotion,
    )

    if not clip_path.exists():
        return None

    clip_dur = _probe_duration(clip_path)
    if clip_dur < 3.0:
        log.warning("[TNTL] Clip too short (%.1fs): %s", clip_dur, clip_path.name)
        return None

    # Compute verdict placement: last N seconds of clip
    if verdict_tts and verdict_tts.exists():
        tts_dur = _probe_duration(verdict_tts)
        verdict_start = max(0.5, clip_dur - tts_dur - 0.3)
    else:
        tts_dur = 0.0
        verdict_start = clip_dur  # effectively no verdict

    # Build facecam timeline — idle up to verdict_start, then reaction emotion
    reactions = [{"start": verdict_start, "emotion": emotion, "intensity": 1.0}]
    timeline = build_facecam_timeline(reactions, clip_dur, clips_dir=clips_dir)
    if not timeline:
        log.warning("[TNTL] Empty timeline for %s", clip_path.name)
        return None

    work_dir = output_path.parent / f"_fc_{output_path.stem}"
    work_dir.mkdir(exist_ok=True)
    facecam_path = work_dir / "facecam.mp4"

    if render_dynamic_facecam(timeline, facecam_path, size=facecam_size) is None:
        log.warning("[TNTL] Facecam render failed for %s", clip_path.name)
        return None

    # Single ffmpeg composite: video overlay + audio amix + score text
    # Audio: clip audio ducked 0.5, verdict TTS delayed to verdict_start boosted 2.0
    verdict_delay_ms = int(verdict_start * 1000)

    # Escape colons / special chars in drawtext score overlay
    score_safe = score_text.replace(":", r"\:").replace("'", r"\'")

    if verdict_tts and verdict_tts.exists():
        # KSI-style reaction punch-in: Peanut PIP is small during the clip,
        # then jumps to ~40% bigger at verdict_start for emphasis. Two
        # chromakeyed copies of the facecam (small + big) gated by
        # enable='lt(t,VERDICT_START)' / 'gte(...)' so exactly one is
        # visible at any moment.
        big_size = int(facecam_size * 1.4)   # 360 -> 504
        big_margin = max(20, margin - 10)    # slightly tighter margin when big
        # Chromakey target: AI Kling clips use a teal-jade gradient
        # background (#005f37 -> #00cc8a, centered around #008c52), NOT
        # the pure 0x00FF00 that flat_peanut used. High similarity (0.35)
        # + generous blend (0.22) covers the full gradient range without
        # eating the peanut's tan/brown body (which is far from teal in
        # YUV space). The beanie's green stripe IS affected but only at
        # its edge, barely noticeable.
        filter_complex = (
            f"[1:v]chromakey=0x008C52:0.20:0.12,split=2[fcA][fcB];"
            f"[fcA]scale={facecam_size}:{facecam_size}[fc_small];"
            f"[fcB]scale={big_size}:{big_size}[fc_big];"
            f"[0:v][fc_small]overlay=W-w-{margin}:H-h-{margin}:"
            f"enable='lt(t,{verdict_start:.3f})'[v1];"
            f"[v1][fc_big]overlay=W-w-{big_margin}:H-h-{big_margin}:"
            f"enable='gte(t,{verdict_start:.3f})',"
            f"drawtext=text='{score_safe}':fontcolor=yellow:fontsize=36:"
            f"box=1:boxcolor=black@0.6:boxborderw=8:x=20:y=20[video_out];"
            f"[0:a]volume=0.55[src_audio];"
            f"[2:a]adelay={verdict_delay_ms}|{verdict_delay_ms},volume=1.8[verdict];"
            f"[src_audio][verdict]amix=inputs=2:duration=first:dropout_transition=0[audio_out]"
        )
        inputs = ["-i", str(clip_path), "-i", str(facecam_path), "-i", str(verdict_tts)]
        maps = ["-map", "[video_out]", "-map", "[audio_out]"]
    else:
        # No verdict (cold-open / hook clip) — just PIP overlay + source audio.
        # Same AI-clip chromakey params as the verdict-case above.
        filter_complex = (
            f"[1:v]chromakey=0x008C52:0.20:0.12,"
            f"scale={facecam_size}:{facecam_size}[fc];"
            f"[0:v][fc]overlay=W-w-{margin}:H-h-{margin},"
            f"drawtext=text='{score_safe}':fontcolor=yellow:fontsize=36:"
            f"box=1:boxcolor=black@0.6:boxborderw=8:x=20:y=20[video_out]"
        )
        inputs = ["-i", str(clip_path), "-i", str(facecam_path)]
        maps = ["-map", "[video_out]", "-map", "0:a"]

    try:
        subprocess.run([
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            *maps,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-shortest",
            str(output_path),
        ], check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as e:
        log.error("[TNTL] Clip composite failed for %s: %s",
                  clip_path.name, e.stderr.decode("utf-8", errors="replace")[-400:])
        return None
    finally:
        # Cleanup facecam work files
        try:
            facecam_path.unlink(missing_ok=True)
            work_dir.rmdir()
        except Exception:
            pass

    return output_path if output_path.exists() else None


def build_tntl_episode(
    clips: list[Path],
    output_dir: Path,
    character: str = "Peanut",
    voice: str = "en-GB-RyanNeural",
    rate: str = "+5%",
) -> Optional[Path]:
    """Build a full Try Not To Laugh episode from clips.

    Structure (hybrid KSI+ format / Richard Sales voice):
      - Cold-open hook (biggest fail clip, Peanut PIP, no intro music)
      - Rules intro (short: "I laugh, shell cracks. Let's get into it.")
      - N × (clip with Peanut PIP in corner + verdict TTS in last ~2s of clip)
      - Outro with final score + CTA

    v3.2: verdicts are now context-aware. For each clip we:
      1. Describe it via Groq Llama 3.2 Vision (or fallback if no key)
      2. Generate a verdict via DeepSeek using the Peanut persona + history
      3. Synthesize the verdict via ElevenLabs (British "George" voice) with
         fallback to Edge TTS if ElevenLabs fails.
    """
    from peanut_reacts.character.tts import EdgeTTSEngine, TTSConfig
    from peanut_reacts.character.clip_context import describe_clip, generate_verdict

    output_dir.mkdir(parents=True, exist_ok=True)
    edge_tts = EdgeTTSEngine(TTSConfig(voice=voice, rate=rate))
    clips_dir = PROJECT_ROOT / "data" / "ai_peanut_clips"

    # ── ElevenLabs verdict TTS (with Edge TTS fallback) ────────────────────
    # "George" (JBFqnCBsd6RMkjVDRZzb) is a warm British narrator voice —
    # matches our Richard-Sales-inspired calm Victorian butler Peanut.
    def _synthesize_verdict(text: str, out_path: Path, emotion: str) -> None:
        """Try ElevenLabs first, fall back to Edge TTS on any failure."""
        try:
            from peanut_reacts.character.elevenlabs_tts import (
                ElevenLabsTTSEngine, ElevenLabsConfig,
            )
            if not os.environ.get("ELEVENLABS_API_KEY"):
                raise RuntimeError("ELEVENLABS_API_KEY missing")
            engine = ElevenLabsTTSEngine(ElevenLabsConfig(
                voice_id="JBFqnCBsd6RMkjVDRZzb",  # British "George"
                model_id="eleven_multilingual_v2",
            ))
            # Map our emotion names to the subset elevenlabs_tts knows
            el_emotion = {
                "laughing": "amused", "facepalm": "sarcastic",
                "scared": "shocked", "celebrating": "excited",
                "idle": "neutral",
            }.get(emotion, emotion if emotion in {
                "excited","shocked","amused","sarcastic","confused","neutral"
            } else "neutral")
            engine.synthesize(text, out_path, emotion=el_emotion)
            return
        except Exception as e:
            log.warning("[TNTL] ElevenLabs failed (%s); falling back to Edge TTS",
                        str(e)[:120])
        asyncio.run(edge_tts.synthesize(text, out_path))

    import os  # local import for env checks

    # ── Determine outcomes PER CLIP via context-aware LLM verdict ──────────
    # Replaces the old random 35/65 split + static line arrays. For each
    # clip we describe -> verdict. History ensures DeepSeek doesn't repeat
    # lines across the episode.
    outcomes: list[dict] = []
    descriptions: list[str] = []
    history: list[str] = []
    score = 1000
    streak = 0

    log.info("[TNTL] Generating context-aware verdicts for %d clips...", len(clips))
    for i, clip in enumerate(clips):
        description = describe_clip(clip)
        descriptions.append(description)
        verdict = generate_verdict(description, history=history)
        history.append(verdict.text)

        survives = verdict.outcome == "survive"
        if survives:
            streak += 1
            score += 100
        else:
            score -= 200
            streak = 0

        outcomes.append({
            "survives": survives,
            "line": verdict.text,
            "emotion": verdict.emotion,
            "intensity": verdict.intensity,
            "description": description,
            "score_after": score,
            "streak": streak,
        })

    # Build each segment: clip audio lowered + Peanut reaction TTS after
    segments = []
    tts_dir = output_dir / "tts"
    tts_dir.mkdir(exist_ok=True)

    # Intro TTS — Richard-voiced Peanut: calm gravitas, short bursts, "me" tic.
    # Defer sub/bell ask to outro per KSI+ pattern. Uses ElevenLabs voice.
    intro_text = (
        f"Right. Try Not To Laugh. {len(clips)} clips. "
        f"One rule: I laugh, the shell cracks. Let's get into it, me."
    )
    intro_tts = tts_dir / "intro.mp3"
    if not intro_tts.exists():
        _synthesize_verdict(intro_text, intro_tts, emotion="neutral")

    # Generate TTS for each verdict via ElevenLabs (with Edge TTS fallback).
    # The verdict line is already LLM-generated and context-aware — no more
    # random struggle preamble (that was a hack to add variety when lines
    # were static; now each line is fresh).
    for i, outcome in enumerate(outcomes):
        reaction_tts = tts_dir / f"reaction_{i:02d}.mp3"
        if not reaction_tts.exists():
            _synthesize_verdict(outcome["line"], reaction_tts,
                                emotion=outcome["emotion"])

    # Outro TTS — Richard grammar: short, dry, understated. "crack the sub" is
    # the Peanut-specific CTA twist on KSI+'s "smash the sub".
    final_score = outcomes[-1]["score_after"] if outcomes else 1000
    if final_score > 500:
        outro_text = (
            f"Final score: {final_score}. Shell held. Very impressive, me. "
            f"Smash the like, crack the sub button. Peace."
        )
    else:
        outro_text = (
            f"Final score: {final_score}. I'm cracked. Fully cracked. "
            f"BRIM ME BABY. Smash the like, crack the sub button. Peace."
        )

    outro_tts = tts_dir / "outro.mp3"
    if not outro_tts.exists():
        _synthesize_verdict(outro_text, outro_tts,
                            emotion="sarcastic" if final_score > 500 else "amused")

    # ─── Build final video ───
    # Structure: [cold-open hook] → [rules intro] → N × [clip+PIP+verdict]
    #            → [outro].
    # Verdict TTS fires IN the last ~2s of each clip (Richard-at-end-of-clip
    # grammar), NOT on a dark screen between clips. Peanut is VISIBLE
    # throughout every clip as a bottom-right PIP via chroma-keyed facecam
    # overlay — not just as a talking-head between clips like v0.
    segment_files: list[Path] = []

    # ── Cold-open hook (0-12s) ──
    # KSI+ pattern: pre-roll the BIGGEST reaction clip from later in the
    # episode, no intro music, direct-to-camera voice tease. We pick the
    # highest-index FAIL clip (by the time we're deep, laughs are more
    # extreme) and play its last 8s with Peanut laughing PIP.
    hook_clip_idx = next(
        (i for i in range(len(clips) - 1, -1, -1) if not outcomes[i]["survives"]),
        0,
    )
    hook_src = clips[hook_clip_idx]
    hook_tease_path = output_dir / "seg_00_hook_tease.mp4"
    hook_tease_tts = tts_dir / "hook_tease.mp3"
    hook_tease_text = f"You will not make it past clip {hook_clip_idx + 1}. Trust me, me."
    if not hook_tease_tts.exists():
        _synthesize_verdict(hook_tease_text, hook_tease_tts, emotion="amused")

    # Trim hook source to last 6s (the payoff beat)
    hook_trimmed = output_dir / "_hook_trim.mp4"
    hook_dur = _probe_duration(hook_src)
    hook_start = max(0.0, hook_dur - 6.0)
    if not hook_trimmed.exists():
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(hook_start), "-i", str(hook_src),
            "-t", "6", "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", str(hook_trimmed),
        ], capture_output=True, timeout=60)

    if hook_trimmed.exists():
        hook_rendered = _render_clip_with_peanut(
            clip_path=hook_trimmed,
            verdict_tts=hook_tease_tts,
            emotion="laughing",
            score_text="HOOK",
            output_path=hook_tease_path,
            clips_dir=clips_dir,
        )
        if hook_rendered:
            segment_files.append(hook_rendered)

    # ── Rules intro (short title card with intro TTS over Peanut idle) ──
    intro_vid = output_dir / "seg_01_intro.mp4"
    intro_dur = max(5.0, _probe_duration(intro_tts) + 0.5)
    if not intro_vid.exists():
        # Title card: dark bg + "TRY NOT TO LAUGH" + "with Peanut" + intro TTS
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=#1a1a2e:s=1920x1080:d={intro_dur}",
            "-i", str(intro_tts),
            "-vf", (
                f"drawtext=text='TRY NOT TO LAUGH':fontcolor=white:fontsize=120:"
                f"x=(w-text_w)/2:y=(h-text_h)/2-80,"
                f"drawtext=text='with {character}':fontcolor=#D4A574:fontsize=64:"
                f"x=(w-text_w)/2:y=(h-text_h)/2+80"
            ),
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
            "-shortest", str(intro_vid),
        ], capture_output=True, timeout=60)
    if intro_vid.exists():
        segment_files.append(intro_vid)

    # ── Each clip: source + Peanut PIP + verdict TTS near end ──
    for i, (clip, outcome) in enumerate(zip(clips, outcomes)):
        # Retrospective score display (not a spoiler): current score +
        # cracks-so-far. Previous version showed the upcoming delta
        # "(+100 / -200)" which was forward-looking and spoiled each clip's
        # outcome before it landed. Now we show what has happened up to
        # this clip — which tracks with the "CRACK BAR" mental model.
        score_before = outcomes[i - 1]["score_after"] if i > 0 else 1000
        cracks_so_far = sum(1 for o in outcomes[:i] if not o["survives"])
        score_text = (
            f"CLIP {i + 1}/{len(clips)} | SHELL {cracks_so_far} CRACKS | {score_before}"
        )

        reaction_tts = tts_dir / f"reaction_{i:02d}.mp3"
        seg_out = output_dir / f"seg_10_clip_{i:02d}.mp4"
        if not seg_out.exists():
            _render_clip_with_peanut(
                clip_path=clip,
                verdict_tts=reaction_tts if reaction_tts.exists() else None,
                emotion=outcome["emotion"],
                score_text=score_text,
                output_path=seg_out,
                clips_dir=clips_dir,
            )
        if seg_out.exists():
            segment_files.append(seg_out)

    # ── Outro (final-score title card) ──
    outro_vid = output_dir / "seg_99_outro.mp4"
    outro_dur = max(6.0, _probe_duration(outro_tts) + 0.5)
    if not outro_vid.exists():
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=#1a1a2e:s=1920x1080:d={outro_dur}",
            "-i", str(outro_tts),
            "-vf", (
                f"drawtext=text='FINAL SCORE':fontcolor=white:fontsize=80:"
                f"x=(w-text_w)/2:y=(h-text_h)/2-100,"
                f"drawtext=text='{final_score}':"
                f"fontcolor={'#22c55e' if final_score > 500 else '#ff4444'}:"
                f"fontsize=140:x=(w-text_w)/2:y=(h-text_h)/2+30,"
                f"drawtext=text='crack the sub button':fontcolor=yellow:"
                f"fontsize=44:x=(w-text_w)/2:y=h-100"
            ),
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
            "-shortest", str(outro_vid),
        ], capture_output=True, timeout=60)
    if outro_vid.exists():
        segment_files.append(outro_vid)

    # Concatenate all segments
    valid_segs = [s for s in segment_files if s.exists()]
    if len(valid_segs) < 3:
        log.error("[TNTL] Not enough segments")
        return None

    # Normalize every segment to 1920x1080 @ 30fps, stereo 48k before concat.
    # Source clips can be any res/fps/audio layout; intro/outro are forced
    # 1920x1080. `-c copy` requires matching codecs AND matching stream
    # parameters — it won't work here, so we re-encode each segment through
    # a fixed-spec filter and then concat.
    normalized_dir = output_dir / "_normalized"
    normalized_dir.mkdir(exist_ok=True)
    normalized_segs: list[Path] = []
    for idx, seg in enumerate(valid_segs):
        norm = normalized_dir / f"norm_{idx:03d}.mp4"
        if not norm.exists():
            try:
                # Blur-pillarbox fill: portrait source clips (phone footage,
                # vertical TikToks) would otherwise leave ugly black bars on
                # the sides after pad=1920:1080. Instead: duplicate the
                # source, scale one copy to cover 1920x1080 and blur it as
                # the background, then overlay the original (aspect-correct
                # scaled) on top. This is the "iPhone wallpaper" look that
                # makes portrait content feel intentional instead of
                # letterboxed.
                filter_complex = (
                    "[0:v]split=2[bg][fg];"
                    "[bg]scale=1920:1080:force_original_aspect_ratio=increase,"
                    "crop=1920:1080,gblur=sigma=30,setsar=1:1,fps=30[bgblur];"
                    "[fg]scale=1920:1080:force_original_aspect_ratio=decrease,"
                    "setsar=1:1,fps=30[fgfit];"
                    "[bgblur][fgfit]overlay=(W-w)/2:(H-h)/2[vout]"
                )
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(seg),
                    "-filter_complex", filter_complex,
                    "-map", "[vout]", "-map", "0:a?",
                    "-af", "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                    "-c:a", "aac", "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    str(norm),
                ], check=True, capture_output=True, timeout=300)
                normalized_segs.append(norm)
            except subprocess.CalledProcessError as e:
                log.warning("[TNTL] Normalize failed for %s: %s",
                            seg.name, e.stderr.decode("utf-8", errors="replace")[-200:])
        else:
            normalized_segs.append(norm)

    if len(normalized_segs) < 3:
        log.error("[TNTL] Not enough normalized segments (%d)", len(normalized_segs))
        return None

    concat_list = output_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{s.resolve().as_posix()}'" for s in normalized_segs),
        encoding="utf-8",
    )

    final = output_dir / f"TNTL_EP_{datetime.utcnow().strftime('%Y%m%d')}.mp4"
    try:
        # Now that all segments share the same codec + params, -c copy is safe
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list), "-c", "copy",
            "-movflags", "+faststart", str(final),
        ], check=True, capture_output=True, timeout=600)
        log.info("[TNTL] Episode ready: %s", final.name)
        return final
    except subprocess.CalledProcessError as e:
        log.error("[TNTL] Concat failed: %s", e.stderr.decode("utf-8", errors="replace")[-300:])
        return None


def run_tntl_pipeline(
    output_dir: Path,
    num_clips: int = 18,   # KSI+ target: 15-22 clips/episode (was 12 — too few)
    character: str = "Peanut",
    voice: str = "en-GB-RyanNeural",
    upload_service=None,
    upload_privacy: str = "public",
    tags: list[str] = None,
) -> dict:
    """Full TNTL pipeline: source → build → upload."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {"video_path": None, "youtube_url": None, "errors": []}

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
    ep_dir = output_dir / f"episode_{timestamp}"

    # Step 1: Source clips
    log.info("[TNTL] Sourcing funny clips...")
    clips_dir = ep_dir / "clips"
    clips = source_funny_clips(clips_dir, num_clips=num_clips)
    if len(clips) < 5:
        result["errors"].append(f"Only found {len(clips)} clips (need 5+)")
        return result

    # Step 2: Build episode
    log.info("[TNTL] Building episode with %d clips...", len(clips))
    final = build_tntl_episode(clips, ep_dir, character=character, voice=voice)
    if not final:
        result["errors"].append("Episode build failed")
        return result

    result["video_path"] = str(final)

    # Step 2.5: Generate thumbnail (KSI+ grammar: Peanut face left, blue-rect
    # clip frame right, yellow-in-quotes caption). Works even without upload.
    thumbnail_path = None
    try:
        from peanut_reacts.character.thumbnail_tntl import (
            generate_tntl_thumbnail_for_episode,
        )
        # Pick a caption from PEANUT_FAIL_LINES (thumbnail always implies the
        # reactor gets cracked — that's what sells the CTR, per KSI+ pattern).
        # Strip trailing period for clean quoted display.
        thumb_caption = random.choice(PEANUT_FAIL_LINES).split(".")[0].strip()
        thumbnail_path = ep_dir / f"thumb_{timestamp}.jpg"
        generate_tntl_thumbnail_for_episode(
            episode_clips=clips,
            output_path=thumbnail_path,
            caption=thumb_caption,
        )
        if not thumbnail_path.exists():
            thumbnail_path = None
    except Exception as e:
        log.warning("[TNTL] Thumbnail generation failed (uploading without): %s", e)
        thumbnail_path = None

    # Step 3: Upload
    if upload_service:
        try:
            from peanut_reacts.upload.youtube_upload import YouTubeUploader, UploadMetadata
            uploader = YouTubeUploader(upload_service)

            # KSI+ title grammar: all-caps emotional superlative, no episode
            # numbers, "!!" or "..." terminus. Richard twist: "NO GAPS"/"BRIM"
            # vocabulary for differentiation from every other TNTL clone.
            titles = [
                "THIS TRY NOT TO LAUGH CRACKED MY SHELL",
                "HARDEST PEANUT TRY NOT TO LAUGH EVER",
                "YOU WILL CRACK BEFORE I DO",
                "THIS TRY NOT TO LAUGH WAS BRUTAL!!",
                "NO GAPS. NO LAUGHS. I SURVIVED.",
                "I NEEDED ANSWERS. I GOT CRACKED INSTEAD.",
                "BRIM ME BABY — THIS TRY NOT TO LAUGH BROKE ME",
            ]
            title = random.choice(titles)

            meta = UploadMetadata(
                title=title[:100],
                description=(
                    f"{character} attempts Try Not To Laugh. British calm, "
                    f"{len(clips)} clips, one cracked shell.\n\n"
                    f"Rules: I laugh, the shell cracks. Starting score 1000, "
                    f"-200 per laugh, +100 per survive.\n\n"
                    f"Can YOU do better? Comment your score.\n\n"
                    f"Subscribe — new TNTL every Tuesday &amp; Friday.\n\n"
                    f"#trynottolaugh #tntl #{character.lower()} #animatedreaction "
                    f"#british #memes #funny"
                ),
                tags=(tags or []) + [
                    "try not to laugh", "tntl", "funny", "challenge",
                    "animated reaction", "british", "memes",
                    character.lower(),
                ],
                category_id="24",
                privacy_status=upload_privacy,
                thumbnail_path=thumbnail_path,
            )
            up_result = uploader.upload_video(final, meta)
            result["youtube_url"] = up_result.url
            log.info("[TNTL] Uploaded: %s", up_result.url)
        except Exception as e:
            log.error("[TNTL] Upload failed: %s", e)
            result["errors"].append(f"Upload: {e}")

    return result
