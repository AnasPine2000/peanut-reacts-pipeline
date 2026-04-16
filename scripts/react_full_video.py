#!/usr/bin/env python3
"""React to FULL short video with maximum density — every moment commented on."""

import sys, io, json, os, asyncio, subprocess, time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.core.srt import parse_srt
from peanut_reacts.core.ffmpeg import get_video_duration
from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider
from peanut_reacts.core.logging_setup import build_logger
import edge_tts

log = build_logger("full_react", verbose=True)
OUTPUT = Path("production/full_react")
OUTPUT.mkdir(parents=True, exist_ok=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")

# Source: FUNNIEST AMONG US GAME (8.6 min)
SOURCE = Path("downloads/KSIAMONGUS/11 - FUNNIEST AMONG US GAME.mkv")
SOURCE_SRT = Path("downloads/KSIAMONGUS/11 - FUNNIEST AMONG US GAME.en.srt")


def main():
    duration = get_video_duration(SOURCE)
    log.info("Video: %s (%.1f min)", SOURCE.name, duration / 60)

    # Step 1: Convert source
    clip = OUTPUT / "source.mp4"
    if not clip.exists():
        log.info("Converting source...")
        subprocess.run([
            "ffmpeg", "-y", "-i", str(SOURCE),
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", str(clip),
        ], check=True, capture_output=True)

    # Step 2: Parse FULL transcript
    transcript = parse_srt(SOURCE_SRT)
    log.info("Transcript: %d segments", len(transcript))

    # Step 3: Generate ULTRA-DENSE reactions — react to EVERYTHING
    log.info("=== Generating ultra-dense reactions ===")
    reactions_file = OUTPUT / "reactions.json"

    if reactions_file.exists():
        all_reactions = json.loads(reactions_file.read_text(encoding="utf-8"))
        log.info("Loaded %d cached reactions", len(all_reactions))
    else:
        llm = create_llm_provider(LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY))
        all_reactions = []

        # Process in 30-second chunks for maximum context awareness
        for chunk_start in range(0, int(duration), 30):
            chunk_end = min(chunk_start + 30, int(duration))
            chunk_segs = [s for s in transcript if chunk_start <= float(s.get("start", 0)) < chunk_end]

            if not chunk_segs:
                continue

            context = "\n".join(
                f"[{float(s['start']):.0f}s] {s.get('text', '').strip()}"
                for s in chunk_segs[:20]
            )

            prompt = (
                f"You are Peanut watching KSI play Among Us (FUNNIEST AMONG US GAME episode).\n"
                f"Generate 5 reactions for {chunk_start}s-{chunk_end}s.\n\n"
                f"You are reacting TO WHAT IS HAPPENING ON SCREEN. Comment directly on:\n"
                f"- What players are saying/doing\n"
                f"- Kills, accusations, meetings, votes\n"
                f"- Funny moments, fails, clutch plays\n"
                f"- Reference specific people by name\n\n"
                f"TRANSCRIPT ({chunk_start}s-{chunk_end}s):\n{context}\n\n"
                f"STATS YOU KNOW:\n"
                f"- KSI: 1.42 KDR, 46.84% imposter win, 310 games played\n"
                f"- Simon: 74.58% imposter win rate (the GOAT)\n"
                f"- This episode is considered one of the funniest ever\n\n"
                f"React DIRECTLY to what you see in the transcript. Quote what they say.\n"
                f"Examples of good reactions:\n"
                f'- "Did he just say THAT?! Hahahaha"\n'
                f'- "KSI is about to get caught, watch this"\n'
                f'- "Oh no no no he killed right in front of everyone!"\n'
                f'- "The way Ethan just accused Simon... classic"\n'
                f'- "See this right here is why KSI has a 46 percent win rate"\n\n'
                f"Keep 5-20 words. Space 6-8 seconds apart.\n"
                f'JSON: [{{"start":{chunk_start+2},"text":"...","emotion":"excited","intensity":0.8}}]'
            )

            try:
                response = llm.complete(
                    [{"role": "system", "content": "Return ONLY a JSON array."},
                     {"role": "user", "content": prompt}],
                    temperature=0.95, max_tokens=1200,
                )
                text = response.strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()
                if not text.endswith("]"):
                    last = text.rfind("}")
                    if last > 0:
                        text = text[:last + 1] + "]"

                data = json.loads(text)
                added = 0
                for r in data:
                    s = float(r.get("start", chunk_start))
                    if chunk_start <= s < chunk_end:
                        all_reactions.append(r)
                        added += 1
                log.info("  %d-%ds: %d reactions", chunk_start, chunk_end, added)
            except Exception as e:
                log.warning("  %d-%ds failed: %s", chunk_start, chunk_end, str(e)[:60])

        # Deduplicate (min 5s apart)
        all_reactions.sort(key=lambda r: r["start"])
        deduped = []
        for r in all_reactions:
            if not deduped or r["start"] - deduped[-1]["start"] >= 5:
                deduped.append(r)
        all_reactions = deduped

        reactions_file.write_text(json.dumps(all_reactions, indent=2), encoding="utf-8")
        log.info("Total: %d reactions (%.1f/min)", len(all_reactions), len(all_reactions) / (duration / 60))

    # Step 4: TTS
    log.info("=== TTS ===")
    tts_dir = OUTPUT / "tts"
    tts_dir.mkdir(exist_ok=True)

    for i, r in enumerate(all_reactions):
        path = tts_dir / f"r_{i:04d}.mp3"
        if path.exists() and path.stat().st_size > 500:
            continue
        intensity = r.get("intensity", 0.5)
        rate = "+20%" if intensity > 0.8 else ("+10%" if intensity > 0.5 else "+0%")
        for attempt in range(3):
            try:
                c = edge_tts.Communicate(r["text"], "en-US-GuyNeural", rate=rate)
                asyncio.run(c.save(str(path)))
                break
            except:
                time.sleep(2)
        time.sleep(0.3)

    log.info("TTS: %d files", len(list(tts_dir.glob("r_*.mp3"))))

    # Step 5: Reaction audio (5x boosted)
    log.info("=== Reaction audio ===")
    reaction_audio = OUTPUT / "reaction_audio.aac"
    if not reaction_audio.exists():
        inputs = ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=44100:cl=stereo"]
        filters = []
        valid = 0
        for i, r in enumerate(all_reactions):
            tts = tts_dir / f"r_{i:04d}.mp3"
            if not tts.exists() or tts.stat().st_size < 500:
                continue
            inputs.extend(["-i", str(tts)])
            delay = int(r["start"] * 1000)
            filters.append(f"[{valid + 1}:a]aresample=44100,adelay={delay}|{delay}[a{valid}]")
            valid += 1
        mix = "[0:a]" + "".join(f"[a{i}]" for i in range(valid))
        filters.append(f"{mix}amix=inputs={valid + 1}:duration=first:dropout_transition=2[out]")
        subprocess.run(
            ["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(filters),
             "-map", "[out]", "-c:a", "aac", "-b:a", "192k", "-t", str(duration), str(reaction_audio)],
            check=True, capture_output=True,
        )

    boosted = OUTPUT / "reaction_BOOST.aac"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(reaction_audio),
        "-af", "volume=6.0", "-c:a", "aac", "-b:a", "192k", str(boosted),
    ], check=True, capture_output=True)

    # Step 6: AI facecam (loop talking clip)
    log.info("=== Facecam ===")
    facecam = OUTPUT / "facecam.mp4"
    if not facecam.exists():
        long_clip = Path("data/ai_peanut_clips/peanut_long_talking.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-stream_loop", str(int(duration / 10) + 2),
            "-i", str(long_clip), "-t", str(duration),
            "-vf", "scale=300:300:force_original_aspect_ratio=decrease,pad=300:300:(ow-iw)/2:(oh-ih)/2:color=0x00FF00",
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p", "-an",
            str(facecam),
        ], check=True, capture_output=True)

    # Step 7: Final composite — gameplay at 10%, peanut DOMINATES
    log.info("=== Final composite ===")
    final = OUTPUT / "FULL_REACT_FINAL.mp4"

    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(clip),
        "-i", str(facecam),
        "-i", str(boosted),
        "-t", str(duration),
        "-filter_complex",
        "[1:v]colorkey=0x00FF00:0.25:0.15,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=3[peanut];"
        "[0:v][peanut]overlay=W-320:16[video];"
        "[0:a]volume=0.10[orig];"
        "[2:a]volume=1.5[react];"
        "[orig][react]amix=inputs=2:duration=first:dropout_transition=2[audio]",
        "-map", "[video]", "-map", "[audio]",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(final),
    ], check=True, capture_output=True)

    # Normalize VERY LOUD
    final_loud = OUTPUT / "FULL_REACT_LOUD.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(final),
        "-af", "loudnorm=I=-12:TP=-0.5:LRA=7",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(final_loud),
    ], check=True, capture_output=True)

    dur = get_video_duration(final_loud)
    log.info("DONE! %s — %.1f min, %d reactions (%.1f/min)",
             final_loud.name, dur / 60, len(all_reactions), len(all_reactions) / (dur / 60))
    print(f"\nOutput: {final_loud}")
    print(f"Duration: {dur / 60:.1f} min")
    print(f"Reactions: {len(all_reactions)} ({len(all_reactions) / (dur / 60):.1f}/min)")


if __name__ == "__main__":
    main()
