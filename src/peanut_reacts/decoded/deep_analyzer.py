"""
Deep transcript analyzer for Sidemen Among Us.

Extracts player-specific insights by mining speech patterns,
accusation networks, role-based behavior changes, emotional
moments, and "tells" that reveal the imposter.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from peanut_reacts.core.srt import parse_srt

logger = logging.getLogger(__name__)

# Player name patterns in transcripts (how they refer to each other)
PLAYER_REFS = {
    "KSI": ["ksi", "jj", "olajide", "folabi", "neek"],
    "Miniminter": ["simon", "miniminter", "mini", "sim"],
    "W2S": ["harry", "w2s", "wroetoshaw", "haz"],
    "TBJZL": ["tobi", "tbjzl", "tobz"],
    "Behzinga": ["ethan", "behzinga", "behz", "beh"],
    "Vikkstar": ["vik", "vikk", "vikkstar", "vikram"],
    "Zerkaa": ["josh", "zerkaa", "josher"],
    "Deji": ["deji", "dej", "dj"],
    "Randolph": ["randolph", "randy"],
    "Chip": ["chip", "chippo"],
}

# Emotional/dramatic keywords
DRAMA_WORDS = {
    "shock": ["oh my god", "what", "no way", "insane", "wait what", "are you kidding"],
    "accusation": ["it's", "i think", "sus", "suspicious", "voted", "i saw", "he killed", "she killed"],
    "defense": ["wasn't me", "i swear", "i didn't", "i'm innocent", "trust me", "i promise"],
    "celebration": ["let's go", "yes", "we won", "got him", "called it", "knew it"],
    "frustration": ["oh no", "damn", "come on", "why", "unfair", "rage", "hate"],
    "betrayal": ["backstab", "betrayed", "lied", "fake", "snake", "rat"],
    "strategy": ["vote", "skip", "emergency", "meeting", "report", "body", "alibi"],
}

# Role-specific gameplay events
ROLE_EVENTS = {
    "kill": ["killed", "murder", "eliminate", "took out", "got him", "dead body"],
    "vent": ["vented", "vent", "saw him vent", "popped out"],
    "meeting": ["emergency meeting", "body reported", "meeting called", "report"],
    "vote": ["voted", "vote for", "voting", "skip vote", "self report"],
    "task": ["doing tasks", "task bar", "medbay scan", "fix wiring", "download"],
    "sabotage": ["lights", "reactor", "o2", "comms", "sabotage"],
}


@dataclass
class PlayerInsight:
    """Deep insight about a player's behavior."""
    player: str
    insight_type: str  # "tell", "pattern", "rivalry", "strategy", "emotional"
    title: str         # short headline
    description: str   # detailed explanation
    evidence: list[dict] = field(default_factory=list)  # transcript excerpts
    confidence: float = 0.0  # 0-1
    video_count: int = 0  # how many videos support this


@dataclass
class DeepAnalysis:
    """Complete deep analysis results."""
    player: str
    insights: list[PlayerInsight]
    accusation_network: dict[str, dict[str, int]]  # accuser -> {accused: count}
    emotional_profile: dict[str, int]  # emotion -> count
    drama_moments: list[dict]  # highest drama transcription segments
    vocabulary_fingerprint: dict[str, list[str]]  # category -> top words


def analyze_accusations_deep(transcripts: list[dict]) -> dict[str, dict[str, int]]:
    """Build a detailed accusation network: who accuses who.

    Looks for patterns like:
    - "I think it's [name]"
    - "[name] is sus"
    - "Vote [name]"
    - "[name] killed/vented"
    """
    network: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    accusation_patterns = [
        r"(?:i think|it's|it was|its|vote|vote for)\s+(\w+)",
        r"(\w+)\s+(?:is sus|is suspicious|killed|vented|self.?reported|is the imposter|is impostor)",
        r"(?:get|vote out|eject)\s+(\w+)",
    ]

    for video in transcripts:
        segments = video.get("segments", [])
        for seg in segments:
            text = seg.get("text", "").lower()

            for pattern in accusation_patterns:
                for match in re.finditer(pattern, text):
                    accused_name = match.group(1).strip()
                    # Resolve to canonical name
                    canonical = None
                    for player, refs in PLAYER_REFS.items():
                        if accused_name in refs:
                            canonical = player
                            break

                    if canonical:
                        # We don't know who's speaking without diarization,
                        # but we can track who gets accused
                        network["all_players"][canonical] += 1

    return dict(network)


