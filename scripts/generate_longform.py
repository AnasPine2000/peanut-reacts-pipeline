#!/usr/bin/env python3
"""Generate a 15-20 minute KSI analysis long-form video."""

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
from peanut_reacts.decoded.script_writer import ScriptSection, AnalysisScript
from peanut_reacts.decoded.clip_extractor import (
    create_data_card, create_narration_segment, extract_clip,
    find_source_video, add_analysis_overlay,
)
from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider
from peanut_reacts.character.elevenlabs_tts import ElevenLabsConfig, ElevenLabsTTSEngine
from peanut_reacts.core.ffmpeg import concatenate
from peanut_reacts.core.logging_setup import build_logger

log = build_logger("longform", verbose=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")
ELEVENLABS_KEY = os.environ.get("ELEVENLABS_API_KEY", "sk_ee27d67f87c51cd5f641d0cf7c3efdb1c1806703108f2dc8")

VIDEO_DIRS = [Path("downloads/AMONGUS"), Path("downloads/KSIAMONGUS"), Path("downloads")]
OUTPUT_DIR = Path("production/decoded_longform")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    # ── Step 1: Load data ──
    log.info("=== Step 1: Loading transcripts ===")
    transcripts = parse_all_transcripts(Path("downloads/AMONGUS"))
    transcripts += parse_all_transcripts(Path("downloads/KSIAMONGUS"))
    log.info("Loaded %d videos", len(transcripts))

    # ── Step 2: Analyze ──
    log.info("=== Step 2: Analyzing KSI ===")
    ksi_report = generate_player_report("KSI", transcripts)
    all_profiles = analyze_player_mentions(transcripts)
    all_moments = find_key_moments(transcripts)
    ksi_moments = [m for m in all_moments if "ksi" in m["text"].lower() or "jj" in m["text"].lower()]

    comparison = {}
    for name in SIDEMEN:
        p = all_profiles.get(name)
        if p:
            comparison[name] = {
                "mentions": p.total_mentions,
                "accusations": p.accusation_count,
                "videos": p.videos_appeared_in,
            }
            log.info("  %s: %d mentions, %d accusations", name, p.total_mentions, p.accusation_count)

    # ── Step 3: Generate script via LLM ──
    log.info("=== Step 3: Generating long-form script ===")
    llm = create_llm_provider(LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY))

    data_ctx = json.dumps({
        "player": "KSI",
        "total_mentions": ksi_report["total_mentions"],
        "videos": ksi_report["videos_appeared_in"],
        "accusations": ksi_report["times_accused_by_others"],
        "roles": ksi_report["roles_played"],
        "top_words": ksi_report["most_common_words"][:15],
        "comparison": comparison,
        "key_moments": [{"time": m["timestamp"], "video": m["video"][:50], "text": m["text"][:80]} for m in ksi_moments[:15]],
        "speech_samples": [{"type": s["type"], "video": s["video"][:40], "text": s["text"][:80]} for s in ksi_report["speech_samples"][:20]],
    }, indent=2)

    prompt = (
        'Write a 15-20 minute YouTube video essay script analyzing KSI in Sidemen Among Us.\n\n'
        'REAL DATA:\n' + data_ctx + '\n\n'
        'Generate JSON with 18-22 sections totaling 900-1200 seconds.\n'
        'Cover: The KSI Paradox, The Deji Connection, Role Analysis, '
        'Compared to Other Sidemen, Speech Patterns, The Verdict.\n\n'
        'JSON: {"title":"...","description":"...","tags":[...],"sections":[{"section_type":"intro|data_reveal|clip_moment|analysis|comparison|outro",'
        '"narration":"45-90 seconds text","visual_note":"...","duration_estimate":60,"clip_query":"video title","clip_timestamp":0,"emotion":"neutral|shocked|excited"}]}\n\n'
        'Every section must reference REAL data. 45-90 seconds narration per section.'
    )

    response = llm.complete(
        [{"role": "system", "content": "You are a YouTube video essay writer. Return ONLY valid JSON."},
         {"role": "user", "content": prompt}],
        temperature=0.85, max_tokens=8000,
    )

    text = response.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    script_data = json.loads(text)
    sections = script_data.get("sections", [])
    total_dur = sum(s.get("duration_estimate", 50) for s in sections)
    log.info('Script: "%s" — %d sections, ~%ds (%.1f min)', script_data["title"], len(sections), total_dur, total_dur / 60)

    # Save script
    (OUTPUT_DIR / "script.json").write_text(json.dumps(script_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Step 4: Generate narration ──
    log.info("=== Step 4: Generating narration ===")
    narr_dir = OUTPUT_DIR / "narration"
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
        log.info("  Section %d (%s): %.1fs", i, sec.get("section_type", "?"), result.duration)

    # ── Step 5: Build video segments ──
    log.info("=== Step 5: Building segments ===")
    seg_dir = OUTPUT_DIR / "segments"
    seg_dir.mkdir(exist_ok=True)

    segment_videos = []
    for i, sec in enumerate(sections):
        if i not in narration_paths:
            continue

        seg_path = seg_dir / f"seg_{i:02d}.mp4"
        narr = narration_paths[i]
        sec_type = sec.get("section_type", "analysis")

        if sec_type == "data_reveal":
            card = seg_dir / f"card_{i:02d}.mp4"
            stats = {
                "Total Mentions": str(ksi_report["total_mentions"]),
                "Videos": str(ksi_report["videos_appeared_in"]),
                "Accused": str(ksi_report["times_accused_by_others"]),
            }
            roles = ksi_report.get("roles_played", {})
            if roles:
                top = max(roles, key=roles.get)
                stats["Top Role"] = f"{top} ({roles[top]}x)"
            create_data_card(stats, "KSI Stats", card)
            _overlay_narration(card, narr, seg_path)

        elif sec_type == "clip_moment" and sec.get("clip_query"):
            source = find_source_video(sec["clip_query"], VIDEO_DIRS)
            if source:
                clip_raw = seg_dir / f"clip_{i:02d}_raw.mp4"
                ts = sec.get("clip_timestamp", 0)
                extract_clip(source, ts, ts + 12, clip_raw)
                clip_ann = seg_dir / f"clip_{i:02d}_ann.mp4"
                add_analysis_overlay(clip_raw, clip_ann, top_text=sec.get("visual_note", "")[:60])
                _overlay_narration(clip_ann, narr, seg_path)
            else:
                create_narration_segment(narr, seg_path, visual_text=sec.get("visual_note", "")[:100])

        elif sec_type == "comparison":
            card = seg_dir / f"comp_{i:02d}.mp4"
            comp_stats = {}
            for name in ["KSI", "Miniminter", "W2S", "TBJZL"]:
                c = comparison.get(name, {})
                comp_stats[name] = f"{c.get('mentions', 0)} mentions / {c.get('accusations', 0)} accused"
            create_data_card(comp_stats, "Sidemen Comparison", card)
            _overlay_narration(card, narr, seg_path)

        else:
            create_narration_segment(narr, seg_path, visual_text=sec.get("visual_note", "")[:100])

        if seg_path.exists():
            segment_videos.append(seg_path)
            log.info("  Seg %d (%s): %s", i, sec_type, seg_path.name)

    # ── Step 6: Compile ──
    log.info("=== Step 6: Compiling final video ===")
    final = OUTPUT_DIR / "KSI_Decoded_Full_Analysis.mp4"
    concatenate(segment_videos, final)

    import subprocess
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)],
        capture_output=True, text=True,
    ).stdout.strip())

    log.info("DONE! %s — %.1f minutes", final.name, dur / 60)
    print(f"\nOutput: {final}")
    print(f"Duration: {dur / 60:.1f} minutes")
    print(f"Segments: {len(segment_videos)}")


def _overlay_narration(video, narration, output):
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
