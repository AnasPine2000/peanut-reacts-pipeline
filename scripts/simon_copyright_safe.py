#!/usr/bin/env python3
"""Generate copyright-safe Simon analysis: 80% data visuals + 20% brief clips."""

import sys, io, json, os, random, asyncio, subprocess
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peanut_reacts.decoded.data_visuals import (
    render_stat_card, render_comparison_bars,
    render_quote_callout, render_rivalry_graphic,
)
from peanut_reacts.decoded.retention_editor import fast_cut_segment, _get_clip_duration
from peanut_reacts.core.ffmpeg import concatenate
from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.character.reaction_generator import LLMConfig, create_llm_provider
import edge_tts

log = build_logger("simon_safe", verbose=True)

OUTPUT = Path("production/simon_safe")
OUTPUT.mkdir(parents=True, exist_ok=True)

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a1ea2128b9ab4fa58c0bd752a2ca4869")

# Load data
analysis = json.loads(Path("data/transcripts/simon_full_analysis.json").read_text(encoding="utf-8"))
fan = analysis["fan_stats"]
deep = analysis["deep_analysis"]
basic = analysis["basic_report"]
fan_stats_all = json.loads(Path("data/transcripts/fan_stats.json").read_text(encoding="utf-8"))

# Brief clips (only for illustrative fair use — max 3-5 seconds each)
simon_clips = sorted(Path("data/clips/simon").glob("simon_*.mp4"))
general_clips = sorted(Path("data/clips").glob("clip_*.mp4"))
all_clips = simon_clips + general_clips


