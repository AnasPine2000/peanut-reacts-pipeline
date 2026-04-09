#!/usr/bin/env python3
"""Generate viral Deji Sidemen Among Us analysis video."""

import sys
import io
import json
import os
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.decoded.data_analyzer import (
    parse_all_transcripts, generate_player_report,
    analyze_player_mentions, find_key_moments, SIDEMEN,
)
from peanut_reacts.decoded.clip_extractor import (
    create_data_card, create_narration_segment, extract_clip,
    find_source_video, add_analysis_overlay,
)
from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider
from peanut_reacts.character.elevenlabs_tts import ElevenLabsConfig, ElevenLabsTTSEngine
from peanut_reacts.core.ffmpeg import concatenate
from peanut_reacts.core.logging_setup import build_logger

log = build_logger("deji_video", verbose=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")
ELEVENLABS_KEY = os.environ.get("ELEVENLABS_API_KEY", "sk_ee27d67f87c51cd5f641d0cf7c3efdb1c1806703108f2dc8")

VIDEO_DIRS = [Path("downloads/AMONGUS"), Path("downloads/KSIAMONGUS"), Path("downloads")]
OUTPUT = Path("production/deji_decoded")
OUTPUT.mkdir(parents=True, exist_ok=True)


def main():
    # ── Load data ──
    log.info("=== Loading data ===")
    transcripts = parse_all_transcripts(Path("downloads/AMONGUS"))
    transcripts += parse_all_transcripts(Path("downloads/KSIAMONGUS"))
    log.info("Transcripts: %d", len(transcripts))

    deji_report = json.loads(Path("data/transcripts/deji_report.json").read_text(encoding="utf-8"))
    fan_stats = json.loads(Path("data/transcripts/fan_stats.json").read_text(encoding="utf-8"))

    # Get Deji's fan stats
    deji_fan = next((p for p in fan_stats["players"] if p["name"] == "Deji"), {})

    # Get comparison stats for all Sidemen
    all_moments = find_key_moments(transcripts)
    deji_moments = [m for m in all_moments if "deji" in m["text"].lower() or "dj" in m["text"].lower()]

    # ── Generate script ──
    log.info("=== Generating viral script ===")
    llm = create_llm_provider(LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY))

    data_for_llm = json.dumps({
        "player": "Deji",
        "fan_stats": {
            "games_played": deji_fan.get("games_played", 257),
            "wins": deji_fan.get("wins", 105),
            "losses": deji_fan.get("losses", 151),
            "win_pct": deji_fan.get("win_pct", 40.86),
            "kdr": deji_fan.get("kdr", 1.32),
            "kills_as_imposter": deji_fan.get("kills_as_imposter", 104),
            "kills_per_imposter_game": deji_fan.get("kills_per_imposter_game", 2.17),
            "imposter_games": deji_fan.get("imposter_games", 48),
            "imposter_wins": deji_fan.get("imposter_wins", 17),
            "imposter_win_pct": deji_fan.get("imposter_win_pct", 35.42),
            "crewmate_win_pct": deji_fan.get("crewmate_win_pct", 44.2),
            "deaths": deji_fan.get("deaths", 114),
        },
        "comparison": {
            "Simon_imposter_win_pct": 74.58,
            "Vik_imposter_win_pct": 58.97,
            "Deji_imposter_win_pct": 35.42,
            "Simon_is_best": "74.58% vs Deji 35.42% — Simon wins DOUBLE",
            "Deji_has_worst_win_rate": "40.86% overall — lowest of all regular players",
            "but_kdr_is_high": "1.32 KDR — actually kills more than he dies",
            "KSI_kdr": 1.42,
            "Harry_kdr": 0.74,
        },
        "transcript_mentions": deji_report["total_mentions"],
        "videos_appeared": deji_report["videos_appeared_in"],
        "times_accused": deji_report["times_accused_by_others"],
        "roles_played": deji_report["roles_played"],
        "key_moments": [{"time": m["timestamp"], "video": m["video"][:50], "text": m["text"][:80]} for m in deji_moments[:12]],
        "speech_samples": [{"type": s["type"], "video": s["video"][:40], "text": s["text"][:80]} for s in deji_report["speech_samples"][:15]],
        "insights": fan_stats["insights"],
    }, indent=2)

    prompt = (
        "Write a VIRAL 15-minute YouTube video essay script analyzing Deji in Sidemen Among Us.\n\n"
        "Channel: 'Sidemen AmongUs Decoded'\n\n"
        "This video should be CONTROVERSIAL and ENGAGING — Deji has the WORST stats of any regular player "
        "but there is a hidden story the data reveals.\n\n"
        "REAL DATA:\n" + data_for_llm + "\n\n"
        "The video should cover:\n"
        "Part 1: THE SHOCKING STATS (Deji's terrible win rate revealed) — 3 sections\n"
        "Part 2: THE KSI-DEJI DYNAMIC (they constantly target each other) — 3 sections\n"
        "Part 3: THE HIDDEN TALENT (his KDR is actually high — he kills but still loses) — 3 sections\n"
        "Part 4: WHY HE KEEPS LOSING (the real reasons from transcript analysis) — 3 sections\n"
        "Part 5: THE VERDICT — is Deji the worst or the most entertaining? — 2 sections\n\n"
        "Generate JSON with 14-16 sections totaling 800-1000 seconds.\n"
        'JSON: {"title":"viral title under 70 chars","description":"...","tags":[...],'
        '"sections":[{"section_type":"intro|data_reveal|clip_moment|analysis|comparison|outro",'
        '"narration":"60-90 seconds per section","visual_note":"...","duration_estimate":60,'
        '"clip_query":"video title","clip_timestamp":0,"emotion":"neutral|shocked|excited"}]}\n\n'
        "Make it DRAMATIC. Use cliffhangers between sections. The title should make Sidemen fans CLICK."
    )

    response = llm.complete(
        [{"role": "system", "content": "You write viral YouTube video essays. Return ONLY valid JSON."},
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
    total_dur = sum(s.get("duration_estimate", 50) for s in sections)
    log.info('Script: "%s" — %d sections, ~%ds (%.1f min)', script["title"], len(sections), total_dur, total_dur / 60)

    (OUTPUT / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Narration ──
    log.info("=== Generating narration ===")
    narr_dir = OUTPUT / "narration"
    narr_dir.mkdir(exist_ok=True)

    tts = ElevenLabsTTSEngine(
        ElevenLabsConfig(api_key=ELEVENLABS_KEY, voice_id="TX3LPaxmHKxFdv7VOQHJ"),
        log,
    )

    narration_paths = {}
    for i, sec in enumerate(sections):
        narr_text = sec.get("narration", "").strip()
        if not narr_text:
            continue
        path = narr_dir / f"sec_{i:02d}.mp3"
        result = tts.synthesize(narr_text, path, emotion=sec.get("emotion", "neutral"))
        narration_paths[i] = result.audio_path
        log.info("  Sec %d (%s): %.1fs — %s", i, sec.get("section_type", "?"), result.duration, narr_text[:50])

    # ── Build segments ──
    log.info("=== Building video segments ===")
    seg_dir = OUTPUT / "segments"
    seg_dir.mkdir(exist_ok=True)

    segment_videos = []
    for i, sec in enumerate(sections):
        if i not in narration_paths:
            continue

        seg_path = seg_dir / f"seg_{i:02d}.mp4"
        narr = narration_paths[i]
        sec_type = sec.get("section_type", "analysis")

        try:
            if sec_type == "data_reveal":
                card = seg_dir / f"card_{i:02d}.mp4"
                stats = {
                    "Games Played": str(deji_fan.get("games_played", 257)),
                    "Win Rate": f"{deji_fan.get('win_pct', 40.86)}%",
                    "KDR": str(deji_fan.get("kdr", 1.32)),
                    "Imposter Win%": f"{deji_fan.get('imposter_win_pct', 35.42)}%",
                    "Deaths": str(deji_fan.get("deaths", 114)),
                }
                create_data_card(stats, "DEJI STATS", card)
                _overlay_narr(card, narr, seg_path)

            elif sec_type == "comparison":
                card = seg_dir / f"comp_{i:02d}.mp4"
                comp = {
                    "Simon": "74.58% imposter win",
                    "Vik": "58.97% imposter win",
                    "JJ/KSI": "46.84% imposter win",
                    "Harry": "47.17% imposter win",
                    "DEJI": "35.42% imposter win",
                }
                create_data_card(comp, "IMPOSTER WIN RATE", card)
                _overlay_narr(card, narr, seg_path)

            elif sec_type == "clip_moment" and sec.get("clip_query"):
                source = find_source_video(sec["clip_query"], VIDEO_DIRS)
                if source:
                    clip_raw = seg_dir / f"clip_{i:02d}_raw.mp4"
                    ts = sec.get("clip_timestamp", 0)
                    extract_clip(source, ts, ts + 12, clip_raw)
                    clip_ann = seg_dir / f"clip_{i:02d}_ann.mp4"
                    add_analysis_overlay(clip_ann if not clip_ann.exists() else clip_raw,
                                        clip_ann, top_text=sec.get("visual_note", "")[:60])
                    _overlay_narr(clip_ann if clip_ann.exists() else clip_raw, narr, seg_path)
                else:
                    create_narration_segment(narr, seg_path, visual_text=sec.get("visual_note", "")[:100])

            else:
                create_narration_segment(narr, seg_path, visual_text=sec.get("visual_note", "")[:100])

            if seg_path.exists():
                segment_videos.append(seg_path)
                log.info("  Seg %d (%s): OK", i, sec_type)

        except Exception as e:
            log.warning("  Seg %d failed: %s — using narration-only fallback", i, e)
            create_narration_segment(narr, seg_path, visual_text=sec.get("visual_note", "")[:100])
            if seg_path.exists():
                segment_videos.append(seg_path)

    # ── Compile ──
    log.info("=== Compiling final video ===")
    final = OUTPUT / "Deji_Among_Us_Decoded.mp4"
    concatenate(segment_videos, final)

    import subprocess
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)],
        capture_output=True, text=True,
    ).stdout.strip())

    log.info("DONE! %s — %.1f minutes (%d segments)", final.name, dur / 60, len(segment_videos))

    # ── Extract shorts ──
    from peanut_reacts.shorts.editor import crop_to_portrait, add_text_overlay
    shorts_dir = OUTPUT / "shorts"
    shorts_dir.mkdir(exist_ok=True)

    clip_segs = [s for s in segment_videos if "clip_" in s.name or "card_" in s.name][:3]
    if not clip_segs:
        clip_segs = segment_videos[1:4]

    for idx, seg in enumerate(clip_segs):
        portrait = shorts_dir / f"short_{idx+1}_p.mp4"
        crop_to_portrait(seg, portrait)
        short_final = shorts_dir / f"short_{idx+1}.mp4"
        add_text_overlay(portrait, short_final,
                        hook_text="Deji's stats are TERRIBLE!",
                        ending_text="Full analysis on our channel!")
        portrait.unlink(missing_ok=True)
        log.info("Short %d: %s", idx + 1, short_final)

    print(f"\nOutput: {final}")
    print(f"Duration: {dur / 60:.1f} minutes")
    print(f"Segments: {len(segment_videos)}")
    print(f"Shorts: {len(list(shorts_dir.glob('short_*.mp4')))}")


def _overlay_narr(video, narration, output):
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(narration)],
        capture_output=True, text=True,
    )
    dur = float(probe.stdout.strip()) + 1

    result = subprocess.run([
        "ffmpeg", "-y", "-i", str(video), "-i", str(narration),
        "-filter_complex", "[0:a]volume=0.15[bg];[1:a]volume=1.5[narr];[bg][narr]amix=inputs=2:duration=longest:dropout_transition=2[a]",
        "-map", "0:v", "-map", "[a]", "-t", str(dur),
        "-c:v", "copy", "-c:a", "aac", str(output),
    ], capture_output=True)

    if result.returncode != 0:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video), "-i", str(narration),
            "-map", "0:v", "-map", "1:a", "-t", str(dur),
            "-c:v", "copy", "-c:a", "aac", str(output),
        ], check=True, capture_output=True)


if __name__ == "__main__":
    main()
