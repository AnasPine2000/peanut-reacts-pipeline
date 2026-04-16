"""Music sources for the ambient pipeline.

Three layers:
1. Pixabay CC0 - free, fully owned by us when downloaded, zero Content ID risk
2. Public domain classical via MIDI render (free, infinite, zero risk)
3. Suno helper - manual batch import or future API integration

All sources output to production/ambient/tracks/{theme}/*.mp3
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class Track:
    path: Path
    title: str = ""
    duration_seconds: float = 0
    source: str = ""  # pixabay | suno | midi | manual
    bpm: int = 0
    mood: str = ""
    cc_license: str = "CC0"


# ── Pixabay CC0 scraper ────────────────────────────────────────────

PIXABAY_API_KEY_ENV = "PIXABAY_API_KEY"


def search_pixabay_music(query: str, max_results: int = 20, api_key: str = "") -> list[dict]:
    """Search Pixabay for CC0 music tracks.

    Note: Pixabay's official API doesn't include audio search currently.
    This uses the public web JSON endpoint which is rate-limited.
    """
    import httpx
    try:
        url = f"https://pixabay.com/api/?key={api_key}&q={query}&safesearch=true&per_page={max_results}"
        if not api_key:
            log.warning("No Pixabay API key — manual download required")
            return []

        resp = httpx.get(url, timeout=30)
        if resp.status_code != 200:
            log.warning("Pixabay search failed: %s", resp.status_code)
            return []
        return resp.json().get("hits", [])
    except Exception as e:
        log.error("Pixabay search error: %s", e)
        return []


def import_pixabay_directory(source_dir: Path, target_dir: Path, theme: str = "general") -> list[Track]:
    """Import manually-downloaded Pixabay tracks into the tracks library.

    Pixabay's web UI lets you bulk download. Drop them in source_dir,
    this organizes them under target_dir/{theme}/.
    """
    target = target_dir / theme
    target.mkdir(parents=True, exist_ok=True)

    imported = []
    for ext in ("*.mp3", "*.wav", "*.flac", "*.ogg"):
        for src in source_dir.glob(ext):
            dst = target / src.name
            if not dst.exists():
                import shutil
                shutil.copy2(src, dst)
            duration = _get_audio_duration(dst)
            imported.append(Track(
                path=dst,
                title=src.stem,
                duration_seconds=duration,
                source="pixabay",
                cc_license="CC0",
            ))
    log.info("Imported %d tracks into %s", len(imported), target)
    return imported


# ── Public Domain Classical (MIDI render) ──────────────────────────

PUBLIC_DOMAIN_PIECES = [
    # Classical works that are in the public domain (composer died >70 years ago)
    {"composer": "Erik Satie", "piece": "Gymnopédie No.1", "mood": "melancholic"},
    {"composer": "Erik Satie", "piece": "Gymnopédie No.3", "mood": "peaceful"},
    {"composer": "Erik Satie", "piece": "Gnossienne No.1", "mood": "mysterious"},
    {"composer": "Claude Debussy", "piece": "Clair de Lune", "mood": "dreamy"},
    {"composer": "Claude Debussy", "piece": "Arabesque No.1", "mood": "elegant"},
    {"composer": "Frédéric Chopin", "piece": "Nocturne Op.9 No.2", "mood": "romantic"},
    {"composer": "Frédéric Chopin", "piece": "Prelude Op.28 No.4", "mood": "sad"},
    {"composer": "Maurice Ravel", "piece": "Pavane pour une infante défunte", "mood": "stately"},
    {"composer": "Johann Sebastian Bach", "piece": "Prelude in C", "mood": "calm"},
    {"composer": "Johann Sebastian Bach", "piece": "Air on G String", "mood": "serene"},
    {"composer": "Ludwig van Beethoven", "piece": "Moonlight Sonata 1st mvt", "mood": "moody"},
    {"composer": "Antonio Vivaldi", "piece": "Largo (Winter)", "mood": "cold"},
]


def list_public_domain_pieces() -> list[dict]:
    """Return the list of curated public domain piano/orchestral pieces."""
    return PUBLIC_DOMAIN_PIECES


def render_midi_to_audio(midi_path: Path, output_path: Path,
                         soundfont: Optional[Path] = None) -> bool:
    """Render a MIDI file to MP3 using FluidSynth + ffmpeg.

    Requires fluidsynth installed and a SoundFont (e.g. FluidR3_GM.sf2).
    """
    if not midi_path.exists():
        log.error("MIDI not found: %s", midi_path)
        return False

    if soundfont is None or not soundfont.exists():
        log.warning("No soundfont configured — skipping MIDI render")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wav_temp = output_path.with_suffix(".wav")

    try:
        subprocess.run(
            ["fluidsynth", "-ni", "-F", str(wav_temp), str(soundfont), str(midi_path)],
            check=True, capture_output=True, timeout=300,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_temp),
             "-codec:a", "libmp3lame", "-b:a", "192k", str(output_path)],
            check=True, capture_output=True, timeout=120,
        )
        wav_temp.unlink(missing_ok=True)
        log.info("Rendered MIDI to %s", output_path)
        return True
    except Exception as e:
        log.error("MIDI render failed: %s", e)
        return False


# ── Suno (manual import for now, API later) ───────────────────────

def import_suno_directory(source_dir: Path, target_dir: Path, theme: str = "suno") -> list[Track]:
    """Import manually-downloaded Suno tracks.

    Workflow:
    1. Generate tracks in suno.com web UI (Pro account required for commercial use)
    2. Bulk-download to source_dir
    3. Run this function to organize them
    """
    target = target_dir / theme
    target.mkdir(parents=True, exist_ok=True)

    imported = []
    for src in source_dir.glob("*.mp3"):
        dst = target / src.name
        if not dst.exists():
            import shutil
            shutil.copy2(src, dst)
        duration = _get_audio_duration(dst)
        imported.append(Track(
            path=dst,
            title=src.stem,
            duration_seconds=duration,
            source="suno",
            cc_license="commercial",
        ))
    log.info("Imported %d Suno tracks into %s", len(imported), target)
    return imported


# ── Track library management ──────────────────────────────────────

def scan_track_library(library_dir: Path) -> list[Track]:
    """Scan the entire ambient/tracks directory and return all Track objects."""
    if not library_dir.exists():
        return []

    tracks = []
    for ext in ("*.mp3", "*.wav", "*.flac", "*.ogg"):
        for path in library_dir.rglob(ext):
            theme = path.parent.name if path.parent != library_dir else "general"
            duration = _get_audio_duration(path)
            tracks.append(Track(
                path=path,
                title=path.stem,
                duration_seconds=duration,
                source=theme,
                mood=theme,
            ))
    log.info("Library scan: %d tracks across %d themes",
             len(tracks), len(set(t.source for t in tracks)))
    return tracks


def filter_by_theme(tracks: list[Track], theme: str) -> list[Track]:
    """Filter tracks to those tagged with a specific theme/mood."""
    theme_lower = theme.lower()
    return [t for t in tracks if theme_lower in (t.source or "").lower()
            or theme_lower in (t.mood or "").lower()
            or theme_lower in (t.title or "").lower()]


def _get_audio_duration(path: Path) -> float:
    """Get audio duration in seconds via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0


# ── Sample track downloader (free CC0 sources for testing) ────────

def download_test_tracks(target_dir: Path) -> list[Track]:
    """Download a small set of CC0 ambient tracks for testing the pipeline.

    Uses Pixabay's direct download URLs for known CC0 ambient tracks.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    # These are sample CC0 URLs - users should replace with their own curation
    test_urls = [
        # Pixabay CC0 ambient tracks (replace with real URLs)
    ]

    if not test_urls:
        log.info("No test URLs configured. Add CC0 tracks manually to %s", target_dir)
        return []

    import httpx
    tracks = []
    for i, url in enumerate(test_urls):
        try:
            resp = httpx.get(url, timeout=60)
            if resp.status_code == 200:
                fname = target_dir / f"test_track_{i:03d}.mp3"
                fname.write_bytes(resp.content)
                duration = _get_audio_duration(fname)
                tracks.append(Track(
                    path=fname,
                    title=f"Test Track {i+1}",
                    duration_seconds=duration,
                    source="pixabay_cc0",
                ))
        except Exception as e:
            log.warning("Test download %d failed: %s", i, e)
    return tracks
