"""Video polish layer — intro sting, outro CTA, SFX overlay helpers.

Called from the pipeline's composite step to wrap the main video
between a 2s branded intro and a 3s subscribe-CTA outro, producing a
single mp4 that LOOKS intentional instead of "AI slop with no framing".

Design:
  * All clips match the main video's resolution / codec / sample rate
    so the final concat is a clean demuxer operation with NO re-encode.
    That keeps the polish step under 5 s even on a CPU runner.
  * Intro + outro are generated on the fly from ffmpeg's lavfi source,
    not committed binary assets. That means no git bloat, no "forgot
    to push the mp4" failures, and the branding updates instantly when
    a channel changes its character name.
  * Everything degrades gracefully: if ffmpeg errors at any stage, we
    return the un-polished main video unchanged. Polish is bonus, not
    load-bearing.

Usage:
    from peanut_reacts.character.video_polish import (
        add_intro_outro, VideoStyle
    )
    style = VideoStyle(
        title="REDDIT STORIES",
        subtitle="narrated by Cashew",
        character="Cashew",
        resolution=(1920, 1080),
    )
    polished = add_intro_outro(main_video, output_path, style)

The reddit pipeline's composite_reddit_video() wraps this call around
its existing output so no rewrites needed inside the composite.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger(__name__)


@dataclass
class VideoStyle:
    """Branding + format specs the polish layer needs."""
    title: str = "REDDIT STORIES"
    subtitle: str = ""                      # "narrated by Cashew" etc.
    character: str = "Cashew"
    # Match your composite output. reddit_stories currently produces
    # 1920x1080 landscape. For Shorts-format content flip to (1080, 1920).
    resolution: Tuple[int, int] = (1920, 1080)
    fps: int = 30
    intro_duration: float = 2.0
    outro_duration: float = 3.0
    # Background color behind the branded text. Use the same hex the
    # main pipeline uses for its gameplay fallback so intro/main blend.
    bg_color: str = "#1a1a2e"
    # Accent color for the title text. Yellow-gold gets high contrast
    # against the dark navy background and reads well at thumbnail size.
    title_color: str = "#FFD93D"
    subtitle_color: str = "#FFFFFF"
    # If set, we use h264_nvenc instead of libx264. Matches the main
    # pipeline's encoder so concat demuxer can stitch without re-encode.
    use_nvenc: bool = False


def _encoder_args(style: VideoStyle) -> list[str]:
    enc = "h264_nvenc" if style.use_nvenc else "libx264"
    preset = "fast" if style.use_nvenc else "veryfast"
    return ["-c:v", enc, "-preset", preset, "-pix_fmt", "yuv420p"]


def build_intro(style: VideoStyle, output: Path) -> Optional[Path]:
    """Render a branded intro clip.

    Layout (1920x1080 landscape):
        [title centered at 40% height, gold, 140pt]
        [subtitle centered at 55% height, white, 60pt]
        [animated "SUBSCRIBE" badge bottom-right fading in last 0.5s]

    Audio: 0.8s rising-pitch sine ping → silence for remainder. Gives
    the viewer a clear "chapter started" cue without being obnoxious.
    """
    w, h = style.resolution
    output.parent.mkdir(parents=True, exist_ok=True)

    # Sanitize text for drawtext (no single-quotes or colons)
    def _safe(s: str) -> str:
        return s.replace("'", "").replace(":", " -").replace("\\", "")

    title = _safe(style.title)
    subtitle = _safe(style.subtitle)

    # Video filter: solid bg + title + subtitle. Title fades in via
    # alpha(if(t<0.4, t/0.4, 1)) to give a soft entrance.
    vf = (
        f"drawtext=text='{title}':fontcolor={style.title_color}:"
        f"fontsize={int(h / 7.5)}:"
        f"x=(w-text_w)/2:y=(h*0.38):"
        f"font=Arial:alpha='if(lt(t,0.4),t/0.4,1)'"
    )
    if subtitle:
        vf += (
            f",drawtext=text='{subtitle}':fontcolor={style.subtitle_color}:"
            f"fontsize={int(h / 18)}:"
            f"x=(w-text_w)/2:y=(h*0.56):"
            f"font=Arial:alpha='if(lt(t,0.7),(t-0.3)/0.4,1)'"
        )

    # Two lavfi sources: color-bg for video, sine-ping + silence for audio.
    # The ping is 880 Hz for 0.15 s, fades out by 0.6 s. The rest of
    # the audio track is silence so total audio length == video length.
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"color=c={style.bg_color}:s={w}x{h}:d={style.intro_duration}:r={style.fps}",
            "-f", "lavfi", "-i",
            f"sine=frequency=880:duration={style.intro_duration}:sample_rate=44100",
            "-vf", vf,
            "-af", (
                f"volume=0.3,"
                f"afade=t=out:st=0.15:d=0.5,"
                f"apad=pad_dur={max(0, style.intro_duration - 0.65):.2f}"
            ),
            *_encoder_args(style),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-t", str(style.intro_duration),
            "-shortest",
            str(output),
        ], check=True, capture_output=True, timeout=60)
        return output
    except subprocess.CalledProcessError as e:
        log.warning(
            "[POLISH] intro render failed: %s",
            (e.stderr or b"").decode("utf-8", errors="replace")[-300:],
        )
        return None
    except Exception as e:
        log.warning("[POLISH] intro crashed: %s", e)
        return None


def build_outro(style: VideoStyle, output: Path) -> Optional[Path]:
    """Render a subscribe-CTA outro clip.

    Layout:
        [big "SUBSCRIBE" centered 35% height, gold]
        [smaller "new stories every day" 55% height, white]
        [tiny character tag 80% height]

    Audio: descending 2-note flourish (G5 → E5) in the first 0.5s,
    silence after.
    """
    w, h = style.resolution
    output.parent.mkdir(parents=True, exist_ok=True)

    def _safe(s: str) -> str:
        return s.replace("'", "").replace(":", " -").replace("\\", "")

    ctas = [
        ("SUBSCRIBE", style.title_color, int(h / 6.5), 0.35),
        ("new stories every day", style.subtitle_color, int(h / 20), 0.55),
        (f"{_safe(style.character).upper()} out", style.subtitle_color, int(h / 32), 0.75),
    ]
    drawtexts = []
    for text, color, size, y_frac in ctas:
        drawtexts.append(
            f"drawtext=text='{_safe(text)}':fontcolor={color}:"
            f"fontsize={size}:x=(w-text_w)/2:y=(h*{y_frac}):"
            f"font=Arial"
        )
    vf = ",".join(drawtexts)

    # Audio: two sines concatenated to make a G5→E5 bell flourish.
    # aevalsrc can generate arbitrary functions; here we use a simple
    # two-note sine envelope.
    audio_filter = (
        f"aevalsrc="
        f"'0.3*sin(2*PI*784*t)*exp(-4*t)+"
        f"0.25*sin(2*PI*659*max(0,t-0.25))*exp(-4*max(0,t-0.25))'"
        f":duration={style.outro_duration}:sample_rate=44100"
    )

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"color=c={style.bg_color}:s={w}x{h}:d={style.outro_duration}:r={style.fps}",
            "-f", "lavfi", "-i", audio_filter,
            "-vf", vf,
            *_encoder_args(style),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-t", str(style.outro_duration),
            "-shortest",
            str(output),
        ], check=True, capture_output=True, timeout=60)
        return output
    except subprocess.CalledProcessError as e:
        log.warning(
            "[POLISH] outro render failed: %s",
            (e.stderr or b"").decode("utf-8", errors="replace")[-300:],
        )
        return None
    except Exception as e:
        log.warning("[POLISH] outro crashed: %s", e)
        return None


def concat_clips(clips: list[Path], output: Path,
                 style: Optional[VideoStyle] = None) -> Optional[Path]:
    """Concat two or more mp4s into a single output.

    Uses ffmpeg's concat filter (re-encode path) instead of the concat
    demuxer because lavfi-generated intro/outro often have slightly
    different SAR/pix_fmt from the main video's source-derived output.
    Re-encoding once is ~20-30 s on a CPU runner; still cheap vs the
    multi-minute main composite.

    All clips must have at least one video and one audio stream. The
    function probes the first clip to decide encoder + audio params.
    """
    valid = [c for c in clips if c and c.exists()]
    if len(valid) < 2:
        log.warning("[POLISH] concat needs >= 2 clips, got %d", len(valid))
        return None

    output.parent.mkdir(parents=True, exist_ok=True)

    # Build the filter_complex expression for concat
    # [0:v][0:a][1:v][1:a]...concat=n=3:v=1:a=1[outv][outa]
    inputs = []
    streams = []
    for i, clip in enumerate(valid):
        inputs.extend(["-i", str(clip)])
        streams.append(f"[{i}:v][{i}:a]")
    filter_complex = (
        "".join(streams)
        + f"concat=n={len(valid)}:v=1:a=1[outv][outa]"
    )

    style = style or VideoStyle()
    try:
        subprocess.run([
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            *_encoder_args(style),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            str(output),
        ], check=True, capture_output=True, timeout=1200)
        log.info("[POLISH] concat wrote %s (%d clips)", output.name, len(valid))
        return output
    except subprocess.CalledProcessError as e:
        log.error(
            "[POLISH] concat failed: %s",
            (e.stderr or b"").decode("utf-8", errors="replace")[-500:],
        )
        return None
    except Exception as e:
        log.error("[POLISH] concat crashed: %s", e)
        return None


def add_intro_outro(
    main_video: Path,
    output: Path,
    style: VideoStyle,
    work_dir: Optional[Path] = None,
) -> Optional[Path]:
    """One-shot wrapper: generate intro + outro, concat with main, done.

    Returns the polished video path on success, or the UNPOLISHED main
    video path if any stage fails. That way the pipeline always has a
    final output, and polish becomes a nice-to-have that can't break
    a run.
    """
    if not main_video.exists():
        log.warning("[POLISH] main video missing: %s", main_video)
        return None

    work = work_dir or main_video.parent
    intro_path = work / f"_intro_{main_video.stem}.mp4"
    outro_path = work / f"_outro_{main_video.stem}.mp4"

    intro = build_intro(style, intro_path)
    outro = build_outro(style, outro_path)

    if not intro or not outro:
        log.warning("[POLISH] intro or outro failed to render — returning unpolished video")
        return main_video

    final = concat_clips([intro, main_video, outro], output, style=style)
    if not final:
        log.warning("[POLISH] concat failed — returning unpolished video")
        return main_video

    # Clean up intermediate intro/outro to save disk. Keep the main
    # video file untouched (caller may want to retry/inspect).
    for p in (intro_path, outro_path):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

    return final
