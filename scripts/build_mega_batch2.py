#!/usr/bin/env python3
"""Build 5+ hour mega reaction: full episodes back-to-back with peanut reacting."""

import sys, io, json, os, asyncio, subprocess, time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.core.srt import parse_srt
from peanut_reacts.core.ffmpeg import get_video_duration, concatenate
from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider
from peanut_reacts.core.logging_setup import build_logger
import edge_tts

log = build_logger("mega", verbose=True)
OUTPUT = Path("production/mega_batch2")
OUTPUT.mkdir(parents=True, exist_ok=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")

# Use FIRST 3 KSI Among Us episodes for the first batch (~40 min)
VIDEOS = [
    {"path": "downloads/KSIAMONGUS/04 - I'M AN IDIOT.mkv", "srt": "downloads/KSIAMONGUS/04 - I'M AN IDIOT.en.srt"},
    {"path": "downloads/KSIAMONGUS/05 - Folabi Plays Among Us.mkv", "srt": "downloads/KSIAMONGUS/05 - Folabi Plays Among Us.en.srt"},
    {"path": "downloads/KSIAMONGUS/06 - Cheating In Among Us？.mkv", "srt": "downloads/KSIAMONGUS/06 - Cheating In Among Us？.en.srt"},
    {"path": "downloads/KSIAMONGUS/07 - I Paid Money To Win In Among Us.mkv", "srt": "downloads/KSIAMONGUS/07 - I Paid Money To Win In Among Us.en.srt"},
    {"path": "downloads/KSIAMONGUS/08 - AMONG US BUT THE WHEEL DECIDES WHAT I SAY.mkv", "srt": "downloads/KSIAMONGUS/08 - AMONG US BUT THE WHEEL DECIDES WHAT I SAY.en.srt"},
]


def main():
    log.info("=== MEGA REACTION: FULL EPISODES + PEANUT ===")

    # Step 1: Concatenate full episodes (no cutting!)
    log.info("=== Step 1: Concatenating full episodes ===")
    concat_source = OUTPUT / "full_episodes.mp4"

    if not concat_source.exists():
        # Convert each to mp4 first
        mp4s = []
        for i, v in enumerate(VIDEOS):
            mp4 = OUTPUT / f"ep_{i:02d}.mp4"
            if not mp4.exists():
                log.info("  Converting %s...", Path(v["path"]).name)
                subprocess.run([
                    "ffmpeg", "-y", "-i", v["path"],
                    "-c:v", "libx264", "-crf", "23", "-preset", "fast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k",
                    str(mp4),
                ], check=True, capture_output=True)
            mp4s.append(mp4)

        log.info("  Concatenating %d episodes...", len(mp4s))
        concatenate(mp4s, concat_source)

    duration = get_video_duration(concat_source)
    log.info("Full episodes: %.1f min (%.1f hours)", duration / 60, duration / 3600)

    # Step 2: Generate reactions for FULL duration
    log.info("=== Step 2: Dense reactions for full episodes ===")
    reactions_file = OUTPUT / "reactions.json"

    if reactions_file.exists():
        all_reactions = json.loads(reactions_file.read_text(encoding="utf-8"))
        log.info("Loaded %d cached reactions", len(all_reactions))
    else:
        # Parse all transcripts
        all_transcript = []
        offset = 0
        for v in VIDEOS:
            srt_segs = parse_srt(Path(v["srt"]))
            ep_dur = get_video_duration(Path(v["path"]))
            for s in srt_segs:
                s["start"] = str(float(s["start"]) + offset)
                s["end"] = str(float(s["end"]) + offset)
            all_transcript.extend(srt_segs)
            offset += ep_dur

        log.info("Combined transcript: %d segments", len(all_transcript))

        llm = create_llm_provider(LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY))
        all_reactions = []

        # Process in 60-second chunks
        for chunk_start in range(0, int(duration), 60):
            chunk_end = min(chunk_start + 60, int(duration))
            chunk_segs = [s for s in all_transcript if chunk_start <= float(s.get("start", 0)) < chunk_end]

            if not chunk_segs:
                continue

            context = "\n".join(
                f"[{float(s['start']):.0f}s] {s.get('text', '').strip()}"
                for s in chunk_segs[:25]
            )

            prompt = (
                f"You are Peanut watching KSI play Among Us. Generate 6 reactions for {chunk_start}s-{chunk_end}s.\n"
                f"React DIRECTLY to what is happening. Comment on kills, meetings, accusations, funny moments.\n"
                f"Reference players by name. Be a talkative friend watching along.\n\n"
                f"TRANSCRIPT:\n{context}\n\n"
                f"Mix: hype, analysis, funny, play-by-play, shocked reactions.\n"
                f"Keep 5-20 words. Space 8-12 seconds apart.\n"
                f'JSON: [{{"start":{chunk_start+3},"text":"...","emotion":"excited","intensity":0.8}}]'
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
                for r in data:
                    s = float(r.get("start", chunk_start))
                    if chunk_start <= s < chunk_end:
                        all_reactions.append(r)
                log.info("  %d-%ds: %d reactions", chunk_start, chunk_end, len(data))
            except Exception as e:
                log.warning("  %d-%ds failed: %s", chunk_start, chunk_end, str(e)[:60])

        # Deduplicate
        all_reactions.sort(key=lambda r: r["start"])
        deduped = []
        for r in all_reactions:
            if not deduped or r["start"] - deduped[-1]["start"] >= 5:
                deduped.append(r)
        all_reactions = deduped

        reactions_file.write_text(json.dumps(all_reactions, indent=2), encoding="utf-8")
        log.info("Total: %d reactions (%.1f/min)", len(all_reactions), len(all_reactions) / (duration / 60))

    # Step 3: TTS
    log.info("=== Step 3: TTS (%d reactions) ===", len(all_reactions))
    tts_dir = OUTPUT / "tts"
    tts_dir.mkdir(exist_ok=True)

    for i, r in enumerate(all_reactions):
        path = tts_dir / f"r_{i:04d}.mp3"
        if path.exists() and path.stat().st_size > 500:
            continue
        rate = "+15%" if r.get("intensity", 0.5) > 0.7 else "+5%"
        for attempt in range(3):
            try:
                c = edge_tts.Communicate(r["text"], "en-US-GuyNeural", rate=rate)
                asyncio.run(c.save(str(path)))
                break
            except:
                time.sleep(2)
        time.sleep(0.3)
        if i % 50 == 0:
            log.info("  TTS: %d/%d", i, len(all_reactions))

    # Step 4: Reaction audio
    log.info("=== Step 4: Reaction audio ===")
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

    # Boost 6x
    boosted = OUTPUT / "reaction_BOOST.aac"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(reaction_audio),
        "-af", "volume=6.0", "-c:a", "aac", "-b:a", "192k", str(boosted),
    ], check=True, capture_output=True)

    # Step 5: AI facecam (loop talking clip for full duration)
    log.info("=== Step 5: AI facecam ===")
    facecam = OUTPUT / "facecam.mp4"
    if not facecam.exists():
        long_clip = Path("data/ai_peanut_clips/peanut_long_talking.mp4")
        loops = int(duration / 10) + 2
        subprocess.run([
            "ffmpeg", "-y", "-stream_loop", str(loops),
            "-i", str(long_clip), "-t", str(duration),
            "-vf", "scale=300:300:force_original_aspect_ratio=decrease,pad=300:300:(ow-iw)/2:(oh-ih)/2:color=0x00FF00",
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p", "-an",
            str(facecam),
        ], check=True, capture_output=True)

    # Step 6: Final composite
    log.info("=== Step 6: Final composite ===")
    final = OUTPUT / "MEGA_REACTION.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(concat_source),
        "-i", str(facecam),
        "-i", str(boosted),
        "-t", str(duration),
        "-filter_complex",
        "[1:v]colorkey=0x00FF00:0.25:0.15,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=3[peanut];"
        "[0:v][peanut]overlay=W-320:16[video];"
        "[0:a]volume=0.12[orig];"
        "[2:a]volume=1.5[react];"
        "[orig][react]amix=inputs=2:duration=first:dropout_transition=2[audio]",
        "-map", "[video]", "-map", "[audio]",
        "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(final),
    ], check=True, capture_output=True)

    # Normalize
    final_loud = OUTPUT / "MEGA_REACTION_FINAL.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(final),
        "-af", "loudnorm=I=-13:TP=-0.5:LRA=8",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(final_loud),
    ], check=True, capture_output=True)

    dur = get_video_duration(final_loud)
    log.info("DONE! %s — %.1f min (%.1f hours), %d reactions",
             final_loud.name, dur / 60, dur / 3600, len(all_reactions))
    print(f"\nOutput: {final_loud}")
    print(f"Duration: {dur / 60:.1f} min ({dur / 3600:.1f} hours)")
    print(f"Reactions: {len(all_reactions)}")


if __name__ == "__main__":
    main()
