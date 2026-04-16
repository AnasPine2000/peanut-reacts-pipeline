#!/usr/bin/env python3
"""Build reaction video with AI-generated emotion-switching peanut facecam."""

import sys, io, json, os, asyncio, subprocess
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.decoded.dense_reactor import generate_dense_reactions
from peanut_reacts.decoded.emotion_clip_mapper import build_emotion_timeline, render_facecam_track
from peanut_reacts.core.srt import parse_srt
from peanut_reacts.core.ffmpeg import get_video_duration
from peanut_reacts.character.reaction_generator import LLMConfig
from peanut_reacts.core.logging_setup import build_logger
import edge_tts

log = build_logger("ai_reaction", verbose=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")
CLIPS_DIR = Path("data/ai_peanut_clips")
SOURCE_VIDEO = Path("downloads/AMONGUS/DUMBEST SIDEMEN AMONG US EVER.webm")
SOURCE_SRT = Path("downloads/AMONGUS/DUMBEST SIDEMEN AMONG US EVER.en.srt")
OUTPUT = Path("production/ai_reaction")
OUTPUT.mkdir(parents=True, exist_ok=True)

MAX_DURATION = 600  # 10 min test


def main():
    log.info("=== AI PEANUT REACTION VIDEO ===")

    # Step 1: Extract source clip
    duration = min(get_video_duration(SOURCE_VIDEO), MAX_DURATION)
    clip = OUTPUT / "source_clip.mp4"
    if not clip.exists():
        log.info("Extracting %ds clip...", duration)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(SOURCE_VIDEO), "-t", str(duration),
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(clip),
        ], check=True, capture_output=True)

    # Step 2: Generate dense reactions
    log.info("=== Generating dense reactions ===")
    reactions_file = OUTPUT / "reactions.json"
    if reactions_file.exists():
        reactions = json.loads(reactions_file.read_text(encoding="utf-8"))
        log.info("Loaded %d cached reactions", len(reactions))
    else:
        transcript = parse_srt(SOURCE_SRT)
        transcript_clip = [s for s in transcript if float(s.get("start", 0)) < duration]
        from peanut_reacts.decoded.dense_reactor import DenseReaction
        raw = generate_dense_reactions(
            transcript_clip, duration,
            LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY),
            reactions_per_minute=4.0,
        )
        reactions = [{"start": r.start, "text": r.text, "emotion": r.emotion, "intensity": r.intensity} for r in raw]
        reactions_file.write_text(json.dumps(reactions, indent=2), encoding="utf-8")
        log.info("Generated %d reactions", len(reactions))

    # Step 3: Synthesize TTS
    log.info("=== Synthesizing TTS ===")
    tts_dir = OUTPUT / "tts"
    tts_dir.mkdir(exist_ok=True)
    import time

    for i, r in enumerate(reactions):
        path = tts_dir / f"r_{i:04d}.mp3"
        if path.exists() and path.stat().st_size > 500:
            continue
        rate = "+10%" if r.get("intensity", 0.5) > 0.7 else "+0%"
        for attempt in range(3):
            try:
                c = edge_tts.Communicate(r["text"], "en-US-GuyNeural", rate=rate)
                asyncio.run(c.save(str(path)))
                break
            except:
                time.sleep(2)
        time.sleep(0.3)
    log.info("TTS: %d files", len(list(tts_dir.glob("r_*.mp3"))))

    # Step 4: Build reaction audio track
    log.info("=== Building reaction audio ===")
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

    # Step 5: Build AI emotion-switching facecam
    log.info("=== Building AI facecam track ===")
    facecam = OUTPUT / "facecam.mp4"
    if not facecam.exists():
        # Add duration to each reaction for the timeline
        for r in reactions:
            tts_path = tts_dir / f"r_{reactions.index(r):04d}.mp3"
            if tts_path.exists():
                try:
                    dur = float(subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(tts_path)],
                        capture_output=True, text=True,
                    ).stdout.strip())
                    r["duration"] = dur
                except:
                    r["duration"] = 3.0
            else:
                r["duration"] = 3.0

        timeline = build_emotion_timeline(reactions, duration, CLIPS_DIR)
        log.info("Timeline: %d segments", len(timeline))
        for t in timeline[:5]:
            log.info("  [%.0f-%.0fs] %s → %s", t["start"], t["end"], t["emotion"], Path(t["clip"]).name)

        render_facecam_track(timeline, facecam, duration, size=300)
    log.info("Facecam track ready")

    # Step 6: Final composite
    log.info("=== Final composite ===")
    final = OUTPUT / "AI_REACTION_FINAL.mp4"

    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(clip),
        "-stream_loop", "-1", "-i", str(facecam),
        "-i", str(reaction_audio),
        "-t", str(duration),
        "-filter_complex",
        "[1:v]colorkey=0x00FF00:0.25:0.15,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=3[peanut];"
        "[0:v][peanut]overlay=W-320:16[video];"
        "[0:a]volume=0.7[orig];"
        "[2:a]volume=2.5[react];"
        "[orig][react]amix=inputs=2:duration=first:dropout_transition=2[audio]",
        "-map", "[video]", "-map", "[audio]",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(final),
    ], check=True, capture_output=True)

    # Normalize
    final_loud = OUTPUT / "AI_REACTION_LOUD.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(final),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(final_loud),
    ], check=True, capture_output=True)

    dur = get_video_duration(final_loud)
    log.info("DONE! %s — %.1f min, %d reactions, AI facecam", final_loud.name, dur / 60, len(reactions))
    print(f"\nOutput: {final_loud}")
    print(f"Duration: {dur / 60:.1f} minutes")
    print(f"Reactions: {len(reactions)}")


if __name__ == "__main__":
    main()
