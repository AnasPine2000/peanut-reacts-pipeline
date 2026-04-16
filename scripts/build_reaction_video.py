#!/usr/bin/env python3
"""Build a full reaction video: Sidemen gameplay + peanut character reacting live."""

import sys, io, json, os, asyncio, subprocess, random
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.decoded.dense_reactor import generate_dense_reactions
from peanut_reacts.core.srt import parse_srt
from peanut_reacts.core.ffmpeg import get_video_duration
from peanut_reacts.character.reaction_generator import LLMConfig
from peanut_reacts.core.logging_setup import build_logger
import edge_tts

log = build_logger("reaction", verbose=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")

# Config
SOURCE_VIDEO = Path("downloads/AMONGUS/DUMBEST SIDEMEN AMONG US EVER.webm")
SOURCE_SRT = Path("downloads/AMONGUS/DUMBEST SIDEMEN AMONG US EVER.en.srt")
PEANUT_IMAGE = Path("assets/peanut_face/burnt_peanut_v1.png")
OUTPUT = Path("production/reaction_full")
OUTPUT.mkdir(parents=True, exist_ok=True)

# Limit to first 10 minutes for testing
MAX_DURATION = 600  # 10 min


def main():
    log.info("=== FULL REACTION VIDEO PIPELINE ===")

    # Step 1: Get video info
    full_duration = get_video_duration(SOURCE_VIDEO)
    duration = min(full_duration, MAX_DURATION)
    log.info("Video: %s (%.1f min, using %.1f min)", SOURCE_VIDEO.name, full_duration / 60, duration / 60)

    # Step 2: Extract clip
    log.info("=== Step 2: Extracting clip ===")
    clip = OUTPUT / "source_clip.mp4"
    if not clip.exists():
        subprocess.run([
            "ffmpeg", "-y", "-i", str(SOURCE_VIDEO),
            "-t", str(duration),
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(clip),
        ], check=True, capture_output=True)
    log.info("Clip: %s", clip)

    # Step 3: Generate dense reactions
    log.info("=== Step 3: Generating dense reactions ===")
    transcript = parse_srt(SOURCE_SRT)
    transcript_clip = [s for s in transcript if float(s.get("start", 0)) < duration]

    reactions_file = OUTPUT / "reactions.json"
    if reactions_file.exists():
        reactions_data = json.loads(reactions_file.read_text(encoding="utf-8"))
        log.info("Loaded %d cached reactions", len(reactions_data))
    else:
        from peanut_reacts.decoded.dense_reactor import DenseReaction
        reactions = generate_dense_reactions(
            transcript_clip, duration,
            LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY),
            reactions_per_minute=4.0,
        )
        reactions_data = [{"start": r.start, "text": r.text, "emotion": r.emotion, "intensity": r.intensity} for r in reactions]
        reactions_file.write_text(json.dumps(reactions_data, indent=2), encoding="utf-8")
        log.info("Generated %d reactions", len(reactions_data))

    # Step 4: Synthesize TTS for all reactions
    log.info("=== Step 4: Synthesizing TTS ===")
    tts_dir = OUTPUT / "tts"
    tts_dir.mkdir(exist_ok=True)

    import time
    for i, r in enumerate(reactions_data):
        path = tts_dir / f"r_{i:04d}.mp3"
        if path.exists() and path.stat().st_size > 500:
            continue

        text = r["text"]
        # Adjust voice for emotion
        rate = "+5%"
        pitch = "+0Hz"
        if r.get("intensity", 0.5) > 0.7:
            rate = "+15%"
            pitch = "+5Hz"
        elif r.get("emotion") == "sarcastic":
            rate = "-5%"

        for attempt in range(3):
            try:
                c = edge_tts.Communicate(text, "en-US-GuyNeural", rate=rate, pitch=pitch)
                asyncio.run(c.save(str(path)))
                break
            except:
                time.sleep(2)

        if i % 10 == 0:
            log.info("  TTS: %d/%d", i, len(reactions_data))
        time.sleep(0.3)

    log.info("TTS complete: %d files", len(list(tts_dir.glob("r_*.mp3"))))

    # Step 5: Build reaction audio track (all TTS at correct offsets)
    log.info("=== Step 5: Building reaction audio track ===")
    reaction_audio = OUTPUT / "reaction_audio.aac"

    if not reaction_audio.exists():
        # Build inputs + adelay filter
        inputs = ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=44100:cl=stereo"]
        filters = []
        valid = 0

        for i, r in enumerate(reactions_data):
            tts_path = tts_dir / f"r_{i:04d}.mp3"
            if not tts_path.exists() or tts_path.stat().st_size < 500:
                continue
            input_idx = valid + 1
            inputs.extend(["-i", str(tts_path)])
            delay_ms = int(r["start"] * 1000)
            filters.append(f"[{input_idx}:a]aresample=44100,adelay={delay_ms}|{delay_ms}[a{valid}]")
            valid += 1

        if valid == 0:
            log.error("No valid TTS files!")
            return

        mix_inputs = "[0:a]" + "".join(f"[a{i}]" for i in range(valid))
        filters.append(f"{mix_inputs}amix=inputs={valid + 1}:duration=first:dropout_transition=2[out]")

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", ";".join(filters),
            "-map", "[out]", "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration), str(reaction_audio),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    log.info("Reaction audio: %s", reaction_audio)

    # Step 6: Create peanut idle video (looping, always visible)
    log.info("=== Step 6: Creating peanut facecam ===")
    peanut_loop = OUTPUT / "peanut_loop.mp4"
    if not peanut_loop.exists():
        # Animated idle with sway + bob
        cs = 300
        pad = cs + 60
        subprocess.run([
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(PEANUT_IMAGE),
            "-t", str(duration), "-r", "25",
            "-vf", (
                f"scale={pad}:{pad}:force_original_aspect_ratio=decrease,"
                f"pad={pad}:{pad}:(ow-iw)/2:(oh-ih)/2:color=0x00FF00,"
                f"crop=w={cs}:h={cs}"
                f":x='{(pad-cs)//2}+12*sin(2*PI*t/2)'"
                f":y='{(pad-cs)//2}+8*sin(2*PI*t/1.5)',"
                f"rotate='0.02*sin(2*PI*t/2.5)':fillcolor=0x00FF00:ow={cs}:oh={cs}"
            ),
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
            str(peanut_loop),
        ], check=True, capture_output=True)
    log.info("Peanut loop: %s", peanut_loop)

    # Step 7: Final composite
    log.info("=== Step 7: Final composite ===")
    final = OUTPUT / "REACTION_FINAL.mp4"

    # Layout: gameplay full screen + peanut top-right + reaction audio mixed
    # Peanut with border frame
    filter_complex = (
        # Peanut: remove green screen, add border
        "[1:v]colorkey=0x00FF00:0.25:0.15,"
        "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=3[peanut];"
        # Overlay peanut on gameplay (top-right corner)
        "[0:v][peanut]overlay=W-320:16[video];"
        # Mix audio: original (ducked during reactions) + reaction voice
        "[0:a]volume=0.7[orig];"
        "[2:a]volume=2.5[react];"
        "[orig][react]amix=inputs=2:duration=first:dropout_transition=2[audio]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip),
        "-stream_loop", "-1", "-i", str(peanut_loop),
        "-i", str(reaction_audio),
        "-t", str(duration),
        "-filter_complex", filter_complex,
        "-map", "[video]", "-map", "[audio]",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(final),
    ]

    subprocess.run(cmd, check=True, capture_output=True)

    # Normalize audio
    final_loud = OUTPUT / "REACTION_LOUD.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(final),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(final_loud),
    ], check=True, capture_output=True)

    dur = get_video_duration(final_loud)
    log.info("DONE! %s — %.1f min, %d reactions", final_loud.name, dur / 60, len(reactions_data))
    print(f"\nOutput: {final_loud}")
    print(f"Duration: {dur / 60:.1f} minutes")
    print(f"Reactions: {len(reactions_data)}")


if __name__ == "__main__":
    main()