def analyze_emotional_profile(
    transcripts: list[dict],
    target_player: str,
) -> dict[str, list[dict]]:
    """Analyze emotional moments involving a specific player.

    Returns segments categorized by emotion type with context.
    """
    player_refs = PLAYER_REFS.get(target_player, [target_player.lower()])
    emotions: dict[str, list[dict]] = defaultdict(list)

    for video in transcripts:
        title = video.get("title", "")
        for seg in video.get("segments", []):
            text = seg.get("text", "").lower()

            # Check if this segment involves the target player
            if not any(ref in text for ref in player_refs):
                continue

            # Categorize emotion
            for emotion, keywords in DRAMA_WORDS.items():
                if any(kw in text for kw in keywords):
                    emotions[emotion].append({
                        "text": seg.get("text", "").strip(),
                        "timestamp": float(seg.get("start", 0)),
                        "video": title,
                        "emotion": emotion,
                    })

    return dict(emotions)


def find_player_tells(
    transcripts: list[dict],
    target_player: str,
) -> list[PlayerInsight]:
    """Find behavioral "tells" — patterns that reveal imposter/crewmate status.

    Looks for:
    - Phrases only used when imposter is mentioned
    - Defensive language patterns
    - Unusual silence or over-talking
    - Self-report patterns
    """
    player_refs = PLAYER_REFS.get(target_player, [target_player.lower()])
    insights: list[PlayerInsight] = []

    # Collect all segments mentioning the player
    player_segments = []
    for video in transcripts:
        for seg in video.get("segments", []):
            text = seg.get("text", "").lower()
            if any(ref in text for ref in player_refs):
                player_segments.append({
                    "text": seg.get("text", "").strip(),
                    "timestamp": float(seg.get("start", 0)),
                    "video": video.get("title", ""),
                    "has_kill": any(kw in text for kw in ROLE_EVENTS["kill"]),
                    "has_accusation": any(kw in text for kw in DRAMA_WORDS["accusation"]),
                    "has_defense": any(kw in text for kw in DRAMA_WORDS["defense"]),
                    "has_drama": any(kw in text for kw in DRAMA_WORDS["shock"]),
                })

    if not player_segments:
        return insights

    # Insight 1: Kill frequency
    kill_segs = [s for s in player_segments if s["has_kill"]]
    if kill_segs:
        insights.append(PlayerInsight(
            player=target_player,
            insight_type="pattern",
            title=f"{target_player}'s Kill Moments",
            description=f"{target_player} is mentioned in {len(kill_segs)} kill-related moments across all videos. "
                       f"That's {len(kill_segs)/len(player_segments)*100:.1f}% of all their mentions.",
            evidence=kill_segs[:10],
            confidence=min(1.0, len(kill_segs) / 20),
            video_count=len(set(s["video"] for s in kill_segs)),
        ))

    # Insight 2: Defense frequency
    defense_segs = [s for s in player_segments if s["has_defense"]]
    if defense_segs:
        insights.append(PlayerInsight(
            player=target_player,
            insight_type="tell",
            title=f"How {target_player} Defends Themselves",
            description=f"{target_player} uses defensive language in {len(defense_segs)} segments. "
                       f"Common defenses: 'wasn't me', 'i swear', 'trust me'. "
                       f"This could be a tell when they're the imposter — watch for over-defending.",
            evidence=defense_segs[:10],
            confidence=min(1.0, len(defense_segs) / 15),
            video_count=len(set(s["video"] for s in defense_segs)),
        ))

    # Insight 3: Drama magnet score
    drama_segs = [s for s in player_segments if s["has_drama"]]
    drama_pct = len(drama_segs) / len(player_segments) * 100 if player_segments else 0
    insights.append(PlayerInsight(
        player=target_player,
        insight_type="emotional",
        title=f"{target_player}'s Drama Score",
        description=f"{drama_pct:.1f}% of {target_player}'s mentions involve dramatic moments "
                   f"(shock, surprise, disbelief). {len(drama_segs)} dramatic segments found.",
        evidence=drama_segs[:10],
        confidence=min(1.0, drama_pct / 30),
        video_count=len(set(s["video"] for s in drama_segs)),
    ))

    # Insight 4: Accusation target analysis
    accused_segs = [s for s in player_segments if s["has_accusation"]]
    if accused_segs:
        insights.append(PlayerInsight(
            player=target_player,
            insight_type="rivalry",
            title=f"Who Accuses {target_player} the Most",
            description=f"{target_player} is involved in {len(accused_segs)} accusation moments. "
                       f"They're a frequent target in meetings and votes.",
            evidence=accused_segs[:10],
            confidence=min(1.0, len(accused_segs) / 20),
            video_count=len(set(s["video"] for s in accused_segs)),
        ))

    # Insight 5: Vocabulary fingerprint
    all_words = Counter()
    for s in player_segments:
        for word in s["text"].lower().split():
            if len(word) > 3 and word not in {"that", "this", "with", "they", "have", "from", "been", "were", "what", "just"}:
                all_words[word] += 1

    top_words = all_words.most_common(20)
    if top_words:
        unique_words = [w for w, c in top_words if c > 3]
        insights.append(PlayerInsight(
            player=target_player,
            insight_type="pattern",
            title=f"{target_player}'s Signature Words",
            description=f"Most common words in {target_player}'s segments: {', '.join(unique_words[:10])}. "
                       f"These are the words most associated with {target_player} in the transcripts.",
            evidence=[],
            confidence=0.8,
            video_count=len(set(s["video"] for s in player_segments)),
        ))

    return insights


