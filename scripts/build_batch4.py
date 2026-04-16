#!/usr/bin/env python3
"""Build batch 4: Sidemen Among Us episodes (~100 min)."""

import sys, io, json, os, asyncio, subprocess, time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.core.ffmpeg import get_video_duration, concatenate
from peanut_reacts.core.srt import parse_srt
from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider
from peanut_reacts.core.logging_setup import build_logger
import edge_tts

log = build_logger("batch4", verbose=True)
OUTPUT = Path("production/mega_batch4")
OUTPUT.mkdir(parents=True, exist_ok=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")


def main():
    selection = json.loads(Path("production/mega_batch4_selection.json").read_text(encoding="utf-8"))
    log.info("Batch 4: %d Sidemen videos", len(selection))

    # Step 1: Convert and concatenate
    log.info("=== Step 1: Converting ===")
    mp4s = []
    for i, v in enumerate(selection):
        mp4 = OUTPUT / f"ep_{i:02d}.mp4"
        if not mp4.exists():
            log.info("  Converting %s...", Path(v["path"]).name[:50])
            subprocess.run([
                "ffmpeg", "-y", "-i", v["path"],
                "-c:v", "libx264", "-crf", "23", "-preset", "fast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", str(mp4),
            ], check=True, capture_output=True)
        mp4s.append(mp4)

    concat = OUTPUT / "full_episodes.mp4"
    if not concat.exists():
        log.info("Concatenating %d episodes...", len(mp4s))
        concatenate(mp4s, concat)

    duration = get_video_duration(concat)
    log.info("Duration: %.1f min", duration / 60)

    # Step 2: Reactions
    log.info("=== Step 2: Reactions ===")
    reactions_file = OUTPUT / "reactions.json"
    if reactions_file.exists():
        deduped = json.loads(reactions_file.read_text(encoding="utf-8"))
        log.info("Cached: %d reactions", len(deduped))
    else:
        all_transcript = []
        offset = 0
        for v in selection:
            srt_path = Path(v["path"]).with_suffix(".en.srt")
            if not srt_path.exists():
                srt_name = Path(v["path"]).stem + ".en.srt"
                srt_path = Path(v["path"]).parent / srt_name
            if not srt_path.exists():
                log.warning("  No SRT for %s", Path(v["path"]).name[:40])
                offset += v["duration_min"] * 60
                continue

            segs = parse_srt(srt_path)
            for s in segs:
                s["start"] = str(float(s["start"]) + offset)
                s["end"] = str(float(s["end"]) + offset)
            all_transcript.extend(segs)
            offset += v["duration_min"] * 60
            log.info("  SRT: %s (%d segs)", srt_path.name[:40], len(segs))

        log.info("Transcript: %d segments", len(all_transcript))

        llm = create_llm_provider(LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY))
        all_reactions = []

        for chunk_start in range(0, int(duration), 60):
            chunk_end = min(chunk_start + 60, int(duration))
            chunk_segs = [
                s for s in all_transcript
                if chunk_start <= float(s.get("start", 0)) < chunk_end
            ]
            if not chunk_segs:
                continue

            context = "\n".join(
                f"[{float(s['start']):.0f}s] {s.get('text', '').strip()}"
                for s in chunk_segs[:25]
            )

            prompt = (
                f"Peanut reacts to Sidemen Among Us. 6 reactions for {chunk_start}s-{chunk_end}s. "
                f"React to what happens. Reference players by name. 5-20 words each. 8-12s apart.\n"
                f"TRANSCRIPT:\n{context}\n"
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

                for r in json.loads(text):
                    s = float(r.get("start", chunk_start))
                    if chunk_start <= s < chunk_end:
                        all_reactions.append(r)
            except:
                pass

        all_reactions.sort(key=lambda r: r["start"])
        deduped = [all_reactions[0]] if all_reactions else []
        for r in all_reactions[1:]:
            if r["start"] - deduped[-1]["start"] >= 5:
                deduped.append(r)

        reactions_file.write_text(json.dumps(deduped, indent=2), encoding="utf-8")
        log.info("Reactions: %d (%.1f/min)", len(deduped), len(deduped) / (duration / 60))

    # Step 3: TTS
    log.info("=== Step 3: TTS ===")
    tts_dir = OUTPUT / "tts"
    tts_dir.mkdir(exist_ok=True)
    for i, r in enumerate(deduped):
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

    # Step 4: Batch audio
    log.info("=== Step 4: Audio ===")
    reaction_audio = OUTPUT / "reaction_audio.aac"
    if not reaction_audio.exists():
        batch_files = []
        for bs in range(0, len(deduped), 50):
            be = min(bs + 50, len(deduped))
            ba = OUTPUT / f"batch_{bs:04d}.aac"
            if ba.exists():
                batch_files.append(ba)
                continue
            inputs = ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=44100:cl=stereo"]
            filters = []
            valid = 0
            for i, r in enumerate(deduped[bs:be]):
                tts = tts_dir / f"r_{bs + i:04d}.mp3"
                if not tts.exists() or tts.stat().st_size < 500:
                    continue
                inputs.extend(["-i", str(tts)])
                delay = int(r["start"] * 1000)
                filters.append(f"[{valid + 1}:a]aresample=44100,adelay={delay}|{delay}[a{valid}]")
                valid += 1
            if valid == 0:
                continue
            mix = "[0:a]" + "".join(f"[a{i}]" for i in range(valid))
            filters.append(f"{mix}amix=inputs={valid + 1}:duration=first:dropout_transition=2[out]")
            subprocess.run(
                ["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(filters),
                 "-map", "[out]", "-c:a", "aac", "-b:a", "192k", "-t", str(duration), str(ba)],
                check=True, capture_output=True,
            )
            batch_files.append(ba)

        if len(batch_files) == 1:
            import shutil
            shutil.copy2(str(batch_files[0]), str(reaction_audio))
        elif batch_files:
            inputs = []
            for f in batch_files:
                inputs.extend(["-i", str(f)])
            n = len(batch_files)
            mix = "".join(f"[{i}:a]" for i in range(n))
            subprocess.run([
                "ffmpeg", "-y"] + inputs + [
                "-filter_complex", f"{mix}amix=inputs={n}:duration=first:dropout_transition=2[out]",
                "-map", "[out]", "-c:a", "aac", "-b:a", "192k", "-t", str(duration), str(reaction_audio),
            ], check=True, capture_output=True)

    boosted = OUTPUT / "reaction_BOOST.aac"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(reaction_audio),
        "-af", "volume=6.0", "-c:a", "aac", "-b:a", "192k", str(boosted),
    ], check=True, capture_output=True)

    # Step 5: Facecam
    facecam = OUTPUT / "facecam.mp4"
    if not facecam.exists():
        subprocess.run([
            "ffmpeg", "-y", "-stream_loop", str(int(duration / 10) + 2),
            "-i", "data/ai_peanut_clips/peanut_long_talking.mp4",
            "-t", str(duration),
            "-vf", "scale=300:300:force_original_aspect_ratio=decrease,pad=300:300:(ow-iw)/2:(oh-ih)/2:color=0x00FF00",
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p", "-an",
            str(facecam),
        ], check=True, capture_output=True)

    # Step 6: Composite
    log.info("=== Step 6: Composite ===")
    final = OUTPUT / "BATCH4_FINAL.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(concat), "-i", str(facecam), "-i", str(boosted),
        "-t", str(duration),
        "-filter_complex",
        "[1:v]colorkey=0x00FF00:0.25:0.15,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=3[peanut];"
        "[0:v][peanut]overlay=W-320:16[video];"
        "[0:a]volume=0.12[orig];[2:a]volume=1.5[react];"
        "[orig][react]amix=inputs=2:duration=first:dropout_transition=2[audio]",
        "-map", "[video]", "-map", "[audio]",
        "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(final),
    ], check=True, capture_output=True)

    final_loud = OUTPUT / "BATCH4_LOUD.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(final),
        "-af", "loudnorm=I=-13:TP=-0.5:LRA=8",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(final_loud),
    ], check=True, capture_output=True)

    dur = get_video_duration(final_loud)
    log.info("DONE! %s — %.1f min, %d reactions", final_loud.name, dur / 60, len(deduped))
    print(f"\nOutput: {final_loud}")
    print(f"Duration: {dur / 60:.1f} min")
    print(f"Reactions: {len(deduped)}")


if __name__ == "__main__":
    main()
