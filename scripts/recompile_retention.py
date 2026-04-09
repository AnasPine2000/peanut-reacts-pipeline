#!/usr/bin/env python3
"""Recompile Deji video with retention-optimized editing."""

import sys, io, json, os, random
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.decoded.retention_editor import fast_cut_segment, enhanced_data_card, _get_clip_duration
from peanut_reacts.core.ffmpeg import concatenate
from peanut_reacts.core.logging_setup import build_logger
import subprocess

log = build_logger("retention", verbose=True)

OUTPUT = Path("production/deji_decoded_v3")
OUTPUT.mkdir(parents=True, exist_ok=True)
PREV = Path("production/deji_decoded")

# Load script
script = json.loads((PREV / "script.json").read_text(encoding="utf-8"))
sections = script["sections"]
fan_stats = json.loads(Path("data/transcripts/fan_stats.json").read_text(encoding="utf-8"))
deji_fan = next((p for p in fan_stats["players"] if p["name"] == "Deji"), {})

# Load ALL clips
deji_clips = sorted(Path("data/clips/deji").glob("deji_*.mp4"))
general_clips = sorted(Path("data/clips").glob("clip_*.mp4"))
all_clips = deji_clips + general_clips
log.info("Clips: %d Deji + %d general = %d", len(deji_clips), len(general_clips), len(all_clips))

seg_dir = OUTPUT / "segments"
seg_dir.mkdir(exist_ok=True)

segment_videos = []

for i, sec in enumerate(sections):
    narr = PREV / "narration" / f"sec_{i:02d}.mp3"
    if not narr.exists():
        continue

    seg_path = seg_dir / f"seg_{i:02d}.mp4"
    sec_type = sec.get("section_type", "analysis")
    narration_text = sec.get("narration", "")

    # Extract keywords from narration for popup effect
    keywords = []
    for word in ["40.86%", "35.42%", "1.32", "KDR", "257 games", "74.58%", "Deji", "KSI", "imposter", "worst", "secretly", "kills"]:
        if word.lower() in narration_text.lower():
            keywords.append(word)

    try:
        if sec_type in ("data_reveal", "comparison"):
            # Data card with gameplay background + narration
            if sec_type == "data_reveal":
                stats = {
                    "Games Played": str(deji_fan.get("games_played", 257)),
                    "Win Rate": f"{deji_fan.get('win_pct', 40.86)}%",
                    "KDR": str(deji_fan.get("kdr", 1.32)),
                    "Imposter Win": f"{deji_fan.get('imposter_win_pct', 35.42)}%",
                }
                card_title = "DEJI DECODED"
            else:
                stats = {"Simon": "74.58%", "Vik": "58.97%", "KSI": "46.84%", "Harry": "47.17%", "DEJI": "35.42%"}
                card_title = "IMPOSTER WIN RATE"

            bg_clip = random.choice(deji_clips) if deji_clips else None
            card = seg_dir / f"card_{i:02d}.mp4"
            enhanced_data_card(stats, card_title, card, background_clip=bg_clip, duration=8.0)

            narr_dur = _get_clip_duration(narr)
            remaining = max(0, narr_dur - 7.0)

            if remaining > 2 and all_clips:
                # After data card: fast cut clips for remaining narration
                remaining_clips_path = seg_dir / f"remaining_{i:02d}.mp4"
                fast_cut_segment(
                    random.sample(all_clips, min(5, len(all_clips))),
                    narr,  # dummy - we'll mix audio later
                    remaining_clips_path,
                    cut_interval=3.5,
                )
                # Trim remaining to match
                remaining_trimmed = seg_dir / f"rem_trim_{i:02d}.mp4"
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(remaining_clips_path),
                    "-t", str(remaining), "-c", "copy", str(remaining_trimmed),
                ], capture_output=True)

                # Concat card + remaining
                combined = seg_dir / f"combined_{i:02d}.mp4"
                concatenate([card, remaining_trimmed], combined)

                # Add narration
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(combined), "-i", str(narr),
                    "-map", "0:v", "-map", "1:a", "-t", str(narr_dur + 1),
                    "-c:v", "copy", "-c:a", "aac", str(seg_path),
                ], check=True, capture_output=True)

                remaining_clips_path.unlink(missing_ok=True)
                remaining_trimmed.unlink(missing_ok=True)
                combined.unlink(missing_ok=True)
            else:
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(card), "-i", str(narr),
                    "-map", "0:v", "-map", "1:a", "-shortest",
                    "-c:v", "copy", "-c:a", "aac", str(seg_path),
                ], check=True, capture_output=True)

            card.unlink(missing_ok=True)

        else:
            # All other sections: FAST CUTS with Ken Burns + keyword popups
            # Pick 5-8 random clips for variety
            clip_pool = random.sample(all_clips, min(8, len(all_clips)))
            # Prefer Deji clips when narration mentions him
            if "deji" in narration_text.lower() and deji_clips:
                clip_pool = random.sample(deji_clips, min(5, len(deji_clips))) + random.sample(general_clips, min(3, len(general_clips)))

            fast_cut_segment(
                clip_pool,
                narr,
                seg_path,
                cut_interval=3.5,
                overlay_text=sec.get("visual_note", "")[:50],
                keywords=keywords[:3],
            )

        if seg_path.exists() and seg_path.stat().st_size > 10000:
            segment_videos.append(seg_path)
            dur = _get_clip_duration(seg_path)
            log.info("Seg %d (%s): %.1fs — %d keywords", i, sec_type, dur, len(keywords))

    except Exception as e:
        log.warning("Seg %d failed: %s — fallback", i, e)
        import traceback
        traceback.print_exc()
        # Fallback
        fast_cut_segment(
            random.sample(all_clips, min(5, len(all_clips))),
            narr, seg_path, cut_interval=4.0,
        )
        if seg_path.exists():
            segment_videos.append(seg_path)

# Compile
log.info("Compiling %d segments...", len(segment_videos))
final_raw = OUTPUT / "Deji_Decoded_v3_raw.mp4"
concatenate(segment_videos, final_raw)

# Normalize audio
final = OUTPUT / "Deji_Decoded_v3_FINAL.mp4"
subprocess.run([
    "ffmpeg", "-y", "-i", str(final_raw),
    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    str(final),
], check=True, capture_output=True)

dur = _get_clip_duration(final)
log.info("DONE! %s — %.1f min", final.name, dur / 60)
print(f"\nOutput: {final}")
print(f"Duration: {dur / 60:.1f} minutes")
print(f"Segments: {len(segment_videos)}")
