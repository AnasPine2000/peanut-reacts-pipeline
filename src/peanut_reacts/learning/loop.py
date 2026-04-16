"""The learning loop — orchestrates the full feedback cycle.

Runs periodically (e.g., daily) to:
1. Fetch latest metrics for all our videos
2. Extract features from each video
3. Record snapshots in the learning DB
4. Analyze patterns across the dataset
5. Generate insights via LLM
6. Generate recommendations for the next video
7. (Optional) Fetch + analyze new comments for sentiment
8. Save everything to learning.db
9. Export to dashboard/learning.json for the Gradio dashboard

Runs standalone or triggered by the scheduler.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def run_learning_cycle(
    channel_config,
    global_settings,
    learning_db_path: str = "learning.db",
    youtube_service=None,
    llm_provider=None,
    analyze_comments: bool = False,
    dashboard_out: Optional[Path] = None,
) -> dict:
    """Run one full learning cycle for a channel.

    Returns summary: {videos_tracked, insights_generated, recommendations_generated, error}
    """
    from .database import LearningDB
    from .analytics import fetch_our_recent_videos, fetch_video_metrics, compute_engagement_rate
    from .features import extract_all_features
    from .analyzer import full_analysis
    from .insights import generate_insights
    from .recommender import generate_recommendations
    from .sentiment import analyze_comments as analyze_comments_fn

    summary = {
        "channel": channel_config.id,
        "started_at": datetime.utcnow().isoformat(),
        "videos_tracked": 0,
        "videos_updated": 0,
        "insights_generated": 0,
        "recommendations_generated": 0,
        "sentiment_analyzed": 0,
        "errors": [],
    }

    db = LearningDB(learning_db_path)

    # ── Setup services if not provided ───────────────────────────
    if youtube_service is None:
        try:
            from peanut_reacts.upload.youtube_auth import get_authenticated_service
            token_path = Path(channel_config.oauth_token).expanduser() if channel_config.oauth_token else None
            youtube_service = get_authenticated_service(
                Path(channel_config.client_secrets),
                token_path=token_path,
            )
        except Exception as e:
            summary["errors"].append(f"YouTube auth failed: {e}")
            db.close()
            return summary

    if llm_provider is None:
        try:
            from peanut_reacts.character.reaction_generator import create_llm_provider, LLMConfig
            from peanut_reacts.core.config import load_config
            cfg = load_config()
            llm_provider = create_llm_provider(LLMConfig(
                provider=global_settings.llm_provider,
                api_key=cfg.deepseek_api_key,
                model=global_settings.llm_model,
                temperature=0.3,
            ))
        except Exception as e:
            summary["errors"].append(f"LLM setup failed: {e}")

    # ── Step 1: Fetch videos ──────────────────────────────────────
    log.info("[%s] Fetching recent videos...", channel_config.id)
    videos = fetch_our_recent_videos(youtube_service, max_results=100)
    summary["videos_tracked"] = len(videos)

    if not videos:
        summary["errors"].append("No videos fetched")
        db.close()
        return summary

    # ── Step 2: Extract features + record snapshots ──────────────
    for v in videos:
        try:
            # Extract features (no reactions data here - that would come from our pipeline cache)
            features = extract_all_features(
                video_data=v,
                reactions=None,
                source_info={
                    "source_type": channel_config.source_type,
                    "character": channel_config.character,
                },
            )
            features_dict = {k: v for k, v in features.__dict__.items()}

            db.upsert_video(
                video_id=v["video_id"],
                channel_id=channel_config.id,
                title=v.get("title", ""),
                published_at=v.get("published_at", ""),
                features=features_dict,
            )
            db.record_snapshot(v["video_id"], v)
            summary["videos_updated"] += 1
        except Exception as e:
            summary["errors"].append(f"Feature extract {v.get('video_id')}: {e}")

    log.info("[%s] Tracked %d videos", channel_config.id, summary["videos_updated"])

    # ── Step 3: Full analysis ────────────────────────────────────
    videos_with_metrics = db.get_all_with_metrics(channel_config.id)
    analysis = full_analysis(videos_with_metrics)

    # ── Step 4: Generate insights ────────────────────────────────
    if llm_provider:
        insights = generate_insights(analysis, llm_provider, channel_name=channel_config.name)
        for ins in insights:
            db.save_insight(
                channel_id=channel_config.id,
                category=ins.get("category", ""),
                insight=ins.get("insight", ""),
                confidence=_confidence_to_float(ins.get("confidence", "low")),
                supporting_ids=[],
            )
        summary["insights_generated"] = len(insights)

        # ── Step 5: Generate recommendations ─────────────────────
        recs = generate_recommendations(insights, llm_provider, channel_name=channel_config.name)
        for rec in recs:
            db.save_recommendation(
                channel_id=channel_config.id,
                category=rec.get("category", ""),
                recommendation=rec.get("recommendation", ""),
                rationale=rec.get("rationale", ""),
                priority=_priority_to_int(rec.get("priority", 3)),
            )
        summary["recommendations_generated"] = len(recs)
    else:
        insights = []
        recs = []

    # ── Step 5b: Competitor analysis (feedforward loop) ────────
    if llm_provider:
        try:
            from .competitor_tracker import run_competitor_analysis
            comp_result = run_competitor_analysis(
                channel_id=channel_config.id,
                llm_provider=llm_provider,
                learning_db_path=learning_db_path,
            )
            comp_insights = comp_result.get("insights", [])
            summary["competitor_insights"] = len(comp_insights)
            summary["competitors_found"] = comp_result.get("competitors_found", 0)
            log.info("[%s] Competitor analysis: %d competitors, %d insights",
                     channel_config.id, summary["competitors_found"], len(comp_insights))
        except Exception as e:
            log.warning("Competitor analysis failed: %s", e)
            summary["competitor_insights"] = 0

    # ── Step 6: Comment sentiment (optional, uses API quota) ─────
    if analyze_comments and llm_provider:
        # Top 5 performers get sentiment analysis
        top_performers = sorted(videos, key=lambda v: v.get("view_count", 0), reverse=True)[:5]
        for v in top_performers:
            try:
                from peanut_reacts.download.youtube_comments import YouTubeCommentFetcher
                from peanut_reacts.core.config import load_config
                cfg = load_config()
                fetcher = YouTubeCommentFetcher(api_key=cfg.youtube_api_key)
                comments = fetcher.fetch_comments(v["video_id"], max_results=100)
                comment_dicts = [
                    {"text": c.text, "like_count": c.like_count}
                    for c in comments
                ]
                sentiment = analyze_comments_fn(comment_dicts, llm_provider)
                db.save_sentiment(v["video_id"], sentiment)
                summary["sentiment_analyzed"] += 1
            except Exception as e:
                summary["errors"].append(f"Sentiment {v.get('video_id')}: {e}")

    summary["finished_at"] = datetime.utcnow().isoformat()

    # ── Step 7: Export to dashboard ──────────────────────────────
    if dashboard_out:
        dashboard_out.parent.mkdir(parents=True, exist_ok=True)
        export = {
            "channel": channel_config.id,
            "generated_at": summary["finished_at"],
            "summary": summary,
            "analysis": analysis,
            "insights": insights,
            "recommendations": recs,
            "top_performers": analysis.get("top_performers", {}).get("top_videos", []),
        }
        dashboard_out.write_text(json.dumps(export, indent=2, default=str), encoding="utf-8")
        log.info("Learning data exported to %s", dashboard_out)

    db.close()
    return summary


def _confidence_to_float(c) -> float:
    if isinstance(c, (int, float)):
        return float(c)
    mapping = {"high": 0.9, "medium": 0.6, "low": 0.3}
    return mapping.get(str(c).lower(), 0.5)


def _priority_to_int(p) -> int:
    if isinstance(p, int):
        return p
    if isinstance(p, str):
        mapping = {"critical": 1, "high": 2, "medium": 3, "low": 4, "lowest": 5}
        try:
            return int(p)
        except ValueError:
            return mapping.get(p.lower(), 3)
    return 3


def run_learning_for_all_channels(
    pipeline_config,
    global_settings,
    learning_db_path: str = "learning.db",
    dashboard_dir: Path = None,
) -> dict:
    """Run the learning cycle for every configured channel."""
    results = {}
    for ch in pipeline_config.channels:
        if not ch.oauth_token or not Path(ch.oauth_token).expanduser().exists():
            log.debug("Skipping %s - no auth", ch.id)
            continue

        out = (dashboard_dir / f"learning_{ch.id}.json") if dashboard_dir else None
        try:
            results[ch.id] = run_learning_cycle(
                ch, global_settings,
                learning_db_path=learning_db_path,
                dashboard_out=out,
            )
        except Exception as e:
            log.error("Learning cycle for %s failed: %s", ch.id, e)
            results[ch.id] = {"error": str(e)}

    return results
