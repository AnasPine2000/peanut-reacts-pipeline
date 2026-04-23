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
import re
import subprocess
from dataclasses import dataclass, field
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


# ── SFX layer ──────────────────────────────────────────────────────────────
#
# Overlay short synthesized audio cues onto a video at specific
# timestamps. Three pre-canned SFX types, all generated from ffmpeg
# lavfi sources — no binary assets, no licensing, no network calls.
#
#   whoosh   ~0.5 s descending pink-noise burst — story transitions
#   ding     ~0.4 s 880 Hz decaying sine — punchline / chapter-start
#   scratch  ~0.3 s noise burst with pitch sweep — "wait WHAT"

SFX_DEFAULTS = {
    "whoosh":  {"duration": 0.55, "volume": 0.28},
    "ding":    {"duration": 0.45, "volume": 0.22},
    "scratch": {"duration": 0.35, "volume": 0.30},
}

# Emotion cue -> SFX mapping. Missing emotions are skipped silently.
EMOTION_SFX = {
    "shocked":   "scratch",
    "laughing":  "ding",
    "sarcastic": "ding",
    "excited":   "ding",
    # 'serious' / 'confused' -> no SFX (keeps the bed clean)
}

# Character-relative-position regex for emotion cues in the script.
# We capture both the cue type and its position in the stripped
# narration so we can map char-position -> time-offset later.
EMOTION_CUE_RE = re.compile(
    r"\[(shocked|laughing|sarcastic|serious|excited|confused)\]",
    re.IGNORECASE,
)


@dataclass
class SfxEvent:
    """One SFX beat at a specific video timestamp."""
    time_s: float     # seconds from start of video
    kind: str         # 'whoosh' | 'ding' | 'scratch'
    volume: float = 0.25

    def __post_init__(self):
        if self.kind not in SFX_DEFAULTS:
            raise ValueError(f"unknown SFX kind: {self.kind}")


def parse_emotion_cues(raw_script: str, total_duration_s: float) -> list[SfxEvent]:
    """Extract emotion-cue timestamps from the pre-TTS script.

    The narration script is generated by DeepSeek with `[shocked]`,
    `[laughing]`, etc. tags inline. reddit_pipeline strips them before
    TTS (they'd be spoken aloud otherwise), but they carry good
    structural signal for where exciting beats happen.

    We assume TTS pacing is roughly linear relative to character
    count — for a 6-minute narration that's close enough (±5 s per
    cue at worst) and perfectly adequate for SFX timing where
    viewer perception is ~200 ms tolerant.

    Returns SfxEvents sorted by time. Caller can mix with other
    sources (story-transition whooshes etc.) before passing to
    overlay_sfx.
    """
    if not raw_script or total_duration_s <= 0:
        return []

    # Strip cues FIRST to get the canonical "spoken text" char count.
    # Then walk the original script and note each cue's position in
    # the stripped stream — that's what maps to TTS time.
    stripped_total = len(EMOTION_CUE_RE.sub("", raw_script))
    if stripped_total < 50:   # very short script, skip
        return []

    events: list[SfxEvent] = []
    stripped_pos = 0
    cursor = 0
    for m in EMOTION_CUE_RE.finditer(raw_script):
        # Add the non-cue text between cursor and this cue to the
        # stripped position counter.
        stripped_pos += len(EMOTION_CUE_RE.sub("", raw_script[cursor:m.start()]))
        cue = m.group(1).lower()
        cursor = m.end()
        kind = EMOTION_SFX.get(cue)
        if not kind:
            continue
        # Map char position -> time position
        t = (stripped_pos / stripped_total) * total_duration_s
        events.append(SfxEvent(
            time_s=t,
            kind=kind,
            volume=SFX_DEFAULTS[kind]["volume"],
        ))
    return events


