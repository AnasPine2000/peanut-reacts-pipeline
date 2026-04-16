"""Peanut Reacts Pipeline Dashboard - Hugging Face Space Edition.

Displays live channel stats, progression, character profiles, and all links.
Reads status.json (refreshed by the local pipeline) for current data.
"""
import json
from datetime import datetime
from pathlib import Path

import gradio as gr

STATUS_FILE = Path(__file__).parent / "status.json"
PLAN_FILE = Path(__file__).parent / "BUSINESS_PLAN.md"
SHORTS_FILE = Path(__file__).parent / "SHORTS_TIKTOK_STRATEGY.md"
SETUP_FILE = Path(__file__).parent / "CHANNEL_SETUP_GUIDE.md"
LEARNING_FILE = Path(__file__).parent / "learning_uk_clips.json"


def load_learning() -> dict:
    if LEARNING_FILE.exists():
        return json.loads(LEARNING_FILE.read_text(encoding="utf-8"))
    return {}


def load_status():
    if STATUS_FILE.exists():
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    return {
        "stats": {"total": 0, "done": 0, "failed": 0, "running": 0},
        "channels": [], "recent_jobs": [],
        "generated_at": "Never",
        "links": {},
        "revenue_targets": {},
        "characters": {},
    }


def load_markdown(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"# {path.stem}\n\n_File not yet synced._"


def fmt_number(n):
    try:
        n = int(n)
    except (ValueError, TypeError):
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_datetime(iso):
    if not iso or iso == "Never":
        return "Never"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return iso[:16]


# ── OVERVIEW TAB ────────────────────────────────────────────────────

def render_overview():
    status = load_status()
    stats = status.get("stats", {})
    links = status.get("links", {})
    channels = status.get("channels", [])

    # Aggregate totals across all channels
    total_subs = sum(c.get("youtube", {}).get("subscriber_count", 0) for c in channels)
    total_views = sum(c.get("youtube", {}).get("view_count", 0) for c in channels)
    total_uploads = sum(c.get("youtube", {}).get("video_count", 0) for c in channels)
    live_channels = sum(1 for c in channels if c.get("youtube", {}).get("exists"))

    md = f"""## Network Overview

| Metric | Value |
|--------|-------|
| **Live Channels** | {live_channels} / {len(channels)} |
| **Total Subscribers** | {fmt_number(total_subs)} |
| **Total Views** | {fmt_number(total_views)} |
| **Total Videos Uploaded** | {fmt_number(total_uploads)} |
| **Pipeline Jobs Done** | {stats.get('done', 0)} |
| **Pipeline Jobs Failed** | {stats.get('failed', 0)} |
| **Pipeline Jobs Running** | {stats.get('running', 0)} |
| **Last Status Update** | {fmt_datetime(status.get('generated_at', ''))} |

---

## Key Links

"""

    if links.get("livestream"):
        md += f"- **24/7 Livestream:** [Watch now]({links['livestream']})\n"

    for ch in channels:
        yt = ch.get("youtube", {})
        if yt.get("exists") and yt.get("handle_url"):
            md += f"- **{ch['character']['emoji']} {ch['name']}:** [{yt.get('handle') or 'Channel'}]({yt['handle_url']}) — {fmt_number(yt.get('subscriber_count', 0))} subs\n"

    if not any(ch.get("youtube", {}).get("exists") for ch in channels):
        md += "\n_No YouTube channels synced yet. Run the status exporter locally to populate live data._\n"

    return md


# ── CHANNELS TAB ────────────────────────────────────────────────────

def render_channels():
    status = load_status()
    channels = status.get("channels", [])

    if not channels:
        return "_No channels configured. Check channels.yaml and run the status exporter._"

    parts = []
    for ch in channels:
        char = ch.get("character", {})
        yt = ch.get("youtube", {})
        pipeline = ch.get("pipeline", {})

        emoji = char.get("emoji", "❓")
        name = ch.get("name", "Unknown")
        character_name = char.get("name", "?")
        personality = char.get("personality", "")
        catchphrase = char.get("catchphrase", "")
        source_type = ch.get("source_type", "?")
        schedule = ch.get("schedule", "?")

        # Header
        parts.append(f"### {emoji} {name}")
        parts.append(f"**Character:** {character_name} — _{personality}_  ")
        if catchphrase:
            parts.append(f"**Catchphrase:** \"{catchphrase}\"")
        parts.append(f"**Source:** {source_type} | **Schedule:** `{schedule}`")

        # YouTube stats
        if yt.get("exists"):
            parts.append("")
            parts.append("**YouTube (LIVE):**")
            parts.append(f"| Metric | Value |")
            parts.append(f"|--------|-------|")
            parts.append(f"| Handle | [{yt.get('handle', 'channel')}]({yt.get('handle_url', '')}) |")
            parts.append(f"| Subscribers | **{fmt_number(yt.get('subscriber_count', 0))}** |")
            parts.append(f"| Total Views | **{fmt_number(yt.get('view_count', 0))}** |")
            parts.append(f"| Videos | **{yt.get('video_count', 0)}** |")
            if yt.get("country"):
                parts.append(f"| Country | {yt['country']} |")

            # Latest videos
            latest = yt.get("latest_videos", [])
            if latest:
                parts.append("")
                parts.append("**Latest uploads:**")
                for v in latest[:5]:
                    views = fmt_number(v.get("view_count", 0))
                    parts.append(f"- [{v.get('title', '')[:70]}]({v.get('url', '')}) — {views} views")
        else:
            err = yt.get("error")
            if err:
                parts.append(f"_YouTube sync error: {err[:100]}_")
            else:
                parts.append("_No YouTube channel linked yet._")

        # Pipeline stats
        last_run = pipeline.get("last_run_at", "")
        if last_run:
            parts.append(f"\n**Last pipeline run:** {fmt_datetime(last_run)}")

        parts.append("\n---\n")

    return "\n".join(parts)


# ── CHARACTER PROFILES TAB ──────────────────────────────────────────

def render_characters():
    status = load_status()
    chars = status.get("characters", {})
    channels = status.get("channels", [])

    # Map character -> channels that use them
    char_to_channels = {}
    for ch in channels:
        cid = ch.get("character", {}).get("id", "")
        if cid:
            char_to_channels.setdefault(cid, []).append(ch)

    parts = ["## Character Universe\n"]

    for cid, char in chars.items():
        emoji = char.get("emoji", "❓")
        name = char.get("name", cid.title())
        parts.append(f"### {emoji} {name}")
        parts.append(f"_{char.get('personality', '')}_\n")
        parts.append(f"**Origin:** {char.get('origin', '')}  ")
        parts.append(f"**Catchphrase:** \"{char.get('catchphrase', '')}\"  ")
        parts.append(f"**Voice:** `{char.get('voice', '')}`  ")
        parts.append(f"**Color:** `{char.get('color', '')}`")

        # Assigned channels
        assigned = char_to_channels.get(cid, [])
        if assigned:
            parts.append(f"\n**Assigned to:**")
            for ch in assigned:
                yt = ch.get("youtube", {})
                link = yt.get("handle_url", "")
                subs = yt.get("subscriber_count", 0)
                if link:
                    parts.append(f"- [{ch['name']}]({link}) — {fmt_number(subs)} subs")
                else:
                    parts.append(f"- {ch['name']} _(not yet created)_")
        else:
            parts.append(f"\n_Not yet assigned to a channel._")

        parts.append("\n---\n")

    return "\n".join(parts)


# ── PIPELINE JOBS TAB ───────────────────────────────────────────────

def render_jobs():
    status = load_status()
    jobs = status.get("recent_jobs", [])

    if not jobs:
        return "_No pipeline jobs yet. The scheduler will populate this when running._"

    parts = ["## Recent Pipeline Jobs\n"]
    parts.append("| Status | Channel | Video | Step | YouTube | Created |")
    parts.append("|--------|---------|-------|------|---------|---------|")

    status_emoji = {
        "done": "Done", "failed": "Failed", "running": "Running", "queued": "Queued",
    }

    for j in jobs[:25]:
        s = j.get("status", "?")
        emoji = status_emoji.get(s, s)
        ch = j.get("channel_id", "?")
        title = (j.get("title") or j.get("video_id") or "?")[:40]
        step = j.get("step", "?")
        yt_url = j.get("youtube_url", "")
        link = f"[Watch]({yt_url})" if yt_url else "—"
        created = fmt_datetime(j.get("created_at", ""))
        parts.append(f"| {emoji} | {ch} | {title} | {step} | {link} | {created} |")

    return "\n".join(parts)


# ── REVENUE TAB ─────────────────────────────────────────────────────

def render_revenue():
    status = load_status()
    targets = status.get("revenue_targets", {})

    md = """## Revenue Projections (Year 1)

| Month | Target | Cumulative | Driver |
|-------|--------|-----------|--------|
"""
    cumulative = 0
    drivers = {
        "month_0": "Setup phase",
        "month_1": "Building content library",
        "month_2": "Approaching 3,000 watch hours",
        "month_3": "UK CLIPS memberships activate",
        "month_4": "HasanAbi first ad revenue",
        "month_5": "UK CLIPS full ads active",
        "month_6": "Reddit Stories launches (4th channel)",
        "month_7": "HasanAbi scaling fast",
        "month_8": "All channels growing",
        "month_9": "HasanAbi approaching 50K",
        "month_10": "Network effect kicking in",
        "month_11": "Approaching $5K/month",
        "month_12": "YEAR 1 COMPLETE",
    }

    for i in range(13):
        key = f"month_{i}"
        target = targets.get(key, 0)
        cumulative += target
        md += f"| {i} | ${target:,} | ${cumulative:,} | {drivers.get(key, '')} |\n"

    md += f"\n**Year 1 Cumulative Target: ${cumulative:,}**\n"
    md += "\n### Cost: ~$35/month | Break-even: Month 3\n"
    return md


def render_revenue_chart():
    import plotly.graph_objects as go
    status = load_status()
    targets = status.get("revenue_targets", {})

    months = list(range(13))
    monthly = [targets.get(f"month_{i}", 0) for i in months]
    cumulative = []
    running = 0
    for m in monthly:
        running += m
        cumulative.append(running)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[f"M{i}" for i in months],
        y=monthly,
        name="Monthly Target",
        marker_color="#D4A574",
    ))
    fig.add_trace(go.Scatter(
        x=[f"M{i}" for i in months],
        y=cumulative,
        name="Cumulative",
        mode="lines+markers",
        line=dict(color="#548235", width=3),
        yaxis="y2",
    ))
    fig.update_layout(
        title="Year 1 Revenue Projection",
        xaxis_title="Month",
        yaxis=dict(title="Monthly ($)", side="left"),
        yaxis2=dict(title="Cumulative ($)", side="right", overlaying="y"),
        template="plotly_dark",
        height=400,
        legend=dict(x=0.02, y=0.98),
    )
    return fig