def main():
    # ── Step 1: Generate script optimized for data visuals ──
    log.info("=== Step 1: Script ===")
    llm = create_llm_provider(LLMConfig(provider="deepseek", api_key=DEEPSEEK_KEY))

    prompt = (
        "Write a 12-15 minute YouTube video essay: 'Why Simon is UNBEATABLE in Sidemen Among Us (Data Analysis)'\n\n"
        "Channel: 'Sidemen AmongUs Decoded'\n\n"
        "IMPORTANT: This video is 80% DATA VISUALIZATIONS and 20% brief clip references.\n"
        "Each section specifies what TYPE of visual to show.\n\n"
        f"REAL DATA:\n"
        f"Simon stats: games={fan.get('games_played')}, win_rate={fan.get('win_pct')}%, "
        f"imposter_win={fan.get('imposter_win_pct')}%, kills_per_game={fan.get('kills_per_imposter_game')}, "
        f"crewmate_win={fan.get('crewmate_win_pct')}%, kdr={fan.get('kdr')}\n"
        f"Mentions: {basic['total_mentions']}, Videos: {basic['videos_appeared_in']}, Accused: {basic['times_accused_by_others']}\n"
        f"Roles: {json.dumps(basic['roles_played'])}\n"
        f"Emotional: {json.dumps(deep['emotional_profile'])}\n"
        f"Rivalries: Simon-Josh=326, Simon-KSI=227, Simon-Harry=180\n"
        f"All players imposter win: Simon=74.58, Vik=58.97, Lazarbeam=56.25, Harry=47.17, KSI=46.84, Deji=35.42\n"
        f"All players win rate: Simon=52.51, Vik=50.13, Lazarbeam=47.48, Tobi=44.89, Harry=43.86, KSI=42.58, Deji=40.86\n"
        f"Speech samples: {json.dumps(basic['speech_samples'][:10])}\n\n"
        "Generate JSON with 12 sections (EXACTLY 12, not more). EACH section must specify visual_type:\n"
        '- "stat_card": animated player stats (for data reveals)\n'
        '- "comparison_bars": horizontal bar chart comparing players\n'
        '- "quote_callout": dramatic transcript quote on dark bg\n'
        '- "rivalry_graphic": head-to-head VS comparison\n'
        '- "brief_clip": SHORT 3-5 second clip for illustration only (MAX 4 of these in entire video)\n'
        '- "narration_visual": dark bg with key text points\n\n'
        'JSON: {"title":"under 70 chars","description":"...","tags":[...],'
        '"sections":[{"section_type":"intro|data_reveal|analysis|comparison|clip_moment|outro",'
        '"visual_type":"stat_card|comparison_bars|quote_callout|rivalry_graphic|brief_clip|narration_visual",'
        '"narration":"60-90 seconds","visual_note":"what to show",'
        '"visual_data":{},"duration_estimate":60,"emotion":"neutral|shocked|excited"}]}\n\n'
        "Rules:\n"
        "- MAX 4 brief_clip sections in the ENTIRE video (copyright safety)\n"
        "- At least 12 sections must use data visuals (stat_card, comparison_bars, quote_callout, rivalry_graphic)\n"
        "- Every stat mentioned must come from the REAL DATA above\n"
        "- Build tension: start with shocking stat, reveal more data, climax with verdict"
    )

    response = llm.complete(
        [{"role": "system", "content": "You write data-driven YouTube video essays. Return ONLY valid JSON."},
         {"role": "user", "content": prompt}],
        temperature=0.85, max_tokens=8000,
    )

    text = response.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        script = json.loads(text)
    except json.JSONDecodeError:
        # Truncated JSON — try to fix by closing arrays/objects
        text = text.rstrip()
        if not text.endswith("}"):
            # Find last complete section
            last_brace = text.rfind("}")
            if last_brace > 0:
                text = text[:last_brace + 1] + "]}"
        try:
            script = json.loads(text)
        except:
            # Manual fallback script
            log.warning("LLM JSON broken — using fallback script")
            script = {
                "title": "Why Simon is UNBEATABLE in Sidemen Among Us (74.58% Win Rate)",
                "description": "Data analysis of Simon across 358 games. #Sidemen #AmongUs #Decoded",
                "tags": ["Sidemen", "Among Us", "Simon", "Miniminter", "analysis"],
                "sections": [
                    {"section_type": "intro", "visual_type": "comparison_bars", "narration": f"What if I told you one player wins 74.58 percent of their imposter games in Sidemen Among Us? That's nearly 3 out of every 4 games. His name is Simon, also known as Miniminter, and the data proves he's the most dominant player in the history of Sidemen Among Us.", "duration_estimate": 50, "emotion": "neutral"},
                    {"section_type": "data_reveal", "visual_type": "stat_card", "narration": f"Let's look at the numbers. Simon has played {fan.get('games_played', 358)} games with a {fan.get('win_pct', 52.51)} percent overall win rate. That's the highest of ANY Sidemen member. As imposter, he wins {fan.get('imposter_win_pct', 74.58)} percent of the time with {fan.get('kills_per_imposter_game', 2.41)} kills per game, making him the deadliest imposter. Even as crewmate, he wins {fan.get('crewmate_win_pct', 50.92)} percent. He's the best at EVERYTHING.", "duration_estimate": 60, "emotion": "shocked"},
                    {"section_type": "comparison", "visual_type": "comparison_bars", "narration": "To understand how absurd this is, look at the comparison. Simon at 74.58 percent. Vik at 58.97. Lazarbeam at 56.25. KSI at 46.84. And Deji all the way down at 35.42 percent. Simon wins DOUBLE what Deji wins as imposter. The gap between Simon and second place is larger than the gap between second and last.", "duration_estimate": 55, "emotion": "shocked"},
                    {"section_type": "analysis", "visual_type": "narration_visual", "narration": f"So how does he do it? Our transcript analysis of {basic['total_mentions']} mentions across {basic['videos_appeared_in']} videos reveals three key strategies. First, Simon is a master of timing. He doesn't rush kills. He waits for the perfect moment when witnesses are elsewhere. Second, his defensive language is extremely measured. While other players panic and over-explain, Simon stays calm. Third, he uses misdirection, subtly pointing suspicion at others without being obvious about it.", "visual_note": "Simon's 3 Key Strategies", "duration_estimate": 65, "emotion": "neutral"},
                    {"section_type": "clip_moment", "visual_type": "brief_clip", "narration": "Watch this moment. Simon has just killed someone and immediately acts casual. This is textbook Simon — the kill happens and he doesn't flinch. No panic, no rushing to create an alibi. Just calm, measured movement.", "duration_estimate": 40, "emotion": "excited"},
                    {"section_type": "data_reveal", "visual_type": "comparison_bars", "narration": f"Now let's talk about kills per imposter game. Simon averages {fan.get('kills_per_imposter_game', 2.41)} kills every time he's imposter. That means on average he eliminates more than 2 players before getting caught. Lazarbeam is second at 2.19. The difference might seem small, but over {fan.get('imposter_games', 59)} imposter games, that's dozens of extra kills.", "duration_estimate": 50, "emotion": "shocked"},
                    {"section_type": "comparison", "visual_type": "rivalry_graphic", "narration": "The Simon-Josh rivalry is the most intense in Sidemen Among Us history. They appear together in 326 transcript segments, more than any other pair. Josh seems to be Simon's biggest threat, frequently calling him out in meetings. Yet despite this constant suspicion, Simon STILL maintains that 74 percent win rate. Josh can read Simon better than anyone, and it's STILL not enough.", "duration_estimate": 55, "emotion": "excited"},
                    {"section_type": "analysis", "visual_type": "quote_callout", "narration": f"Simon's emotional profile is fascinating. He's involved in {deep['emotional_profile'].get('accusation', 0)} accusation moments and {deep['emotional_profile'].get('defense', 0)} defensive moments. But here's the key insight: his defense-to-accusation ratio is remarkably low. While other players get flustered and defensive, Simon barely defends at all. When accused, he simply redirects. This lack of emotional response makes him incredibly hard to read.", "duration_estimate": 55, "emotion": "neutral"},
                    {"section_type": "data_reveal", "visual_type": "stat_card", "narration": f"And it's not just imposter where Simon dominates. As crewmate, he wins {fan.get('crewmate_win_pct', 50.92)} percent of the time. That's the BEST crewmate win rate too. He's played Sheriff {basic['roles_played'].get('sheriff', 0)} times, Jester {basic['roles_played'].get('jester', 0)} times, and Engineer {basic['roles_played'].get('engineer', 0)} times. No matter what role he gets, he finds a way to win.", "duration_estimate": 50, "emotion": "shocked"},
                    {"section_type": "comparison", "visual_type": "rivalry_graphic", "narration": "The Simon-KSI dynamic is equally fascinating but for different reasons. They appear together in 227 segments. KSI has a much higher kill-death ratio at 1.42 compared to Simon's 0.90. KSI kills more but LOSES more. Simon kills less per game overall but wins far more. This reveals the fundamental difference: KSI plays for kills, Simon plays for victories.", "duration_estimate": 55, "emotion": "excited"},
                    {"section_type": "clip_moment", "visual_type": "brief_clip", "narration": "This moment perfectly captures why Simon is unbeatable. Look at how he navigates this meeting. Everyone is pointing fingers, chaos everywhere, and Simon sits back, waits, then delivers one line that shifts the entire vote. This is not luck. This is 358 games of experience.", "duration_estimate": 40, "emotion": "excited"},
                    {"section_type": "outro", "visual_type": "comparison_bars", "narration": f"So is Simon the greatest Sidemen Among Us player ever? The data says yes. 52.51 percent overall win rate. 74.58 percent as imposter. 50.92 percent as crewmate. 2.41 kills per imposter game. Across {fan.get('games_played', 358)} games, he's proven that consistency beats chaos. While others play for the highlight reel, Simon plays to win. And the numbers don't lie. If you enjoyed this data-driven analysis, subscribe for more Sidemen Among Us Decoded. We break down every player, every strategy, every stat.", "duration_estimate": 60, "emotion": "excited"},
                ],
            }

    sections = script.get("sections", [])
    total = sum(s.get("duration_estimate", 50) for s in sections)
    log.info('Script: "%s" — %d sections, ~%ds (%.1f min)', script["title"], len(sections), total, total / 60)

    # Count visual types
    from collections import Counter
    vtypes = Counter(s.get("visual_type", "?") for s in sections)
    log.info("Visual breakdown: %s", dict(vtypes))
    clip_count = vtypes.get("brief_clip", 0)
    log.info("Brief clips: %d (max 4 for copyright safety)", clip_count)

    (OUTPUT / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Step 2: Narrate ──
    log.info("=== Step 2: Narrate ===")
    narr_dir = OUTPUT / "narration"
    narr_dir.mkdir(exist_ok=True)

    import time

    for i, sec in enumerate(sections):
        t_text = sec.get("narration", "").strip()
        if not t_text:
            continue
        path = narr_dir / f"sec_{i:02d}.mp3"
        if path.exists() and path.stat().st_size > 1000:
            log.info("  Sec %d: cached", i)
            continue

        for attempt in range(3):
            try:
                c = edge_tts.Communicate(t_text, "en-US-GuyNeural", rate="-5%", pitch="-10Hz")
                asyncio.run(c.save(str(path)))
                log.info("  Sec %d: %.1fs", i, _get_clip_duration(path))
                break
            except Exception as e:
                log.warning("  Sec %d TTS failed (attempt %d): %s", i, attempt + 1, e)
                time.sleep(3)

        time.sleep(1)  # rate limit courtesy

    # ── Step 3: Build segments ──
    log.info("=== Step 3: Build segments ===")
    seg_dir = OUTPUT / "segments"
    seg_dir.mkdir(exist_ok=True)
    vis_dir = OUTPUT / "visuals"

    segment_videos = []
    brief_clip_count = 0

    for i, sec in enumerate(sections):
        narr = narr_dir / f"sec_{i:02d}.mp3"
        if not narr.exists():
            continue

        seg_path = seg_dir / f"seg_{i:02d}.mp4"
        vtype = sec.get("visual_type", "narration_visual")
        narr_dur = _get_clip_duration(narr)

        try:
            if vtype == "stat_card":
                vis = vis_dir / f"stat_{i:02d}"
                stats = {
                    "Win Rate": f"{fan.get('win_pct', 52.51)}%",
                    "Imposter Win": f"{fan.get('imposter_win_pct', 74.58)}%",
                    "Kills/Game": str(fan.get("kills_per_imposter_game", 2.41)),
                    "Crewmate Win": f"{fan.get('crewmate_win_pct', 50.92)}%",
                    "Games": str(fan.get("games_played", 358)),
                }
                card_vid = render_stat_card("Simon", stats, "Player Analysis", vis, duration=narr_dur + 1)
                _add_narration(card_vid, narr, seg_path, narr_dur)

            elif vtype == "comparison_bars":
                vis = vis_dir / f"bars_{i:02d}"
                # Determine which comparison from context
                narr_text = sec.get("narration", "").lower()
                if "imposter" in narr_text or "74" in narr_text:
                    data = {"Simon": 74.58, "Vik": 58.97, "Lazarbeam": 56.25, "Harry": 47.17, "KSI": 46.84, "Deji": 35.42}
                    title = "Imposter Win Rate"
                elif "crewmate" in narr_text:
                    data = {"Simon": 50.92, "Vik": 48.6, "Tobi": 47.64, "Lazarbeam": 46.07, "Harry": 45.32}
                    title = "Crewmate Win Rate"
                elif "kill" in narr_text:
                    data = {"Simon": 2.41, "Lazarbeam": 2.19, "Tobi": 2.17, "Vik": 2.14, "Ethan": 2.14, "Harry": 1.98}
                    title = "Kills Per Imposter Game"
                else:
                    data = {"Simon": 52.51, "Vik": 50.13, "Lazarbeam": 47.48, "Tobi": 44.89, "Harry": 43.86, "KSI": 42.58}
                    title = "Overall Win Rate"

                bar_vid = render_comparison_bars(data, title, vis, duration=narr_dur + 1, highlight_player="Simon")
                _add_narration(bar_vid, narr, seg_path, narr_dur)

            elif vtype == "quote_callout":
                vis = vis_dir / f"quote_{i:02d}"
                # Extract a quote from speech samples
                samples = basic.get("speech_samples", [])
                if samples:
                    sample = random.choice(samples[:10])
                    quote_text = sample.get("text", "I think it's Simon")[:100]
                    quote_vid = sample.get("video", "Sidemen Among Us")[:50]
                else:
                    quote_text = "I think it's Simon. He's been too quiet."
                    quote_vid = "Sidemen Among Us"

                q_vid = render_quote_callout(quote_text, "Sidemen", quote_vid, vis, duration=narr_dur + 1)
                _add_narration(q_vid, narr, seg_path, narr_dur)

            elif vtype == "rivalry_graphic":
                vis = vis_dir / f"rivalry_{i:02d}"
                narr_text = sec.get("narration", "").lower()
                if "josh" in narr_text:
                    p2, co = "Josh", 326
                    s2 = {"Win Rate": "42.21%", "Imposter Win": "43.33%", "KDR": "0.90"}
                elif "ksi" in narr_text or "jj" in narr_text:
                    p2, co = "KSI", 227
                    s2 = {"Win Rate": "42.58%", "Imposter Win": "46.84%", "KDR": "1.42"}
                elif "harry" in narr_text:
                    p2, co = "Harry", 180
                    s2 = {"Win Rate": "43.86%", "Imposter Win": "47.17%", "KDR": "0.74"}
                else:
                    p2, co = "Ethan", 153
                    s2 = {"Win Rate": "42.36%", "Imposter Win": "41.67%", "KDR": "0.97"}

                s1 = {"Win Rate": "52.51%", "Imposter Win": "74.58%", "KDR": "0.90"}
                r_vid = render_rivalry_graphic("Simon", p2, co, s1, s2, vis, duration=narr_dur + 1)
                _add_narration(r_vid, narr, seg_path, narr_dur)

            elif vtype == "brief_clip" and brief_clip_count < 4 and all_clips:
                # ONLY 3-5 seconds of actual footage + rest is visual
                brief_clip_count += 1
                clip = random.choice(simon_clips if simon_clips else all_clips)

                # 4 seconds of clip
                brief = seg_dir / f"brief_{i:02d}.mp4"
                subprocess.run([
                    "ffmpeg", "-y", "-ss", str(random.uniform(0, 3)),
                    "-i", str(clip), "-t", "4",
                    "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black",
                    "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
                    "-an", str(brief),
                ], capture_output=True)

                # Rest as narration visual
                remaining = max(1, narr_dur - 4)
                rest = seg_dir / f"rest_{i:02d}.mp4"
                _narration_dark_bg(narr, rest, sec.get("visual_note", "")[:60], remaining + 1)

                # Concat brief clip + rest
                combined = seg_dir / f"comb_{i:02d}.mp4"
                concatenate([brief, rest], combined)
                _add_narration(combined, narr, seg_path, narr_dur)

                for f in [brief, rest, combined]:
                    try: f.unlink(missing_ok=True)
                    except: pass

            else:
                # narration_visual: dark background with key text
                _narration_dark_bg(narr, seg_path, sec.get("visual_note", "")[:80], narr_dur + 1)

            if seg_path.exists() and seg_path.stat().st_size > 10000:
                segment_videos.append(seg_path)
                dur = _get_clip_duration(seg_path)
                log.info("  Seg %d (%s/%s): %.1fs", i, sec.get("section_type", "?"), vtype, dur)

        except Exception as e:
            log.warning("  Seg %d failed: %s — dark bg fallback", i, e)
            _narration_dark_bg(narr, seg_path, sec.get("visual_note", "")[:60], narr_dur + 1)
            if seg_path.exists():
                segment_videos.append(seg_path)

    log.info("Brief clips used: %d/4 max", brief_clip_count)

    # ── Step 4: Compile ──
    log.info("=== Step 4: Compile ===")
    final_raw = OUTPUT / "Simon_Safe_raw.mp4"
    concatenate(segment_videos, final_raw)

    final = OUTPUT / "Simon_Safe_FINAL.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(final_raw),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(final),
    ], check=True, capture_output=True)

    dur = _get_clip_duration(final)
    log.info("DONE! %s — %.1f min (%d segments, %d brief clips)", final.name, dur / 60, len(segment_videos), brief_clip_count)

    print(f"\nOutput: {final}")
    print(f"Duration: {dur / 60:.1f} minutes")
    print(f"Segments: {len(segment_videos)}")
    print(f"Brief clips: {brief_clip_count}/4 (copyright safe)")


