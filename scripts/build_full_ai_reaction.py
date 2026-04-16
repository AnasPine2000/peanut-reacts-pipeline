#!/usr/bin/env python3
"""Build full 10-min reaction video with continuous AI peanut facecam + analysis."""

import sys, io, json, os, asyncio, subprocess, time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.decoded.dense_reactor import generate_dense_reactions
from peanut_reacts.core.srt import parse_srt
from peanut_reacts.core.ffmpeg import get_video_duration, concatenate
from peanut_reacts.character.reaction_generator import LLMConfig
from peanut_reacts.core.logging_setup import build_logger
import edge_tts

log = build_logger("full_ai", verbose=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")
CLIPS_DIR = Path("data/ai_peanut_clips/continuous")
SOURCE_VIDEO = Path("downloads/AMONGUS/DUMBEST SIDEMEN AMONG US EVER.webm")
SOURCE_SRT = Path("downloads/AMONGUS/DUMBEST SIDEMEN AMONG US EVER.en.srt")
OUTPUT = Path("production/full_ai_reaction")
OUTPUT.mkdir(parents=True, exist_ok=True)

MAX_DURATION = 600


def main():
    log.info("=== FULL AI PEANUT REACTION VIDEO (10 min) ===")

    # Step 1: Source clip
    duration = min(get_video_duration(SOURCE_VIDEO), MAX_DURATION)
    clip = OUTPUT / "source.mp4"
    if not clip.exists():
        log.info("Extracting %ds source clip...", duration)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(SOURCE_VIDEO), "-t", str(duration),
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(clip),
        ], check=True, capture_output=True)

    # Step 2: Dense reactions (with analysis mixed in)
    log.info("=== Step 2: Dense reactions + analysis ===")
    reactions_file = OUTPUT / "reactions.json"
    if reactions_file.exists():
        reactions = json.loads(reactions_file.read_text(encoding="utf-8"))
        log.info("Loaded %d cached reactions", len(reactions))
    else:
        transcript = parse_srt(SOURCE_SRT)
        transcript_clip = [s for s in transcript if float(s.get("start", 0)) < duration]

        # Load fan stats for analysis lines
        fan_stats = json.loads(Path("data/transcripts/fan_stats.json").read_text(encoding="utf-8"))

        # Generate dense reactions with analysis sprinkled in
        raw = generate_dense_reactions(
            transcript_clip, duration,
            LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY),
            reactions_per_minute=5.0,
        )
        reactions = [{"start": r.start, "text": r.text, "emotion": r.emotion, "intensity": r.intensity} for r in raw]

        # Inject analysis lines every 60 seconds
        analysis_lines = [
            {"start": 55, "text": "Fun fact: Simon wins 74 percent of his imposter games. The highest of any Sidemen.", "emotion": "analysis", "intensity": 0.5},
            {"start": 115, "text": "Did you know KSI has been accused 633 times across all episodes? Most of any player.", "emotion": "analysis", "intensity": 0.5},
            {"start": 175, "text": "The data shows Josh and Simon interact 326 times in transcripts. Biggest rivalry.", "emotion": "analysis", "intensity": 0.5},
            {"start": 235, "text": "Deji has a 1.32 KDR, meaning he kills more than he dies. But still loses the most.", "emotion": "analysis", "intensity": 0.5},
            {"start": 355, "text": "Harry has the worst KDR at 0.74. He dies almost every single game.", "emotion": "analysis", "intensity": 0.5},
            {"start": 475, "text": "Simon averages 2.41 kills per imposter game. The deadliest imposter in Sidemen history.", "emotion": "analysis", "intensity": 0.5},
        ]

        # Merge analysis into reactions (don't overlap)
        for a in analysis_lines:
            overlap = any(abs(r["start"] - a["start"]) < 10 for r in reactions)
            if not overlap:
                reactions.append(a)

        reactions.sort(key=lambda r: r["start"])
        reactions_file.write_text(json.dumps(reactions, indent=2), encoding="utf-8")
        log.info("Generated %d reactions (including %d analysis)", len(reactions), len(analysis_lines))

    # Step 3: TTS
    log.info("=== Step 3: TTS ===")
    tts_dir = OUTPUT / "tts"
    tts_dir.mkdir(exist_ok=True)

    for i, r in enumerate(reactions):
        path = tts_dir / f"r_{i:04d}.mp3"
        if path.exists() and path.stat().st_size > 500:
            continue

        text = r["text"]
        # Analysis lines: slower, calmer voice
        if r.get("emotion") == "analysis":
            rate, pitch = "-5%", "-5Hz"
        elif r.get("intensity", 0.5) > 0.7:
            rate, pitch = "+15%", "+5Hz"
        else:
            rate, pitch = "+5%", "+0Hz"

        for attempt in range(3):
            try:
                c = edge_tts.Communicate(text, "en-US-GuyNeural", rate=rate, pitch=pitch)
                asyncio.run(c.save(str(path)))
                break
            except:
                time.sleep(2)
        time.sleep(0.3)

    tts_count = len(list(tts_dir.glob("r_*.mp3")))
    log.info("TTS: %d files", tts_count)

    # Step 4: Reaction audio track
    log.info("=== Step 4: Reaction audio ===")
    reaction_audio = OUTPUT / "reaction_audio.aac"
    if not reaction_audio.exists():
        inputs = ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=44100:cl=stereo"]
        filters = []
        valid = 0
        for i, r in enumerate(reactions):
            tts_path = tts_dir / f"r_{i:04d}.mp3"
            if not tts_path.exists() or tts_path.stat().st_size < 500:
                continue
            inputs.extend(["-i", str(tts_path)])
            delay_ms = int(r["start"] * 1000)
            filters.append(f"[{valid+1}:a]aresample=44100,adelay={delay_ms}|{delay_ms}[a{valid}]")
            valid += 1

        mix = "[0:a]" + "".join(f"[a{i}]" for i in range(valid))
        filters.append(f"{mix}amix=inputs={valid+1}:duration=first:dropout_transition=2[out]")
        subprocess.run(
            ["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(filters),
             "-map", "[out]", "-c:a", "aac", "-b:a", "192k", "-t", str(duration), str(reaction_audio)],
            check=True, capture_output=True,
        )
    log.info("Reaction audio ready")

    # Step 5: Build continuous AI facecam from generated clips
    log.info("=== Step 5: Continuous AI facecam ===")
    facecam = OUTPUT / "facecam_continuous.mp4"
    if not facecam.exists():
        # Get all generated continuous clips
        ai_clips = sorted(CLIPS_DIR.glob("clip_*.mp4"))
        log.info("Available AI clips: %d", len(ai_clips))

        if len(ai_clips) < 5:
            log.warning("Not enough AI clips yet! Using looped long clip as fallback")
            long_clip = Path("data/ai_peanut_clips/peanut_long_talking.mp4")
            if long_clip.exists():
                # Loop the 10s clip to fill duration
                subprocess.run([
                    "ffmpeg", "-y", "-stream_loop", str(int(duration / 10) + 1),
                    "-i", str(long_clip), "-t", str(duration),
                    "-vf", "scale=300:300:force_original_aspect_ratio=decrease,pad=300:300:(ow-iw)/2:(oh-ih)/2:color=0x00FF00",
                    "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p", "-an",
                    str(facecam),
                ], check=True, capture_output=True)
            else:
                log.error("No AI clips available!")
                return
        else:
            # Crossfade all clips into one continuous video
            temp_scaled = []
            temp_dir = OUTPUT / "temp_fc"
            temp_dir.mkdir(exist_ok=True)

            for i, c in enumerate(ai_clips):
                scaled = temp_dir / f"scaled_{i:03d}.mp4"
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(c),
                    "-vf", "scale=300:300:force_original_aspect_ratio=decrease,pad=300:300:(ow-iw)/2:(oh-ih)/2:color=0x00FF00",
                    "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p", "-an",
                    str(scaled),
                ], capture_output=True)
                if scaled.exists():
                    temp_scaled.append(scaled)

            if temp_scaled:
                # Concatenate with crossfade would be ideal but complex
                # Simple concat for now — the consistent prompt makes transitions smooth
                concatenate(temp_scaled, facecam)

                # If not enough clips to fill duration, loop what we have
                fc_dur = get_video_duration(facecam)
                if fc_dur < duration - 5:
                    log.info("Facecam is %.1fs, looping to fill %.1fs", fc_dur, duration)
                    looped = OUTPUT / "facecam_looped.mp4"
                    subprocess.run([
                        "ffmpeg", "-y", "-stream_loop", str(int(duration / fc_dur) + 1),
                        "-i", str(facecam), "-t", str(duration),
                        "-c:v", "libx264", "-crf", "22", "-pix_fmt", "yuv420p", "-an",
                        str(looped),
                    ], check=True, capture_output=True)
                    looped.rename(facecam)

            # Cleanup
            for s in temp_scaled:
                s.unlink(missing_ok=True)
            try:
                temp_dir.rmdir()
            except:
                pass

    fc_dur = get_video_duration(facecam) if facecam.exists() else 0
    log.info("Facecam: %.1fs", fc_dur)

    # Step 6: Final composite
    log.info("=== Step 6: Final composite ===")
    final = OUTPUT / "FULL_AI_REACTION.mp4"

    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(clip),
        "-i", str(facecam),
        "-i", str(reaction_audio),
        "-t", str(duration),
        "-filter_complex",
        "[1:v]colorkey=0x00FF00:0.25:0.15,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=3[peanut];"
        "[0:v][peanut]overlay=W-320:16[video];"
        "[0:a]volume=0.65[orig];"
        "[2:a]volume=2.8[react];"
        "[orig][react]amix=inputs=2:duration=first:dropout_transition=2[audio]",
        "-map", "[video]", "-map", "[audio]",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(final),
    ], check=True, capture_output=True)

    # Normalize audio
    final_loud = OUTPUT / "FULL_AI_REACTION_FINAL.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(final),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(final_loud),
    ], check=True, capture_output=True)

    dur = get_video_duration(final_loud)
    log.info("DONE! %s — %.1f min, %d reactions, continuous AI facecam", final_loud.name, dur / 60, len(reactions))
    print(f"\nOutput: {final_loud}")
    print(f"Duration: {dur / 60:.1f} minutes")
    print(f"Reactions: {len(reactions)}")


if __name__ == "__main__":
    main()