# ── PROGRESSION TAB ─────────────────────────────────────────────────

def render_progression_chart():
    import plotly.graph_objects as go
    status = load_status()
    channels = status.get("channels", [])

    labels = []
    subs = []
    colors = []

    for ch in channels:
        yt = ch.get("youtube", {})
        emoji = ch.get("character", {}).get("emoji", "")
        name = ch.get("name", "?")[:20]
        labels.append(f"{emoji} {name}")
        subs.append(yt.get("subscriber_count", 0) if yt.get("exists") else 0)
        colors.append(ch.get("character", {}).get("color", "#888888"))

    if not labels:
        labels = ["No channels"]
        subs = [0]
        colors = ["#888888"]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Subscribers",
        x=labels, y=subs,
        marker_color=colors,
        text=[fmt_number(s) for s in subs],
        textposition="auto",
    ))
    fig.update_layout(
        title="Current Subscriber Counts",
        yaxis_title="Subscribers",
        template="plotly_dark",
        height=400,
        showlegend=False,
    )
    return fig


def render_views_chart():
    import plotly.graph_objects as go
    status = load_status()
    channels = status.get("channels", [])

    fig = go.Figure()
    found_any = False
    for ch in channels:
        yt = ch.get("youtube", {})
        if not yt.get("exists"):
            continue
        latest = yt.get("latest_videos", [])
        if not latest:
            continue
        titles = [v.get("title", "")[:40] for v in latest[:10]]
        views = [v.get("view_count", 0) for v in latest[:10]]
        color = ch.get("character", {}).get("color", "#888888")
        fig.add_trace(go.Bar(
            name=f"{ch.get('character', {}).get('emoji', '')} {ch.get('name', '')[:15]}",
            x=titles, y=views,
            marker_color=color,
        ))
        found_any = True

    if not found_any:
        fig.add_trace(go.Bar(x=["No data yet"], y=[0], marker_color="#888888"))

    fig.update_layout(
        title="Latest Video Performance",
        yaxis_title="Views",
        template="plotly_dark",
        height=450,
        xaxis=dict(tickangle=-45),
    )
    return fig


