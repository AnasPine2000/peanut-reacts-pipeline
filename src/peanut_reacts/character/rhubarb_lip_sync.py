"""Rhubarb-driven lip sync for the Peanut character.

Runs the Rhubarb Lip Sync CLI on a TTS audio file, gets back a timed
sequence of 9 viseme codes (A-H plus X for rest), and emits an ffmpeg
filter-graph that composites the matching Peanut mouth sprite onto
the body frame at each timestamp.

Rhubarb's 9 shapes (Hanna-Barbera / Preston Blair taxonomy):
  A  Closed mouth.         Consonants M, B, P.
  B  Slightly open, teeth  EE / IH / EH (clenched).
     showing or touching.
  C  Open mouth.           EH / AE (as in "red", "cat").
  D  Wide open mouth.      AA / AO (as in "father", "thought").
  E  Slightly rounded.     AH / ER (as in "about", "bird").
  F  Puckered, tight.      OH / UH / OO / W.
  G  Upper teeth on        F and V.
     lower lip.
  H  Tongue visible.       L and TH.
  X  Rest / idle.          Silence — default pose.

Design:
  * `generate_mouth_cues(audio_path)` shells out to rhubarb.exe and
    returns the list of {start, end, value} dicts from the JSON output.
  * `build_lip_sync_filter(cues, base_video_path, sprites_dir)` returns
    an ffmpeg filter_complex string that overlays each sprite during its
    active window via `-enable='between(t, start, end)'`.
  * `composite_talking_peanut(audio, body_video, sprites_dir, out_path)`
    is the one-call pipeline: run rhubarb, build filter, invoke ffmpeg.

All failures fall back gracefully: if rhubarb is missing, or a sprite
file is missing, we return the original body video unchanged. The
calling pipeline then uses the old static-face path — no crash, just
no lip sync.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Canonical location for the Rhubarb CLI. Downloaded + unzipped by the
# Phase 1A setup commit. Falls back to system PATH if someone has
# installed it elsewhere.
DEFAULT_RHUBARB_EXE = (
    PROJECT_ROOT
    / "third_party"
    / "Rhubarb-Lip-Sync-1.14.0-Windows"
    / "rhubarb.exe"
)

# Sprite filenames we expect in sprites_dir. All must be the same
# canvas size with transparent background. The mouth should be
# positioned exactly where it will composite onto the body frame.
SPRITE_FILENAMES = {
    "A": "mouth_A_closed.png",
    "B": "mouth_B_small.png",
    "C": "mouth_C_open.png",
    "D": "mouth_D_wide.png",
    "E": "mouth_E_round_soft.png",
    "F": "mouth_F_round_tight.png",
    "G": "mouth_G_teeth_on_lip.png",
    "H": "mouth_H_tongue.png",
    "X": "mouth_X_rest.png",
}

# Minimum frame a sprite must stay on-screen even if Rhubarb reports a
# shorter window. Prevents strobing on rapid-fire phonemes. 0.06 s ≈ 2
# frames at 30 fps.
MIN_SPRITE_DURATION_S = 0.06


@dataclass
class MouthCue:
    start: float
    end: float
    viseme: str    # one of A-H, X

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _find_rhubarb(override: Optional[Path] = None) -> Optional[Path]:
    """Locate the Rhubarb binary. Honor explicit override, then PATH, then default."""
    if override:
        p = Path(override)
        return p if p.exists() else None
    if DEFAULT_RHUBARB_EXE.exists():
        return DEFAULT_RHUBARB_EXE
    sys_path = shutil.which("rhubarb")
    return Path(sys_path) if sys_path else None


def generate_mouth_cues(
    audio_path: Path,
    rhubarb_exe: Optional[Path] = None,
) -> list[MouthCue]:
    """Run Rhubarb on an audio file and return the timed viseme cues.

    Rhubarb accepts mp3, wav, and ogg as of 1.14. We still convert to
    16 kHz mono WAV via ffmpeg first — it's fast and sidesteps any
    edge-case codec issues on exotic MP3 variants.
    """
    exe = _find_rhubarb(rhubarb_exe)
    if not exe:
        log.warning(
            "[RHUBARB] Binary not found. Download v1.14.0 to %s or install on PATH.",
            DEFAULT_RHUBARB_EXE,
        )
        return []

    if not audio_path.exists():
        log.warning("[RHUBARB] Audio missing: %s", audio_path)
        return []

    # Convert to 16 kHz mono WAV (Rhubarb's native input format)
    wav_path = audio_path.with_suffix(".rhubarb.wav")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(audio_path),
                "-ar", "16000", "-ac", "1",
                str(wav_path),
            ],
            check=True, capture_output=True, timeout=30,
        )
    except Exception as e:
        log.warning("[RHUBARB] ffmpeg transcode failed: %s", e)
        return []

    # Run Rhubarb
    json_path = audio_path.with_suffix(".rhubarb.json")
    try:
        subprocess.run(
            [
                str(exe),
                "-f", "json",
                "-o", str(json_path),
                str(wav_path),
            ],
            check=True, capture_output=True, timeout=60,
        )
    except subprocess.CalledProcessError as e:
        log.warning(
            "[RHUBARB] Analysis failed: %s",
            (e.stderr or b"").decode("utf-8", errors="replace")[-300:],
        )
        return []
    except Exception as e:
        log.warning("[RHUBARB] Analysis crashed: %s", e)
        return []
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        log.warning("[RHUBARB] JSON parse failed: %s", e)
        return []
    finally:
        try:
            json_path.unlink(missing_ok=True)
        except OSError:
            pass

    cues_raw = payload.get("mouthCues", [])
    cues = [
        MouthCue(
            start=float(c.get("start", 0)),
            end=float(c.get("end", 0)),
            viseme=str(c.get("value", "X")).upper(),
        )
        for c in cues_raw
    ]

    # Collapse sub-threshold cues into their predecessor. This smooths
    # out Rhubarb's occasional single-frame viseme flips that would
    # otherwise strobe on screen.
    smoothed: list[MouthCue] = []
    for cue in cues:
        if smoothed and cue.duration < MIN_SPRITE_DURATION_S:
            smoothed[-1] = MouthCue(
                start=smoothed[-1].start,
                end=cue.end,
                viseme=smoothed[-1].viseme,
            )
        else:
            smoothed.append(cue)

    log.info(
        "[RHUBARB] %d cues for %s (duration %.2fs)",
        len(smoothed), audio_path.name, payload.get("metadata", {}).get("duration", 0),
    )
    return smoothed


def verify_sprite_pack(sprites_dir: Path) -> tuple[bool, list[str]]:
    """Check that all 9 mouth sprites exist in the given directory.

    Returns (ok, missing_names). When ok is False, callers should skip
    lip sync and fall back to the static face.
    """
    missing = []
    for viseme, filename in SPRITE_FILENAMES.items():
        if not (sprites_dir / filename).exists():
            missing.append(f"{viseme} -> {filename}")
    return (not missing, missing)


def build_lip_sync_filter(
    cues: list[MouthCue],
    sprites_dir: Path,
    body_input_index: int = 0,
    mouth_x: int = 0,
    mouth_y: int = 0,
    base_time_offset: float = 0.0,
) -> tuple[str, list[Path], list[str]]:
    """Assemble an ffmpeg filter_complex expression for the viseme overlay.

    Returns:
        filter_complex:  the filter graph string
        sprite_paths:    list of sprite PNG paths (one per unique viseme
                         actually used) — caller must pass these as
                         additional -i inputs to ffmpeg, in this order
        final_label:     the output video stream label (e.g. '[vout]')

    The expression chains overlays: base -> overlay[sprite0] enabled
    during cue 0 -> overlay[sprite1] enabled during cue 1 -> ... ->
    final output. Each overlay uses enable='between(t,start,end)' so
    only the active viseme is drawn for any given frame.

    `base_time_offset` is added to every cue's start/end. Use it when
    splicing multiple reaction segments — if clip N starts at t=12.4 s
    in the full episode, pass 12.4 here so the sprite timings align.
    """
    unique_visemes = {c.viseme for c in cues}
    sprite_paths: list[Path] = []
    sprite_idx_by_viseme: dict[str, int] = {}
    for viseme in sorted(unique_visemes):
        filename = SPRITE_FILENAMES.get(viseme)
        if not filename:
            log.warning("[RHUBARB] Unknown viseme %r — skipping", viseme)
            continue
        p = sprites_dir / filename
        if not p.exists():
            log.warning("[RHUBARB] Sprite missing: %s", p)
            continue
        sprite_idx_by_viseme[viseme] = len(sprite_paths)
        sprite_paths.append(p)

    if not sprite_paths:
        log.warning("[RHUBARB] No sprites resolved — skipping lip sync")
        return "", [], f"[{body_input_index}:v]"

    # Build the overlay chain.
    #
    # Input indices: body at body_input_index, sprites start at
    # body_input_index + 1. If the caller passes body as -i #0 and three
    # sprites as -i #1,2,3, the filter refs look like [0:v], [1:v], etc.
    first_sprite_ffmpeg_input = body_input_index + 1
    sprite_stream_labels = [
        f"[{first_sprite_ffmpeg_input + i}:v]" for i in range(len(sprite_paths))
    ]

    parts: list[str] = []
    current_label = f"[{body_input_index}:v]"
    for step_idx, cue in enumerate(cues):
        sprite_idx = sprite_idx_by_viseme.get(cue.viseme)
        if sprite_idx is None:
            continue
        sprite_ref = sprite_stream_labels[sprite_idx]
        start = cue.start + base_time_offset
        end = cue.end + base_time_offset
        enable = f"between(t,{start:.3f},{end:.3f})"
        out_label = f"[vstep{step_idx}]"
        parts.append(
            f"{current_label}{sprite_ref}"
            f"overlay=x={mouth_x}:y={mouth_y}:enable='{enable}'"
            f"{out_label}"
        )
        current_label = out_label

    filter_complex = ";".join(parts)
    return filter_complex, sprite_paths, current_label


def composite_talking_peanut(
    audio_path: Path,
    body_video_path: Path,
    sprites_dir: Path,
    output_path: Path,
    mouth_x: int = 0,
    mouth_y: int = 0,
) -> Optional[Path]:
    """One-shot: analyze audio, composite talking Peanut, write the result.

    Convenience wrapper around generate_mouth_cues + build_lip_sync_filter
    + an ffmpeg invocation. Intended for smoke tests — the production
    pipeline will use build_lip_sync_filter directly inside its existing
    facecam composite pass so we don't add an extra re-encode.

    Returns the output path on success, or None on any failure (in
    which case the caller should fall back to the static body video).
    """
    if not audio_path.exists() or not body_video_path.exists():
        log.warning(
            "[RHUBARB] Missing input: audio=%s (exists=%s), body=%s (exists=%s)",
            audio_path, audio_path.exists(),
            body_video_path, body_video_path.exists(),
        )
        return None

    sprites_ok, missing = verify_sprite_pack(sprites_dir)
    if not sprites_ok:
        log.warning("[RHUBARB] Sprite pack incomplete. Missing: %s", ", ".join(missing))
        return None

    cues = generate_mouth_cues(audio_path)
    if not cues:
        log.warning("[RHUBARB] No cues generated — falling back")
        return None

    filter_complex, sprite_paths, final_label = build_lip_sync_filter(
        cues, sprites_dir,
        body_input_index=0,
        mouth_x=mouth_x, mouth_y=mouth_y,
    )
    if not filter_complex:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    inputs = ["-i", str(body_video_path)]
    for p in sprite_paths:
        inputs.extend(["-i", str(p)])

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", final_label,
        "-map", "0:a?",    # preserve body's audio track if present
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        log.info("[RHUBARB] Wrote talking Peanut: %s", output_path.name)
        return output_path
    except subprocess.CalledProcessError as e:
        log.error(
            "[RHUBARB] ffmpeg composite failed: %s",
            (e.stderr or b"").decode("utf-8", errors="replace")[-500:],
        )
        return None
    except Exception as e:
        log.error("[RHUBARB] composite crashed: %s", e)
        return None


# ── CLI smoke test ─────────────────────────────────────────────────────────

def _main_cli() -> int:
    """Command-line smoke test: `python -m peanut_reacts.character.rhubarb_lip_sync AUDIO.mp3`."""
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    ap = argparse.ArgumentParser()
    ap.add_argument("audio", type=Path)
    ap.add_argument("--rhubarb", type=Path, default=None,
                    help="Path to rhubarb.exe (auto-detected if omitted)")
    args = ap.parse_args()

    cues = generate_mouth_cues(args.audio, rhubarb_exe=args.rhubarb)
    if not cues:
        print("No cues generated.")
        return 1

    print(f"Got {len(cues)} cues for {args.audio.name}:")
    for c in cues[:40]:
        print(f"  {c.start:5.2f}s -> {c.end:5.2f}s  {c.viseme}  ({c.duration:.2f}s)")
    if len(cues) > 40:
        print(f"  ... and {len(cues) - 40} more")

    # Histogram
    from collections import Counter
    hist = Counter(c.viseme for c in cues)
    print("\nViseme usage:")
    for v in "ABCDEFGHX":
        print(f"  {v}: {hist.get(v, 0)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(_main_cli())