def story_transition_events(total_duration_s: float, num_stories: int = 3) -> list[SfxEvent]:
    """Generate whooshes at the approximate story-transition points.

    For a 3-story script, places whooshes at ~1/3 and ~2/3 of the total
    duration. Works as a belt-and-suspenders baseline — even if the
    emotion-cue parse returns nothing, the video still gets structural
    audio punctuation.

    Also plants a final 'ding' at (total - 4 s) as a "coming up,
    subscribe" cue just before the outro kicks in.
    """
    if total_duration_s <= 0 or num_stories < 2:
        return []
    events: list[SfxEvent] = []
    for i in range(1, num_stories):
        t = (i / num_stories) * total_duration_s
        events.append(SfxEvent(time_s=t, kind="whoosh",
                               volume=SFX_DEFAULTS["whoosh"]["volume"]))
    # Pre-outro ding (skip if the video is too short to host it)
    if total_duration_s > 30:
        events.append(SfxEvent(
            time_s=max(0, total_duration_s - 4.0),
            kind="ding",
            volume=SFX_DEFAULTS["ding"]["volume"] * 0.8,
        ))
    return sorted(events, key=lambda e: e.time_s)


def _sfx_lavfi(kind: str, volume: float) -> str:
    """Return an ffmpeg lavfi source string that synthesizes the named SFX.

    Kept as a pure string-returning function so the overlay builder
    can embed these in filter_complex without shelling out per-SFX.
    Each lavfi output is a stereo signal ready to amix.
    """
    spec = SFX_DEFAULTS[kind]
    dur = spec["duration"]
    if kind == "whoosh":
        # Pink-ish noise burst with fast attack + slow release.
        # anoisesrc + volume envelope + bandpass to tame the highs.
        return (
            f"anoisesrc=color=pink:duration={dur}:amplitude=0.9,"
            f"bandpass=f=800:w=1200,"
            f"volume={volume}:eval=frame,"
            f"afade=t=in:d=0.04,afade=t=out:st={dur * 0.35:.3f}:d={dur * 0.6:.3f}"
        )
    if kind == "ding":
        # 880 Hz sine with exponential decay.
        return (
            f"sine=frequency=880:duration={dur}:sample_rate=44100,"
            f"volume={volume},"
            f"afade=t=out:st=0.05:d={dur - 0.05:.3f}"
        )
    if kind == "scratch":
        # Noise burst with a quick pitch sweep feel via tempo ramp.
        return (
            f"anoisesrc=color=white:duration={dur}:amplitude=1.0,"
            f"atempo=1.8,"
            f"volume={volume},"
            f"afade=t=in:d=0.02,afade=t=out:st={dur * 0.4:.3f}:d={dur * 0.55:.3f}"
        )
    # Unknown: silence (should never happen due to dataclass validation)
    return f"anullsrc=duration={dur}"