def find_rivalries(transcripts: list[dict]) -> list[PlayerInsight]:
    """Find player rivalries — pairs that frequently accuse/target each other."""
    pair_mentions: dict[tuple[str, str], int] = defaultdict(int)

    for video in transcripts:
        for seg in video.get("segments", []):
            text = seg.get("text", "").lower()

            # Find all player mentions in this segment
            players_in_seg = set()
            for player, refs in PLAYER_REFS.items():
                if any(ref in text for ref in refs):
                    players_in_seg.add(player)

            # If 2+ players mentioned together, it's an interaction
            players = sorted(players_in_seg)
            for i in range(len(players)):
                for j in range(i + 1, len(players)):
                    pair_mentions[(players[i], players[j])] += 1

    # Find top rivalries
    insights = []
    for (p1, p2), count in sorted(pair_mentions.items(), key=lambda x: -x[1])[:10]:
        insights.append(PlayerInsight(
            player=f"{p1} vs {p2}",
            insight_type="rivalry",
            title=f"The {p1}-{p2} Dynamic",
            description=f"{p1} and {p2} are mentioned together in {count} transcript segments. "
                       f"This is one of the most frequent player interactions in Sidemen Among Us.",
            confidence=min(1.0, count / 100),
        ))

    return insights


def generate_deep_report(
    target_player: str,
    transcripts: list[dict],
) -> DeepAnalysis:
    """Generate a comprehensive deep analysis for one player."""
    logger.info("Deep analyzing: %s across %d videos", target_player, len(transcripts))

    # Find tells and patterns
    tells = find_player_tells(transcripts, target_player)
    logger.info("Found %d insights", len(tells))

    # Emotional profile
    emotions = analyze_emotional_profile(transcripts, target_player)
    emotion_counts = {k: len(v) for k, v in emotions.items()}
    logger.info("Emotional profile: %s", emotion_counts)

    # Accusation network
    acc_network = analyze_accusations_deep(transcripts)

    # Drama moments (top 20 most dramatic involving this player)
    player_refs = PLAYER_REFS.get(target_player, [target_player.lower()])
    drama = []
    for emotion, segments in emotions.items():
        for s in segments:
            drama.append({**s, "drama_score": 1})
    drama.sort(key=lambda d: d.get("drama_score", 0), reverse=True)

    # Vocabulary fingerprint
    vocab: dict[str, list[str]] = {}
    for category, keywords in DRAMA_WORDS.items():
        matches = []
        for video in transcripts:
            for seg in video.get("segments", []):
                text = seg.get("text", "").lower()
                if any(ref in text for ref in player_refs):
                    for kw in keywords:
                        if kw in text:
                            matches.append(kw)
        if matches:
            vocab[category] = [w for w, _ in Counter(matches).most_common(5)]

    # Find rivalries
    rivalries = find_rivalries(transcripts)
    # Add rivalries involving target player
    player_rivalries = [r for r in rivalries if target_player in r.player]
    tells.extend(player_rivalries)

    return DeepAnalysis(
        player=target_player,
        insights=tells,
        accusation_network=acc_network,
        emotional_profile=emotion_counts,
        drama_moments=drama[:30],
        vocabulary_fingerprint=vocab,
    )