# ── LEARNING TAB ────────────────────────────────────────────────────

def render_learning_summary() -> str:
    data = load_learning()
    if not data:
        return "_No learning data yet. Run the learning loop to populate._"

    summary = data.get("summary", {})
    analysis = data.get("analysis", {})
    top = analysis.get("top_performers", {})

    md = f"""## Learning Cycle Summary

| Metric | Value |
|--------|-------|
| Channel | `{data.get('channel', '?')}` |
| Generated | {data.get('generated_at', '?')[:16]} |
| Videos analyzed | {summary.get('videos_tracked', 0)} |
| Insights generated | {summary.get('insights_generated', 0)} |
| Recommendations | {summary.get('recommendations_generated', 0)} |

---

## Top Performers
"""
    for i, v in enumerate(top.get("top_videos", [])[:5], 1):
        md += f"{i}. **{v.get('title', '')[:70]}** — {v.get('views', 0):,} views\n"

    md += f"\n- Top 5 average views: **{top.get('avg_top_views', 0):,}**\n"
    md += f"- Top 5 median views: **{top.get('median_top_views', 0):,}**\n"

    return md


def render_insights() -> str:
    data = load_learning()
    insights = data.get("insights", [])
    if not insights:
        return "_No insights yet. Run the learning loop to generate._"

    md = "## LLM-Generated Insights\n\n"
    md += "_Each insight is derived from actual data patterns in your uploads._\n\n"

    for i, ins in enumerate(insights, 1):
        confidence = ins.get("confidence", "?")
        conf_badge = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}.get(
            str(confidence).lower(), confidence
        )
        md += f"### {i}. [{ins.get('category', '').upper()}] — Confidence: {conf_badge}\n\n"
        md += f"**Observation:** {ins.get('insight', '')}\n\n"
        md += f"**Recommended action:** {ins.get('action', '')}\n\n"
        if ins.get("evidence"):
            md += f"_{ins.get('evidence')}_\n\n"
        md += "---\n\n"

    return md


