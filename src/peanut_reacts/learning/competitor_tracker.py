"""Competitor tracker — discover and learn from similar successful channels.

Two modes:
A) DISCOVER: Find channels similar to ours via YouTube search
B) ANALYZE: Track their posting patterns, titles, thumbnails, view counts
C) IMITATE: Extract strategies and feed them into our recommendation engine

Runs daily as part of the learning loop.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# Competitor search queries per niche
NICHE_SEARCHES = {
    "uk_clips": [
        "sidemen among us compilation",
        "sidemen among us reaction",
        "sidemen clips compilation 2026",
        "peanut reacts sidemen",
    ],
    "hasanabi_archive": [
        "hasanabi clips",
        "hasanabi best moments",
        "hasan piker stream highlights",
        "hasanabi archive",
    ],
    "peanut_tiktok_reacts": [
        "reacting to viral tiktoks",
        "tiktok reaction compilation",
        "funniest tiktoks reaction",
        "tiktok reaction shorts",
    ],
}

# Known competitor channels to always track
KNOWN_COMPETITORS = {
    "uk_clips": [
        {"name": "AllThingsSidemen", "url": "https://www.youtube.com/@AllThingsSidemen"},
        {"name": "SidemenReacts", "url": "https://www.youtube.com/@SidemenReacts"},
        {"name": "MoreSidemen", "url": "https://www.youtube.com/@MoreSidemen"},
    ],
    "hasanabi_archive": [
        {"name": "HasanAbi Archive", "url": "https://www.youtube.com/@HasanAbiArchivefan0"},
        {"name": "HasanAbi Archive fan3", "url": "https://www.youtube.com/@HasanAbiArchivefan3"},
        {"name": "Hasanabi Productions", "url": "https://www.youtube.com/@hasanabiproductions"},
    ],
    "peanut_tiktok_reacts": [
        {"name": "CinnamonToastKen", "url": "https://www.youtube.com/@CinnamonToastKen"},
        {"name": "SSSniperwolf", "url": "https://www.youtube.com/@SSSniperWolf"},
    ],
}


def discover_competitors(channel_id: str, max_results: int = 10) -> list[dict]:
    """Find competitor channels via YouTube search."""
    queries = NICHE_SEARCHES.get(channel_id, ["youtube reaction compilation"])
    found = {}

    for query in queries:
        try:
            result = subprocess.run(
                ["yt-dlp", f"ytsearch{max_results}:{query}",
                 "--flat-playlist",
                 "--print", "%(channel_id)s\t%(channel)s\t%(view_count)s\t%(title)s"],
                capture_output=True, text=True, timeout=60,
            )
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                ch_id = parts[0]
                ch_name = parts[1]
                views = int(parts[2]) if parts[2] != "NA" else 0
                title = parts[3] if len(parts) > 3 else ""

                if ch_id and ch_id not in found:
                    found[ch_id] = {
                        "channel_id": ch_id,
                        "channel_name": ch_name,
                        "sample_views": views,
                        "sample_title": title[:80],
                        "discovered_via": query,
                    }
        except Exception as e:
            log.warning("Search '%s' failed: %s", query, e)

    # Sort by views
    competitors = sorted(found.values(), key=lambda x: x["sample_views"], reverse=True)
    log.info("Discovered %d competitor channels for [%s]", len(competitors), channel_id)
    return competitors[:max_results]


def analyze_competitor_channel(channel_url: str, max_videos: int = 20) -> dict:
    """Analyze a competitor channel's recent uploads for patterns."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist",
             "--print", "%(id)s\t%(title)s\t%(view_count)s\t%(duration)s\t%(upload_date)s",
             "--playlist-end", str(max_videos),
             f"{channel_url}/videos"],
            capture_output=True, text=True, timeout=60,
        )

        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            videos.append({
                "id": parts[0],
                "title": parts[1],
                "views": int(parts[2]) if parts[2] != "NA" else 0,
                "duration": float(parts[3]) if len(parts) > 3 and parts[3] != "NA" else 0,
                "upload_date": parts[4] if len(parts) > 4 else "",
            })

        if not videos:
            return {"error": "No videos found"}

        # Analyze patterns
        views_list = [v["views"] for v in videos if v["views"] > 0]
        durations = [v["duration"] for v in videos if v["duration"] > 0]
        titles = [v["title"] for v in videos]

        import statistics
        analysis = {
            "video_count": len(videos),
            "avg_views": int(statistics.mean(views_list)) if views_list else 0,
            "median_views": int(statistics.median(views_list)) if views_list else 0,
            "max_views": max(views_list) if views_list else 0,
            "avg_duration_min": round(statistics.mean(durations) / 60, 1) if durations else 0,
            "upload_frequency": _estimate_frequency(videos),
            "top_videos": sorted(videos, key=lambda v: v["views"], reverse=True)[:5],
            "title_patterns": _analyze_titles(titles),
        }
        return analysis

    except Exception as e:
        log.error("Competitor analysis failed: %s", e)
        return {"error": str(e)}


def _estimate_frequency(videos: list[dict]) -> str:
    """Estimate upload frequency from dates."""
    dates = [v["upload_date"] for v in videos if v.get("upload_date") and v["upload_date"] != "NA"]
    if len(dates) < 2:
        return "unknown"

    try:
        parsed = sorted([datetime.strptime(d, "%Y%m%d") for d in dates], reverse=True)
        gaps = [(parsed[i] - parsed[i + 1]).days for i in range(min(5, len(parsed) - 1))]
        avg_gap = sum(gaps) / len(gaps)
        if avg_gap <= 1.5:
            return "daily"
        elif avg_gap <= 3.5:
            return "every 2-3 days"
        elif avg_gap <= 7.5:
            return "weekly"
        else:
            return f"every {int(avg_gap)} days"
    except Exception:
        return "unknown"


