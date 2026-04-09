#!/usr/bin/env python3
"""Generate Simon/Miniminter deep analysis video."""

import sys, io, json, os, random, asyncio, subprocess
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.decoded.retention_editor import fast_cut_segment, enhanced_data_card, _get_clip_duration
from peanut_reacts.core.ffmpeg import concatenate
from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider
import edge_tts

log = build_logger("simon", verbose=True)

OUTPUT = Path("production/simon_decoded")
OUTPUT.mkdir(parents=True, exist_ok=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")

# Load all data
analysis = json.loads(Path("data/transcripts/simon_full_analysis.json").read_text(encoding="utf-8"))
fan = analysis["fan_stats"]
deep = analysis["deep_analysis"]
basic = analysis["basic_report"]
clip_manifest = json.loads(Path("data/clips/simon/manifest.json").read_text(encoding="utf-8"))

# Organize clips by category
clips_by_cat = {}
for c in clip_manifest:
    cat = c["category"]
    if cat not in clips_by_cat:
        clips_by_cat[cat] = []
    clips_by_cat[cat].append(Path(c["path"]))

# Also load general clips
general_clips = sorted(Path("data/clips").glob("clip_*.mp4"))
all_simon_clips = [Path(c["path"]) for c in clip_manifest] + general_clips
log.info("Clips: %d Simon + %d general", len(clip_manifest), len(general_clips))


def main():
    # ── Step 1: Generate script ──
    log.info("=== Step 1: Generating script ===")
    llm = create_llm_provider(LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY))

    data_for_llm = json.dumps({
        "player": "Simon (Miniminter)",
        "fan_stats": {
            "games": fan.get("games_played", 358),
            "win_rate": f"{fan.get('win_pct', 52.51)}% (BEST of all Sidemen)",
            "kdr": fan.get("kdr", 0.9),
            "imposter_win_rate": f"{fan.get('imposter_win_pct', 74.58)}% (BEST — wins 3 out of 4 imposter games)",
            "kills_per_imposter": f"{fan.get('kills_per_imposter_game', 2.41)} (HIGHEST — deadliest imposter)",
            "crewmate_win_rate": f"{fan.get('crewmate_win_pct', 50.92)}% (BEST as crewmate too)",
            "imposter_games": fan.get("imposter_games", 59),
            "imposter_wins": fan.get("imposter_wins", 44),
        },
        "deep_insights": deep["insights"],
        "emotional_profile": deep["emotional_profile"],
        "rivalries": {
            "Simon-Josh": "326 co-mentions (strongest dynamic)",
            "Simon-KSI": "227 co-mentions",
            "Simon-Harry": "180 co-mentions",
            "Simon-Ethan": "153 co-mentions",
        },
        "total_mentions": basic["total_mentions"],
        "videos_appeared": basic["videos_appeared_in"],
        "times_accused": basic["times_accused_by_others"],
        "roles": basic["roles_played"],
        "comparison": {
            "Simon_imposter": "74.58%",
            "Vik_imposter": "58.97%",
            "Harry_imposter": "47.17%",
            "KSI_imposter": "46.84%",
            "Deji_imposter": "35.42% (worst)",
        },
        "clip_categories": {
            "kills": f"{len(clips_by_cat.get('kills', []))} clips of Simon killing",
            "accused": f"{len(clips_by_cat.get('accused', []))} clips of Simon being accused",
            "defending": f"{len(clips_by_cat.get('defending', []))} clips of Simon defending himself",
            "winning": f"{len(clips_by_cat.get('winning', []))} clips of Simon winning",
            "caught": f"{len(clips_by_cat.get('caught', []))} clips of Simon getting caught",
        },
        "key_speech_samples": basic["speech_samples"][:15],
    }, indent=2)

    prompt = (
        "Write a VIRAL 15-minute YouTube video essay: 'Why Simon is UNBEATABLE in Sidemen Among Us'\n\n"
        "Channel: 'Sidemen AmongUs Decoded'\n\n"
        "REAL DATA:\n" + data_for_llm + "\n\n"
        "This video reveals HOW Simon dominates with a 74.58% imposter win rate.\n\n"
        "Structure (16-20 sections, 900-1200 seconds total):\n"
        "Part 1: THE SHOCKING STAT (74.58% intro + comparison) — 3 sections\n"
        "Part 2: THE KILL ANALYSIS (2.41 kills/game, how he gets away with it) — 3 sections\n"
        "Part 3: THE DEFENSE STRATEGY (101 defensive moments analyzed) — 3 sections\n"
        "Part 4: THE JOSH RIVALRY (326 co-mentions, his biggest threat) — 3 sections\n"
        "Part 5: WHY NOBODY CAN STOP HIM (the psychology + data) — 3 sections\n"
        "Part 6: THE VERDICT — is Simon the greatest Among Us player ever? — 2 sections\n\n"
        "IMPORTANT: Reference the clip categories. When discussing kills, say 'as we can see in this clip'. "
        "When discussing accusations, reference the 145 accusation moments. "
        "Use SPECIFIC numbers from the data. Make it feel like a sports documentary.\n\n"
        'JSON: {"title":"viral title under 70 chars","description":"...","tags":[...],'
        '"sections":[{"section_type":"intro|data_reveal|clip_moment|analysis|comparison|outro",'
        '"narration":"60-90 seconds per section","visual_note":"...","duration_estimate":60,'
        '"clip_category":"kills|accused|defending|winning|caught|drama|general",'
        '"emotion":"neutral|shocked|excited"}]}'
    )

    response = llm.complete(
        [{"role": "system", "content": "You write viral YouTube video essays analyzing gaming data. Return ONLY valid JSON."},
         {"role": "user", "content": prompt}],
        temperature=0.9, max_tokens=8000,
    )

    text = response.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    script = json.loads(text)
    sections = script.get("sections", [])
    total = sum(s.get("duration_estimate", 50) for s in sections)
    log.info('Script: "%s" — %d sections, ~%ds (%.1f min)', script["title"], len(sections), total, total / 60)
    (OUTPUT / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Step 2: Narrate ──
    log.info("=== Step 2: Narrating ===")
    narr_dir = OUTPUT / "narration"
    narr_dir.mkdir(exist_ok=True)

    async def narrate_all():
        for i, sec in enumerate(sections):
            text = sec.get("narration", "").strip()
            if not text:
                continue
            path = narr_dir / f"sec_{i:02d}.mp3"
            communicate = edge_tts.Communicate(text, "en-US-GuyNeural", rate="-5%", pitch="-10Hz")
            await communicate.save(str(path))
            dur = _get_clip_duration(path)
            log.info("  Sec %d (%s): %.1fs", i, sec.get("section_type", "?"), dur)

    asyncio.run(narrate_all())

    # ── Step 3: Build segments with fast cuts ──
    log.info("=== Step 3: Building retention-optimized segments ===")
    seg_dir = OUTPUT / "segments"
    seg_dir.mkdir(exist_ok=True)

    segment_videos = []
    for i, sec in enumerate(sections):
        narr = narr_dir / f"sec_{i:02d}.mp3"
        if not narr.exists():
            continue

        seg_path = seg_dir / f"seg_{i:02d}.mp4"
        sec_type = sec.get("section_type", "analysis")
        clip_cat = sec.get("clip_category", "general")

        try:
            if sec_type in ("data_reveal", "comparison"):
                if sec_type == "data_reveal":
                    stats = {
                        "Win Rate": f"{fan.get('win_pct', 52.51)}%",
                        "Imposter Win": f"{fan.get('imposter_win_pct', 74.58)}%",
                        "Kills/Game": str(fan.get("kills_per_imposter_game", 2.41)),
                        "Crewmate Win": f"{fan.get('crewmate_win_pct', 50.92)}%",
                    }
                    card_title = "SIMON STATS"
                else:
                    stats = {
                        "Simon": "74.58%",
                        "Vik": "58.97%",
                        "Harry": "47.17%",
                        "KSI": "46.84%",
                        "Deji": "35.42%",
                    }
                    card_title = "IMPOSTER WIN RATE"

                bg = random.choice(clips_by_cat.get("kills", []) or all_simon_clips)
                card = seg_dir / f"card_{i:02d}.mp4"
                enhanced_data_card(stats, card_title, card, background_clip=bg, duration=8.0)

                narr_dur = _get_clip_duration(narr)
                remaining = max(0, narr_dur - 7.0)

                if remaining > 2:
                    # Fast cuts for remaining duration
                    pool = clips_by_cat.get(clip_cat, []) or all_simon_clips
                    rem = seg_dir / f"rem_{i:02d}.mp4"
                    fast_cut_segment(random.sample(pool, min(5, len(pool))), narr, rem, cut_interval=3.5)
                    rem_trim = seg_dir / f"remt_{i:02d}.mp4"
                    subprocess.run(["ffmpeg", "-y", "-i", str(rem), "-t", str(remaining), "-c", "copy", str(rem_trim)], capture_output=True)

                    combined = seg_dir / f"comb_{i:02d}.mp4"
                    concatenate([card, rem_trim], combined)
                    subprocess.run([
                        "ffmpeg", "-y", "-i", str(combined), "-i", str(narr),
                        "-map", "0:v", "-map", "1:a", "-t", str(narr_dur + 1),
                        "-c:v", "copy", "-c:a", "aac", str(seg_path),
                    ], check=True, capture_output=True)

                    for f in [rem, rem_trim, combined]:
                        try: f.unlink(missing_ok=True)
                        except: pass
                else:
                    subprocess.run([
                        "ffmpeg", "-y", "-i", str(card), "-i", str(narr),
                        "-map", "0:v", "-map", "1:a", "-shortest",
                        "-c:v", "copy", "-c:a", "aac", str(seg_path),
                    ], check=True, capture_output=True)

                card.unlink(missing_ok=True)

            else:
                # Fast cuts with category-specific clips
                pool = clips_by_cat.get(clip_cat, [])
                if not pool:
                    pool = all_simon_clips
                clip_pool = random.sample(pool, min(8, len(pool)))

                keywords = []
                narr_text = sec.get("narration", "").lower()
                for kw in ["74.58%", "52.51%", "2.41", "326", "1482", "simon", "imposter", "unbeatable", "kills", "masterclass"]:
                    if kw in narr_text:
                        keywords.append(kw.upper())

                fast_cut_segment(
                    clip_pool, narr, seg_path,
                    cut_interval=3.5,
                    overlay_text=sec.get("visual_note", "")[:50],
                    keywords=keywords[:3],
                )

            if seg_path.exists() and seg_path.stat().st_size > 10000:
                segment_videos.append(seg_path)
                dur = _get_clip_duration(seg_path)
                log.info("  Seg %d (%s, %s clips): %.1fs", i, sec_type, clip_cat, dur)

        except Exception as e:
            log.warning("  Seg %d failed: %s — fallback", i, e)
            fast_cut_segment(random.sample(all_simon_clips, min(5, len(all_simon_clips))), narr, seg_path, cut_interval=4.0)
            if seg_path.exists():
                segment_videos.append(seg_path)

    # ── Step 4: Compile ──
    log.info("=== Step 4: Compiling ===")
    final_raw = OUTPUT / "Simon_Decoded_raw.mp4"
    concatenate(segment_videos, final_raw)

    final = OUTPUT / "Simon_Decoded_FINAL.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(final_raw),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(final),
    ], check=True, capture_output=True)

    dur = _get_clip_duration(final)
    log.info("DONE! %s — %.1f min (%d segments)", final.name, dur / 60, len(segment_videos))

    # Shorts
    from peanut_reacts.shorts.editor import crop_to_portrait, add_text_overlay
    shorts_dir = OUTPUT / "shorts"
    shorts_dir.mkdir(exist_ok=True)
    for idx, seg in enumerate(segment_videos[1:4]):
        p = shorts_dir / f"short_{idx+1}_p.mp4"
        crop_to_portrait(seg, p)
        f = shorts_dir / f"short_{idx+1}.mp4"
        add_text_overlay(p, f, hook_text="74.58% WIN RATE!", ending_text="Full analysis on channel!")
        p.unlink(missing_ok=True)

    print(f"\nOutput: {final}")
    print(f"Duration: {dur / 60:.1f} minutes")
    print(f"Segments: {len(segment_videos)}")


if __name__ == "__main__":
    main()