def render_recommendations() -> str:
    data = load_learning()
    recs = data.get("recommendations", [])
    if not recs:
        return "_No recommendations yet._"

    md = "## Recommendations for the Next Video\n\n"
    md += "_Concrete, actionable changes based on what the data says will work._\n\n"

    sorted_recs = sorted(recs, key=lambda r: r.get("priority", 3))

    priority_label = {1: "CRITICAL", 2: "HIGH", 3: "MEDIUM", 4: "LOW", 5: "LOWEST"}

    for rec in sorted_recs:
        p = rec.get("priority", 3)
        if isinstance(p, str):
            try:
                p = int(p)
            except ValueError:
                p = 3
        label = priority_label.get(p, "MEDIUM")
        md += f"### [{label}] {rec.get('category', '').upper()}\n\n"
        md += f"**{rec.get('recommendation', '')}**\n\n"
        md += f"_Why:_ {rec.get('rationale', '')}\n\n"
        md += "---\n\n"

    return md


def render_experiments() -> str:
    """Render experiment tracking results."""
    exp_file = Path(__file__).parent / "experiments.json"
    if not exp_file.exists():
        return (
            "## Experiment Tracking\n\n"
            "_No experiments yet. Experiments are recorded automatically when "
            "the orchestrator applies learned recommendations to new uploads._\n\n"
            "**How it works:**\n"
            "1. Every new video upload checks for pending recommendations\n"
            "2. Those recommendations are applied to the video's parameters (title, duration, tags, etc.)\n"
            "3. The experiment is recorded with baseline channel performance\n"
            "4. After 7 days, the experiment is evaluated against baseline\n"
            "5. Positive deltas confirm the recommendation; negative ones flag it\n\n"
            "Run the pipeline with `apply_learning=True` to start recording experiments."
        )

    data = json.loads(exp_file.read_text(encoding="utf-8"))
    summary = data.get("summary", {})
    experiments = data.get("experiments", [])

    md = "## Experiment Tracking\n\n"
    md += f"| Metric | Value |\n|--------|-------|\n"
    md += f"| Total experiments | {summary.get('total', 0)} |\n"
    md += f"| Measured (has data) | {summary.get('measured', 0)} |\n"
    md += f"| Positive (>10%) | {summary.get('positive', 0)} |\n"
    md += f"| Negative (<-10%) | {summary.get('negative', 0)} |\n"
    md += f"| Average delta | {summary.get('avg_delta_pct', 0):+.1f}% vs baseline |\n\n"

    category_impact = summary.get("category_impact", {})
    if category_impact:
        md += "### Impact by Category\n\n"
        md += "_Average performance delta by recommendation category._\n\n"
        md += "| Category | Avg Delta |\n|----------|-----------|\n"
        for cat, delta in sorted(category_impact.items(), key=lambda x: -x[1]):
            md += f"| {cat} | {delta:+.1f}% |\n"
        md += "\n"

    if experiments:
        md += "### Recent Experiments\n\n"
        md += "| Video | Applied | Current Views | Delta vs Median |\n|---|---|---|---|\n"
        for e in experiments[:10]:
            overrides = e.get("overrides", {})
            notes = overrides.get("notes", [])
            notes_str = ", ".join(notes[:2]) if notes else "—"
            md += f"| {e.get('video_id', '?')[:12]} | {notes_str[:40]} | {e.get('current_views', 0):,} | {e.get('delta_vs_median_pct', 0):+.1f}% |\n"

    return md


