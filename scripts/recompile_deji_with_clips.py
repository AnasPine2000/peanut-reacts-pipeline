#!/usr/bin/env python3
"""Re-compile Deji analysis with real fast-cut clips."""

import sys, io, json, os, subprocess, random
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.decoded.clip_extractor import create_data_card, add_analysis_overlay
from peanut_reacts.core.ffmpeg import concatenate
from peanut_reacts.core.logging_setup import build_logger

log = build_logger("recompile", verbose=True)

OUTPUT = Path("production/deji_decoded_v2")
OUTPUT.mkdir(parents=True, exist_ok=True)

# Load script and narration from previous run
PREV = Path("production/deji_decoded")
script = json.loads((PREV / "script.json").read_text(encoding="utf-8"))
sections = script["sections"]
fan_stats = json.loads(Path("data/transcripts/fan_stats.json").read_text(encoding="utf-8"))
deji_fan = next((p for p in fan_stats["players"] if p["name"] == "Deji"), {})

# Load clip library
general_clips = sorted(Path("data/clips").glob("clip_*.mp4"))
deji_clips = sorted(Path("data/clips/deji").glob("deji_*.mp4"))
all_clips = deji_clips + general_clips
log.info("Clip library: %d Deji + %d general = %d total", len(deji_clips), len(general_clips), len(all_clips))

seg_dir = OUTPUT / "segments"
seg_dir.mkdir(exist_ok=True)

clip_idx = 0  # cycle through clips


def get_next_clip():
    """Get next clip from the library, cycling through."""
    global clip_idx
    clip = all_clips[clip_idx % len(all_clips)]
    clip_idx += 1
    return clip


def build_clip_segment(narration_path, output_path, overlay_text="", use_deji_clip=True):
    """Build a segment: real gameplay clip as background + narration overlay + text."""
    # Pick a clip (prefer Deji clips)
    if use_deji_clip and deji_clips:
        clip = random.choice(deji_clips)
    else:
        clip = get_next_clip()

    # Get narration duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(narration_path)],
        capture_output=True, text=True,
    )
    narr_dur = float(probe.stdout.strip()) + 1.0

    # Build video: loop clip to match narration length, add text overlay, mix audio
    safe_text = overlay_text.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")[:60] if overlay_text else ""

    vf = "null"
    if safe_text:
        vf = (
            f"drawtext=text='{safe_text}':"
            f"fontsize=32:fontcolor=white:borderw=2:bordercolor=black:"
            f"box=1:boxcolor=black@0.6:boxborderw=8:"
            f"x=(w-tw)/2:y=h-80"
        )

    # Mix: clip audio (low) + narration (high)
    af = (
        "[0:a]volume=0.12[bg];"
        "[1:a]volume=1.8[narr];"
        "[bg][narr]amix=inputs=2:duration=longest:dropout_transition=2[a]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(clip),
        "-i", str(narration_path),
        "-t", str(narr_dur),
        "-vf", vf,
        "-filter_complex", af,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        # Fallback: no audio mixing (clip may not have audio)
        cmd_fb = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(clip),
            "-i", str(narration_path),
            "-t", str(narr_dur),
            "-vf", vf,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(output_path),
        ]
        subprocess.run(cmd_fb, check=True, capture_output=True)

    return output_path


def build_data_segment(narration_path, output_path, stats, title):
    """Build a data card segment with narration."""
    card = output_path.with_name(output_path.stem + "_card.mp4")
    create_data_card(stats, title, card, duration=8.0)

    # Overlay narration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(narration_path)],
        capture_output=True, text=True,
    )
    narr_dur = float(probe.stdout.strip()) + 1

    # After data card, cut to gameplay clip for the rest of the narration
    remaining = max(0, narr_dur - 8.0)
    if remaining > 2 and all_clips:
        clip = random.choice(deji_clips) if deji_clips else get_next_clip()
        clip_seg = output_path.with_name(output_path.stem + "_clippart.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-ss", "0", "-i", str(clip), "-t", str(remaining),
            "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(clip_seg),
        ], check=True, capture_output=True)

        # Concat card + clip
        combined = output_path.with_name(output_path.stem + "_combined.mp4")
        concatenate([card, clip_seg], combined)

        # Add narration
        subprocess.run([
            "ffmpeg", "-y", "-i", str(combined), "-i", str(narration_path),
            "-filter_complex", "[0:a]volume=0.12[bg];[1:a]volume=1.8[narr];[bg][narr]amix=inputs=2:duration=longest:dropout_transition=2[a]",
            "-map", "0:v", "-map", "[a]", "-t", str(narr_dur),
            "-c:v", "copy", "-c:a", "aac", str(output_path),
        ], capture_output=True)

        if not output_path.exists():
            subprocess.run([
                "ffmpeg", "-y", "-i", str(combined), "-i", str(narration_path),
                "-map", "0:v", "-map", "1:a", "-t", str(narr_dur),
                "-c:v", "copy", "-c:a", "aac", str(output_path),
            ], check=True, capture_output=True)

        clip_seg.unlink(missing_ok=True)
        combined.unlink(missing_ok=True)
    else:
        # Short segment — just data card with narration
        subprocess.run([
            "ffmpeg", "-y", "-i", str(card), "-i", str(narration_path),
            "-map", "0:v", "-map", "1:a", "-t", str(narr_dur), "-shortest",
            "-c:v", "copy", "-c:a", "aac", str(output_path),
        ], check=True, capture_output=True)

    card.unlink(missing_ok=True)
    return output_path