def overlay_sfx(
    video: Path,
    events: list[SfxEvent],
    output: Path,
    style: Optional[VideoStyle] = None,
) -> Optional[Path]:
    """Overlay a list of SFX events onto the video's audio track.

    Uses ffmpeg's amix filter to blend the main audio with N SFX
    generators, each delayed to its event time via adelay. Produces
    a single-pass re-encode (video is copied, audio is mixed + AAC
    re-encoded). Typical overhead: ~5-10 s on a CPU runner for a
    6-minute video.

    If events is empty, we stream-copy both tracks so this is a no-op
    wrapper. Safe to always call.
    """
    if not video.exists():
        log.warning("[POLISH/SFX] video missing: %s", video)
        return None
    output.parent.mkdir(parents=True, exist_ok=True)

    # Clamp events to within the video's duration + drop dupes at same t
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=10,
        )
        video_dur = float(r.stdout.strip()) if r.stdout.strip() else 0
    except Exception:
        video_dur = 0

    events = [e for e in events if 0 < e.time_s < (video_dur - 0.5 if video_dur else 1e9)]
    events = sorted(events, key=lambda e: e.time_s)

    if not events:
        log.info("[POLISH/SFX] no events — stream-copy passthrough")
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", str(video),
                "-c", "copy", "-movflags", "+faststart",
                str(output),
            ], check=True, capture_output=True, timeout=120)
            return output
        except Exception as e:
            log.warning("[POLISH/SFX] passthrough copy failed: %s", e)
            return video

    # Build ffmpeg command with one lavfi input per SFX event.
    #
    # amix auto-normalization tip: amix divides each input by the total
    # number of inputs to avoid clipping. With 9 inputs that cuts every
    # SFX to ~11% of its intended volume, which makes them inaudible.
    # The `normalize=0` option would disable this but it's not available
    # on older ffmpeg builds (surfaced as "Option not found"). Portable
    # fix: pre-multiply EVERY input by the total input count. amix then
    # divides by the same count and we land at unit gain.
    num_inputs = len(events) + 1   # main audio + N SFX streams
    boost = float(num_inputs)

    inputs = ["-i", str(video)]
    sfx_streams = []
    for i, ev in enumerate(events):
        inputs.extend(["-f", "lavfi", "-i", _sfx_lavfi(ev.kind, ev.volume)])
        delay_ms = int(ev.time_s * 1000)
        sfx_streams.append(
            f"[{i + 1}:a]adelay={delay_ms}|{delay_ms},"
            f"volume={boost},"
            f"aresample=44100[sfx{i}]"
        )

    # Pre-boost main audio, then amix everything. duration=first ties
    # the output to the original audio length (no silence-padding if
    # the last SFX starts late).
    main_boost = f"[0:a]volume={boost}[main]"
    amix_inputs = "[main]" + "".join(f"[sfx{i}]" for i in range(len(events)))
    filter_complex = (
        main_boost
        + ";"
        + ";".join(sfx_streams)
        + ";"
        + amix_inputs
        + f"amix=inputs={num_inputs}:duration=first:dropout_transition=0[aout]"
    )

    style = style or VideoStyle()
    try:
        subprocess.run([
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",   # video untouched — no quality loss
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            str(output),
        ], check=True, capture_output=True, timeout=600)
        log.info("[POLISH/SFX] %d SFX overlaid: %s",
                 len(events), output.name)
        return output
    except subprocess.CalledProcessError as e:
        log.error(
            "[POLISH/SFX] overlay failed: %s",
            (e.stderr or b"").decode("utf-8", errors="replace")[-500:],
        )
        return None
    except Exception as e:
        log.error("[POLISH/SFX] overlay crashed: %s", e)
        return None


def apply_full_polish(
    main_video: Path,
    output: Path,
    style: VideoStyle,
    raw_script: Optional[str] = None,
    num_stories: int = 3,
    work_dir: Optional[Path] = None,
) -> Path:
    """One-call: SFX overlay -> intro -> main -> outro. Returns final path.

    Order matters:
      1. SFX first — overlays onto the main audio before intro/outro
         are concat'd. That way the SFX timeline is anchored to the
         main video's original timestamps, not to the extended
         timeline after intro.
      2. Intro + outro concat — final step that wraps everything
         with branded opening/closing.

    If any stage fails, falls back to the most-polished path we got.
    """
    if not main_video.exists():
        log.warning("[POLISH] main video missing: %s", main_video)
        return main_video

    work = work_dir or main_video.parent

    # Collect SFX events from all sources
    events: list[SfxEvent] = []
    # Probe duration
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(main_video)],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(r.stdout.strip()) if r.stdout.strip() else 0
    except Exception:
        duration = 0

    if duration > 30:
        events.extend(story_transition_events(duration, num_stories))
        if raw_script:
            events.extend(parse_emotion_cues(raw_script, duration))
    log.info("[POLISH] %d SFX events planned (duration=%.1fs, script=%s)",
             len(events), duration, "yes" if raw_script else "no")

    sfx_path = work / f"_sfx_{main_video.stem}.mp4"
    with_sfx = overlay_sfx(main_video, events, sfx_path, style=style) or main_video

    # Intro + outro wrap
    result = add_intro_outro(with_sfx, output, style, work_dir=work) or with_sfx

    # Cleanup intermediate SFX file (keep main_video + output)
    if sfx_path != main_video and sfx_path != result:
        try:
            sfx_path.unlink(missing_ok=True)
        except OSError:
            pass

    return result