def render_analysis_details() -> str:
    data = load_learning()
    analysis = data.get("analysis", {})
    if not analysis:
        return "_No analysis yet._"

    md = "## Statistical Analysis\n\n"

    timing = analysis.get("publish_timing", {})
    if timing:
        md += "### Best Publish Times\n\n"
        if timing.get("best_day"):
            md += f"- **Best day:** {timing['best_day']}\n"
        if timing.get("best_hour") is not None:
            md += f"- **Best hour:** {timing['best_hour']}:00 UTC\n"

        if timing.get("day_averages"):
            md += "\n**Average views by day:**\n\n| Day | Avg Views |\n|-----|-----------|\n"
            sorted_days = sorted(timing["day_averages"].items(), key=lambda x: -x[1])
            for day, views in sorted_days:
                md += f"| {day} | {int(views):,} |\n"

    correlations = analysis.get("correlations", {})
    if correlations:
        md += "\n### Feature Correlations with Views\n\n"
        md += "_Pearson correlation coefficient. Closer to ±1 = stronger relationship._\n\n"
        md += "| Feature | Correlation |\n|---------|-------------|\n"
        for k, r in list(correlations.items())[:10]:
            direction = "positive" if r > 0 else "negative"
            md += f"| {k} | {r:+.3f} ({direction}) |\n"

    top = analysis.get("top_performers", {})
    comparison = top.get("feature_comparison", {})
    if comparison:
        md += "\n### Top 5 vs Bottom 5 Feature Comparison\n\n"
        md += "| Feature | Top | Bottom | Delta |\n|---------|-----|--------|-------|\n"
        for k, v in comparison.items():
            if "bottom" in v:
                delta = v.get("delta_pct", 0)
                arrow = "+" if delta >= 0 else ""
                md += f"| {k} | {v['top']} | {v['bottom']} | {arrow}{delta:.0f}% |\n"

    return md


