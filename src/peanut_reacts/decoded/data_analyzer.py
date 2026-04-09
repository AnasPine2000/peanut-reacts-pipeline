"""
Sidemen Among Us transcript data analyzer.

Mines 119+ video transcripts for player patterns, speech analysis,
accusations, voting behavior, and key gameplay moments.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from peanut_reacts.core.srt import parse_srt

logger = logging.getLogger(__name__)

# Known Sidemen + frequent players
SIDEMEN = ["KSI", "Miniminter", "W2S", "TBJZL", "Behzinga", "Vikkstar", "Zerkaa"]
SIDEMEN_ALIASES = {
    "ksi": "KSI", "jj": "KSI", "olajide": "KSI", "folabi": "KSI",
    "miniminter": "Miniminter", "simon": "Miniminter", "mini": "Miniminter",
    "w2s": "W2S", "harry": "W2S", "wroetoshaw": "W2S",
    "tbjzl": "TBJZL", "tobi": "TBJZL",
    "behzinga": "Behzinga", "ethan": "Behzinga", "behz": "Behzinga",
    "vikkstar": "Vikkstar", "vik": "Vikkstar", "vikk": "Vikkstar",
    "zerkaa": "Zerkaa", "josh": "Zerkaa",
    "chip": "Chip", "chippo": "Chip",
    "randolph": "Randolph", "randy": "Randolph",
    "deji": "Deji",
    "tommy": "TommyInnit", "tommyinnit": "TommyInnit",
    "lachlan": "Lachlan", "lachy": "Lachlan",
}

# Gameplay keywords
IMPOSTER_KEYWORDS = ["imposter", "impostor", "killer", "murdered", "killed", "vented", "sabotage"]
CREWMATE_KEYWORDS = ["crewmate", "crew mate", "innocent", "clear", "cleared"]
MEETING_KEYWORDS = ["emergency meeting", "body reported", "dead body", "found a body", "report"]
ACCUSATION_PATTERNS = [
    r"(?:i think|it's|it was|vote|sus|suspicious|sussy|definitely)\s+(\w+)",
    r"(\w+)\s+(?:is sus|is the imposter|killed|vented|self-reported)",
]
ROLE_KEYWORDS = [
    "sheriff", "jester", "janitor", "morphling", "engineer", "scientist",
    "shapeshifter", "phantom", "vampire", "werewolf", "detonator",
    "bounty hunter", "spy", "traitor", "cannibal", "grinch", "thanos",
    "puppet master", "hacker", "wizard", "thor", "chameleon",
]


@dataclass
class PlayerMention:
    """A mention of a player in a transcript segment."""
    player: str
    timestamp: float
    context: str       # surrounding text
    video_title: str
    mention_type: str  # "accusation", "defense", "role_reveal", "kill", "general"


@dataclass
class PlayerProfile:
    """Aggregated data about a player across all videos."""
    name: str
    total_mentions: int = 0
    accusation_count: int = 0        # times accused by others
    accuser_count: int = 0           # times they accused someone
    role_mentions: dict = field(default_factory=dict)  # role → count
    common_phrases: list = field(default_factory=list)
    key_moments: list = field(default_factory=list)    # most notable timestamps
    videos_appeared_in: int = 0
    speech_samples: list = field(default_factory=list)  # example quotes


@dataclass
class AnalysisResult:
    """Complete analysis output for LLM consumption."""
    topic: str
    player_profiles: dict[str, PlayerProfile] = field(default_factory=dict)
    key_moments: list[dict] = field(default_factory=list)
    accusations_map: dict[str, list[str]] = field(default_factory=dict)  # accuser → [accused]
    role_distribution: dict[str, dict[str, int]] = field(default_factory=dict)  # player → {role: count}
    interesting_patterns: list[str] = field(default_factory=list)
    raw_stats: dict = field(default_factory=dict)


def _normalize_player(name: str) -> Optional[str]:
    """Normalize a player name to canonical form."""
    lower = name.lower().strip()
    return SIDEMEN_ALIASES.get(lower, None)


def parse_all_transcripts(srt_dir: Path) -> list[dict]:
    """Parse all SRT files in a directory tree."""
    transcripts = []
    for srt_file in sorted(srt_dir.rglob("*.en.srt")):
        try:
            segments = parse_srt(srt_file)
            if segments:
                transcripts.append({
                    "title": srt_file.stem.replace(".en", ""),
                    "path": str(srt_file),
                    "segments": segments,
                })
        except Exception:
            pass
    return transcripts


def analyze_player_mentions(
    transcripts: list[dict],
    target_player: str = "",
) -> dict[str, PlayerProfile]:
    """Analyze how often each player is mentioned and in what context."""
    profiles: dict[str, PlayerProfile] = {}

    for video in transcripts:
        title = video["title"]
        players_in_video: set[str] = set()

        for seg in video["segments"]:
            text = seg.get("text", "").lower()
            start = float(seg.get("start", 0))

            # Check for player mentions
            for alias, canonical in SIDEMEN_ALIASES.items():
                if alias in text:
                    players_in_video.add(canonical)

                    if target_player and canonical != target_player:
                        continue

                    if canonical not in profiles:
                        profiles[canonical] = PlayerProfile(name=canonical)
                    p = profiles[canonical]
                    p.total_mentions += 1

                    # Classify mention type
                    mention_type = "general"
                    if any(kw in text for kw in IMPOSTER_KEYWORDS):
                        mention_type = "imposter_related"
                    elif any(kw in text for kw in ACCUSATION_PATTERNS[0:1]):
                        mention_type = "accusation"
                        p.accusation_count += 1
                    elif any(kw in text for kw in MEETING_KEYWORDS):
                        mention_type = "meeting"

                    # Check for role mentions
                    for role in ROLE_KEYWORDS:
                        if role in text:
                            p.role_mentions[role] = p.role_mentions.get(role, 0) + 1

                    # Store speech sample (limit to interesting ones)
                    if len(p.speech_samples) < 50 and len(text) > 20:
                        p.speech_samples.append({
                            "text": seg.get("text", "").strip(),
                            "timestamp": start,
                            "video": title,
                            "type": mention_type,
                        })

        # Count videos appeared in
        for player in players_in_video:
            if player in profiles:
                profiles[player].videos_appeared_in += 1

    return profiles


def find_accusations(transcripts: list[dict]) -> dict[str, Counter]:
    """Find who accuses who across all videos."""
    accusations: dict[str, Counter] = defaultdict(Counter)

    for video in transcripts:
        full_text = " ".join(seg.get("text", "") for seg in video["segments"]).lower()

        # Look for "I think it's [name]" patterns
        for pattern in ACCUSATION_PATTERNS:
            for match in re.finditer(pattern, full_text):
                accused_raw = match.group(1)
                accused = _normalize_player(accused_raw)
                if accused:
                    # Try to figure out who's speaking (harder without diarization)
                    # For now, track the accused
                    accusations["unknown"][accused] += 1

    return dict(accusations)


def find_key_moments(
    transcripts: list[dict],
    keywords: Optional[list[str]] = None,
) -> list[dict]:
    """Find the most dramatic/interesting moments across all videos."""
    if keywords is None:
        keywords = [
            "oh my god", "no way", "what", "insane", "genius",
            "voted out", "ejected", "killed", "emergency",
            "i knew it", "called it", "betrayed", "backstab",
            "rage quit", "salty", "exposed", "caught",
        ]

    moments = []
    for video in transcripts:
        for seg in video["segments"]:
            text = seg.get("text", "").lower()
            start = float(seg.get("start", 0))

            score = sum(1 for kw in keywords if kw in text)
            if score >= 2:  # at least 2 keywords = interesting
                moments.append({
                    "video": video["title"],
                    "timestamp": start,
                    "text": seg.get("text", "").strip(),
                    "score": score,
                    "path": video["path"],
                })

    # Sort by score descending
    moments.sort(key=lambda m: m["score"], reverse=True)
    return moments[:100]  # top 100


def generate_player_report(
    player: str,
    transcripts: list[dict],
) -> dict:
    """Generate a comprehensive report for one player.

    Returns a dict ready to be passed to the LLM for script generation.
    """
    logger.info("Analyzing player: %s across %d videos", player, len(transcripts))

    profiles = analyze_player_mentions(transcripts, target_player=player)
    profile = profiles.get(player)

    if not profile:
        logger.warning("Player '%s' not found in transcripts", player)
        return {"error": f"Player {player} not found"}

    accusations = find_accusations(transcripts)
    moments = find_key_moments(transcripts)

    # Filter moments involving this player
    player_moments = [
        m for m in moments
        if player.lower() in m["text"].lower()
        or any(alias in m["text"].lower() for alias, canon in SIDEMEN_ALIASES.items() if canon == player)
    ]

    # Common phrases (most repeated words/phrases in their mentions)
    word_freq = Counter()
    for sample in profile.speech_samples:
        words = sample["text"].lower().split()
        for word in words:
            if len(word) > 3:
                word_freq[word] += 1

    report = {
        "player": player,
        "total_mentions": profile.total_mentions,
        "videos_appeared_in": profile.videos_appeared_in,
        "accusation_count": profile.accusation_count,
        "roles_played": dict(profile.role_mentions),
        "most_common_words": word_freq.most_common(20),
        "key_moments": player_moments[:20],
        "speech_samples": profile.speech_samples[:30],
        "times_accused_by_others": accusations.get("unknown", {}).get(player, 0),
    }

    logger.info(
        "Report: %d mentions, %d videos, %d key moments, %d roles",
        report["total_mentions"],
        report["videos_appeared_in"],
        len(report["key_moments"]),
        len(report["roles_played"]),
    )
    return report