def _add_narration(video: Path, narration: Path, output: Path, dur: float):
    """Add narration audio to a visual-only video."""
    result = subprocess.run([
        "ffmpeg", "-y", "-i", str(video), "-i", str(narration),
        "-map", "0:v", "-map", "1:a", "-t", str(dur + 1), "-shortest",
        "-c:v", "copy", "-c:a", "aac", str(output),
    ], capture_output=True)
    if result.returncode != 0:
        # Video may have audio already
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video), "-i", str(narration),
            "-filter_complex", "[0:a]volume=0.1[bg];[1:a]volume=1.5[narr];[bg][narr]amix=inputs=2:duration=longest[a]",
            "-map", "0:v", "-map", "[a]", "-t", str(dur + 1),
            "-c:v", "copy", "-c:a", "aac", str(output),
        ], capture_output=True)


def _narration_dark_bg(narration: Path, output: Path, text: str, dur: float):
    """Dark background with text + narration."""
    safe = text.replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")[:60] if text else ""
    vf = ""
    if safe:
        lines = []
        words = safe.split()
        line = ""
        for w in words:
            if len(line + " " + w) > 35:
                lines.append(line.strip())
                line = w
            else:
                line += " " + w
        if line.strip():
            lines.append(line.strip())

        parts = []
        for j, ln in enumerate(lines[:3]):
            y = 450 + j * 55
            parts.append(
                f"drawtext=text='{ln}':"
                f"fontsize=36:fontcolor=white:borderw=2:bordercolor=black:"
                f"x=(w-tw)/2:y={y}"
            )
        vf = ",".join(parts)

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x121223:s=1920x1080:d={dur}:r=25",
        "-i", str(narration),
        "-map", "0:v", "-map", "1:a", "-shortest",
    ]
    if vf:
        cmd.extend(["-vf", vf])
    cmd.extend([
        "-c:v", "libx264", "-crf", "22", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(output),
    ])
    subprocess.run(cmd, check=True, capture_output=True)


if __name__ == "__main__":
    main()