def render_learning_chart():
    import plotly.graph_objects as go
    data = load_learning()
    analysis = data.get("analysis", {})
    top = analysis.get("top_performers", {})
    top_videos = top.get("top_videos", [])

    if not top_videos:
        fig = go.Figure()
        fig.add_annotation(text="No data yet", xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
        fig.update_layout(template="plotly_dark", height=400)
        return fig

    titles = [v.get("title", "")[:50] for v in top_videos[:10]]
    views = [v.get("views", 0) for v in top_videos[:10]]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=titles, y=views,
        marker_color="#D4A574",
        text=[f"{v:,}" for v in views],
        textposition="auto",
    ))
    fig.update_layout(
        title="Top Performing Videos",
        yaxis_title="Views",
        template="plotly_dark",
        height=500,
        xaxis=dict(tickangle=-45),
    )
    return fig


# ── BUILD THE APP ───────────────────────────────────────────────────

with gr.Blocks(title="Peanut Reacts Pipeline", theme=gr.themes.Soft()) as app:

    gr.Markdown("# Peanut Reacts — Autonomous Pipeline Dashboard")
    gr.Markdown("_Multi-channel YouTube content pipeline. Fully automated, AI-powered._")

    with gr.Tabs():
        with gr.Tab("Overview"):
            overview_md = gr.Markdown(render_overview)
            gr.Button("Refresh", size="sm").click(fn=render_overview, outputs=overview_md)

        with gr.Tab("Channels"):
            channels_md = gr.Markdown(render_channels)
            gr.Button("Refresh", size="sm").click(fn=render_channels, outputs=channels_md)

        with gr.Tab("Characters"):
            chars_md = gr.Markdown(render_characters)
            gr.Button("Refresh", size="sm").click(fn=render_characters, outputs=chars_md)

        with gr.Tab("Pipeline Jobs"):
            jobs_md = gr.Markdown(render_jobs)
            gr.Button("Refresh", size="sm").click(fn=render_jobs, outputs=jobs_md)

        with gr.Tab("Progression"):
            gr.Markdown("## Current Channel Stats")
            prog_plot = gr.Plot(value=render_progression_chart())
            gr.Markdown("## Latest Video Performance")
            views_plot = gr.Plot(value=render_views_chart())
            refresh_btn = gr.Button("Refresh", size="sm")
            refresh_btn.click(fn=render_progression_chart, outputs=prog_plot)
            refresh_btn.click(fn=render_views_chart, outputs=views_plot)

        with gr.Tab("Revenue"):
            rev_plot = gr.Plot(value=render_revenue_chart())
            rev_md = gr.Markdown(render_revenue)

        with gr.Tab("Learning"):
            gr.Markdown("# Feedback Loop — Learning from Past Videos")
            gr.Markdown(
                "_The pipeline analyzes every video you've uploaded and uses "
                "LLM-driven pattern recognition to suggest how to improve the next one. "
                "Exponential improvement by compounding learnings._"
            )
            with gr.Tabs():
                with gr.Tab("Summary"):
                    gr.Markdown(render_learning_summary)
                    learn_plot = gr.Plot(value=render_learning_chart())
                with gr.Tab("Insights"):
                    gr.Markdown(render_insights)
                with gr.Tab("Recommendations"):
                    gr.Markdown(render_recommendations)
                with gr.Tab("Experiments"):
                    gr.Markdown(render_experiments)
                with gr.Tab("Analysis"):
                    gr.Markdown(render_analysis_details)

        with gr.Tab("Business Plan"):
            gr.Markdown(load_markdown(PLAN_FILE))

        with gr.Tab("Shorts Strategy"):
            gr.Markdown(load_markdown(SHORTS_FILE))

        with gr.Tab("Channel Setup"):
            gr.Markdown(load_markdown(SETUP_FILE))

    gr.Markdown("---")
    gr.Markdown(
        "_Dashboard powered by Gradio on Hugging Face Spaces. "
        "Data refreshed by local pipeline every 5 minutes. "
        "Click any link to open in a new tab._"
    )


if __name__ == "__main__":
    app.launch(share=False, ssr_mode=False)
