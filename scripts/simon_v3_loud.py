#!/usr/bin/env python3
"""Simon V3: LOUD talkative peanut with stats-driven commentary."""

import sys, io, json, os, asyncio, subprocess, time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.core.srt import parse_srt
from peanut_reacts.core.ffmpeg import get_video_duration
from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider
from peanut_reacts.core.logging_setup import build_logger
import edge_tts

log = build_logger("v3", verbose=True)
OUTPUT = Path("production/simon_v3")
OUTPUT.mkdir(parents=True, exist_ok=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")
compilation = Path("production/simon_compilation/simon_highlights.mp4")
duration = get_video_duration(compilation)
log.info("Source: %.1f min", duration / 60)

# Step 1: Generate DENSE talkative reactions
log.info("=== Step 1: Dense reactions ===")
reactions_file = OUTPUT / "reactions.json"

if reactions_file.exists():
    deduped = json.loads(reactions_file.read_text(encoding="utf-8"))
    log.info("Loaded %d cached reactions", len(deduped))
else:
    transcript = parse_srt(Path("downloads/AMONGUS/DUMBEST SIDEMEN AMONG US EVER.en.srt"))
    transcript_clip = [s for s in transcript if float(s.get("start", 0)) < duration]
    llm = create_llm_provider(LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY))

    all_reactions = []
    for chunk_start in range(0, int(duration), 60):
        chunk_end = min(chunk_start + 60, int(duration))
        chunk_segs = [s for s in transcript_clip if chunk_start <= float(s.get("start", 0)) < chunk_end]
        context = "\n".join(
            f"[{float(s['start']):.0f}s] {s.get('text', '').strip()}"
            for s in chunk_segs[:30]
        )

        prompt = (
            f"You are Peanut, a VERY talkative reactor watching Sidemen Among Us Simon compilation. "
            f"Generate 8 reactions for {chunk_start}s-{chunk_end}s. Talk NON-STOP like a real streamer.\n\n"
            f"TRANSCRIPT:\n{context}\n\n"
            f"SIMON STATS:\n"
            f"- 74.58% imposter win rate (best EVER)\n"
            f"- 2.41 kills per imposter game\n"
            f"- 52.51% overall win rate\n"
            f"- 1482 times accused but still wins\n"
            f"- Josh is his biggest rival\n\n"
            f"Mix: HYPE, ANALYSIS, FUNNY, PLAY-BY-PLAY, TRASH TALK, STATS\n"
            f"Keep 5-20 words each. Space 8-12 seconds apart.\n"
            f'JSON: [{{"start":{chunk_start+3},"text":"...","emotion":"excited","intensity":0.8}}]'
        )

        try:
            response = llm.complete(
                [{"role": "system", "content": "Return ONLY a JSON array of reactions."},
                 {"role": "user", "content": prompt}],
                temperature=0.95, max_tokens=1500,
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

    all_reactions.sort(key=lambda r: r["start"])
    deduped = []
    for r in all_reactions:
        if not deduped or r["start"] - deduped[-1]["start"] >= 6:
            deduped.append(r)

    reactions_file.write_text(json.dumps(deduped, indent=2), encoding="utf-8")
    log.info("Total: %d reactions (%.1f/min)", len(deduped), len(deduped) / (duration / 60))

# Step 2: TTS
log.info("=== Step 2: TTS ===")
tts_dir = OUTPUT / "tts"
tts_dir.mkdir(exist_ok=True)

for i, r in enumerate(deduped):
    path = tts_dir / f"r_{i:04d}.mp3"
    if path.exists() and path.stat().st_size > 500:
        continue
    rate = "-5%" if "analysis" in r.get("emotion", "") else ("+15%" if r.get("intensity", 0.5) > 0.7 else "+5%")
    for attempt in range(3):
        try:
            c = edge_tts.Communicate(r["text"], "en-US-GuyNeural", rate=rate)
            asyncio.run(c.save(str(path)))
            break
        except:
            time.sleep(2)
    time.sleep(0.3)

log.info("TTS: %d files", len(list(tts_dir.glob("r_*.mp3"))))

# Step 3: Reaction audio
log.info("=== Step 3: Reaction audio ===")
reaction_audio = OUTPUT / "reaction_audio.aac"
if not reaction_audio.exists():
    inputs = ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=44100:cl=stereo"]
    filters = []
    valid = 0
    for i, r in enumerate(deduped):
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

# Boost 5x
boosted = OUTPUT / "reaction_BOOST.aac"
subprocess.run([
    "ffmpeg", "-y", "-i", str(reaction_audio),
    "-af", "volume=5.0", "-c:a", "aac", "-b:a", "192k", str(boosted),
], check=True, capture_output=True)

# Step 4: Composite — gameplay at 15%, peanut FULL BLAST
log.info("=== Step 4: Composite ===")
facecam = Path("production/simon_compilation/facecam.mp4")
final = OUTPUT / "SIMON_V3.mp4"

subprocess.run([
    "ffmpeg", "-y",
    "-i", str(compilation),
    "-i", str(facecam),
    "-i", str(boosted),
    "-t", str(duration),
    "-filter_complex",
    "[1:v]colorkey=0x00FF00:0.25:0.15,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=3[peanut];"
    "[0:v][peanut]overlay=W-320:16[video];"
    "[0:a]volume=0.15[orig];"
    "[2:a]volume=1.5[react];"
    "[orig][react]amix=inputs=2:duration=first:dropout_transition=2[audio]",
    "-map", "[video]", "-map", "[audio]",
    "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "192k",
    str(final),
], check=True, capture_output=True)

# Normalize VERY LOUD
final_loud = OUTPUT / "SIMON_V3_LOUD.mp4"
subprocess.run([
    "ffmpeg", "-y", "-i", str(final),
    "-af", "loudnorm=I=-13:TP=-0.5:LRA=8",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    str(final_loud),
], check=True, capture_output=True)

dur = get_video_duration(final_loud)
log.info("DONE! %s — %.1f min, %d reactions (%.1f/min)",
         final_loud.name, dur / 60, len(deduped), len(deduped) / (dur / 60))
print(f"\nOutput: {final_loud}")
print(f"Duration: {dur / 60:.1f} min")
print(f"Reactions: {len(deduped)} ({len(deduped) / (dur / 60):.1f}/min)")