# ── BUILD ALL SEGMENTS ──
segment_videos = []
for i, sec in enumerate(sections):
    narr = PREV / "narration" / f"sec_{i:02d}.mp3"
    if not narr.exists():
        continue

    seg_path = seg_dir / f"seg_{i:02d}.mp4"
    sec_type = sec.get("section_type", "analysis")

    try:
        if sec_type == "data_reveal":
            stats = {
                "Games": str(deji_fan.get("games_played", 257)),
                "Win Rate": f"{deji_fan.get('win_pct', 40.86)}%",
                "KDR": str(deji_fan.get("kdr", 1.32)),
                "Imposter Win": f"{deji_fan.get('imposter_win_pct', 35.42)}%",
            }
            build_data_segment(narr, seg_path, stats, "DEJI STATS")

        elif sec_type == "comparison":
            comp = {"Simon": "74.58%", "Vik": "58.97%", "KSI": "46.84%", "Harry": "47.17%", "DEJI": "35.42%"}
            build_data_segment(narr, seg_path, comp, "IMPOSTER WIN RATE")

        else:
            # Use real clips as background for all other sections
            overlay = sec.get("visual_note", "")[:60]
            build_clip_segment(narr, seg_path, overlay_text=overlay, use_deji_clip=("deji" in sec.get("narration", "").lower()))

        if seg_path.exists() and seg_path.stat().st_size > 10000:
            segment_videos.append(seg_path)
            log.info("Seg %d (%s): OK", i, sec_type)
        else:
            log.warning("Seg %d: empty or missing", i)

    except Exception as e:
        log.warning("Seg %d failed: %s", i, e)
        # Fallback: clip with narration, no text
        try:
            build_clip_segment(narr, seg_path, use_deji_clip=True)
            if seg_path.exists():
                segment_videos.append(seg_path)
        except:
            pass

# ── COMPILE ──
log.info("Compiling %d segments...", len(segment_videos))
final = OUTPUT / "Deji_Decoded_CLIPS.mp4"
concatenate(segment_videos, final)

dur = float(subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)],
    capture_output=True, text=True,
).stdout.strip())

# Normalize audio
final_loud = OUTPUT / "Deji_Decoded_FINAL.mp4"
subprocess.run([
    "ffmpeg", "-y", "-i", str(final),
    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    str(final_loud),
], check=True, capture_output=True)

log.info("DONE! %s — %.1f min (%d segments)", final_loud.name, dur / 60, len(segment_videos))

# Extract shorts
from peanut_reacts.shorts.editor import crop_to_portrait, add_text_overlay
shorts_dir = OUTPUT / "shorts"
shorts_dir.mkdir(exist_ok=True)

for idx in range(min(3, len(segment_videos))):
    seg = segment_videos[idx + 1]  # skip intro
    p = shorts_dir / f"short_{idx+1}_p.mp4"
    crop_to_portrait(seg, p)
    f = shorts_dir / f"short_{idx+1}.mp4"
    add_text_overlay(p, f, hook_text="Deji's REAL stats exposed!", ending_text="Full analysis on channel!")
    p.unlink(missing_ok=True)

print(f"\nOutput: {final_loud}")
print(f"Duration: {dur / 60:.1f} minutes")
print(f"Segments: {len(segment_videos)}")