def _analyze_titles(titles: list[str]) -> dict:
    """Extract title patterns from competitor videos."""
    patterns = {
        "all_caps_ratio": sum(1 for t in titles if t == t.upper()) / len(titles) if titles else 0,
        "avg_length": sum(len(t) for t in titles) / len(titles) if titles else 0,
        "has_emoji": sum(1 for t in titles if any(ord(c) > 127 for c in t)) / len(titles) if titles else 0,
        "has_numbers": sum(1 for t in titles if re.search(r"\d+", t)) / len(titles) if titles else 0,
        "common_words": _top_words(titles, 10),
    }
    return patterns


def _top_words(titles: list[str], n: int = 10) -> list[str]:
    """Find most common words across titles."""
    from collections import Counter
    words = Counter()
    stop = {"the", "a", "an", "to", "of", "in", "and", "is", "it", "for", "on", "at", "by", "or", "but"}
    for t in titles:
        for w in re.findall(r"[a-zA-Z]+", t.lower()):
            if w not in stop and len(w) > 2:
                words[w] += 1
    return [w for w, _ in words.most_common(n)]


def generate_competitor_insights(
    our_channel_id: str,
    competitor_analyses: list[dict],
    our_analysis: dict,
    llm_provider,
) -> list[dict]:
    """Use LLM to compare our channel vs competitors and suggest improvements."""
    if not competitor_analyses or not llm_provider:
        return []

    # Build comparison summary
    comp_summary = []
    for i, comp in enumerate(competitor_analyses[:5]):
        if comp.get("error"):
            continue
        comp_summary.append(
            f"Competitor {i+1}: avg {comp['avg_views']:,} views, "
            f"posts {comp['upload_frequency']}, "
            f"avg {comp['avg_duration_min']} min, "
            f"title caps: {comp['title_patterns']['all_caps_ratio']*100:.0f}%, "
            f"top words: {', '.join(comp['title_patterns']['common_words'][:5])}"
        )

    our_avg = our_analysis.get("avg_views", 0)
    our_summary = f"Our channel: avg {our_avg:,} views"

    prompt = (
        f"You are a YouTube growth strategist. Compare our channel vs competitors and suggest 3-5 specific improvements.\n\n"
        f"Our channel [{our_channel_id}]: {our_summary}\n\n"
        f"Competitors:\n" + "\n".join(comp_summary) + "\n\n"
        f"For each insight:\n"
        f'- What competitors do differently\n'
        f'- Specific action we should take\n'
        f'- Expected impact\n\n'
        f'Return JSON array: [{{"insight": "...", "action": "...", "priority": 1-5, "source": "competitor_analysis"}}]'
    )

    try:
        response = llm_provider.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=800,
        )
        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text)
    except Exception as e:
        log.error("Competitor insight generation failed: %s", e)
        return []


def run_competitor_analysis(
    channel_id: str,
    llm_provider=None,
    learning_db_path: str = "learning.db",
) -> dict:
    """Full competitor analysis cycle for one channel.

    Returns {competitors_found, analyses, insights}.
    """
    result = {
        "channel_id": channel_id,
        "timestamp": datetime.utcnow().isoformat(),
        "competitors_found": 0,
        "analyses": [],
        "insights": [],
    }

    # Discover competitors
    competitors = discover_competitors(channel_id, max_results=8)
    result["competitors_found"] = len(competitors)

    # Add known competitors
    known = KNOWN_COMPETITORS.get(channel_id, [])
    for k in known:
        if not any(c["channel_name"] == k["name"] for c in competitors):
            competitors.append({
                "channel_id": "",
                "channel_name": k["name"],
                "channel_url": k["url"],
                "sample_views": 0,
                "discovered_via": "known",
            })

    # Analyze top 5 competitors
    analyses = []
    for comp in competitors[:5]:
        url = comp.get("channel_url") or f"https://www.youtube.com/channel/{comp['channel_id']}"
        log.info("Analyzing competitor: %s", comp["channel_name"])
        analysis = analyze_competitor_channel(url, max_videos=15)
        analysis["channel_name"] = comp["channel_name"]
        analyses.append(analysis)

    result["analyses"] = analyses

    # Generate insights
    if llm_provider and analyses:
        insights = generate_competitor_insights(
            channel_id, analyses, {}, llm_provider
        )
        result["insights"] = insights

        # Save insights to learning DB
        if insights:
            try:
                from .database import LearningDB
                db = LearningDB(learning_db_path)
                for ins in insights:
                    db.save_insight(
                        channel_id=channel_id,
                        category="competitor",
                        insight=ins.get("insight", "") + " | Action: " + ins.get("action", ""),
                        confidence=0.7,
                        supporting_ids=[],
                    )
                    db.save_recommendation(
                        channel_id=channel_id,
                        category="competitor",
                        recommendation=ins.get("action", ""),
                        rationale=ins.get("insight", ""),
                        priority=ins.get("priority", 3),
                    )
                db.close()
                log.info("Saved %d competitor insights for [%s]", len(insights), channel_id)
            except Exception as e:
                log.warning("Failed to save competitor insights: %s", e)

    return result
