#!/usr/bin/env python3
"""CLI entry point for generating a peanut reaction script via LLM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from peanut_reacts.core.logging_setup import build_logger
from peanut_reacts.analysis.comments import CommentHighlight, highlights_from_dicts
from peanut_reacts.character.reaction_generator import (
    LLMConfig,
    ReactionScriptGenerator,
    create_llm_provider,
    script_to_dict,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate peanut reaction dialogue from a transcript using an LLM.",
    )
    p.add_argument("transcript", help="Path to transcript JSON (or SRT file)")
    p.add_argument("--comments", default=None, help="Path to highlights.json (from peanut-extract-comments)")
    p.add_argument("--title", default="", help="Video title for context")
    p.add_argument("--duration", type=float, default=0.0, help="Video duration in seconds")
    p.add_argument("--max-reactions", type=int, default=15, help="Max reactions to generate")

    # LLM config
    p.add_argument("--provider", default="deepseek", choices=["deepseek", "groq", "ollama"])
    p.add_argument("--model", default="", help="Model name (provider-specific default if empty)")
    p.add_argument("--api-key", default="", help="API key (or set DEEPSEEK_API_KEY / GROQ_API_KEY env var)")
    p.add_argument("--base-url", default="", help="Custom API base URL")
    p.add_argument("--temperature", type=float, default=0.8)

    p.add_argument("-o", "--output", default="reaction_script.json", help="Output script JSON")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    log = build_logger("generate_reactions", args.verbose)

    # Load transcript
    transcript_path = Path(args.transcript).expanduser().resolve()
    if not transcript_path.exists():
        log.error("Transcript not found: %s", transcript_path)
        return 2

    if transcript_path.suffix == ".srt":
        from peanut_reacts.core.srt import parse_srt
        transcript = parse_srt(transcript_path)
    else:
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))

    # Load highlights
    highlights: list[CommentHighlight] = []
    if args.comments:
        comments_path = Path(args.comments).expanduser().resolve()
        if comments_path.exists():
            data = json.loads(comments_path.read_text(encoding="utf-8"))
            highlights = highlights_from_dicts(data)
            log.info("Loaded %d highlight(s) from %s", len(highlights), comments_path)

    # Create LLM provider
    config = LLMConfig(
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        temperature=args.temperature,
    )

    try:
        llm = create_llm_provider(config)
    except ValueError as e:
        log.error("%s", e)
        return 1

    # Generate
    generator = ReactionScriptGenerator(llm, log)
    script = generator.generate(
        transcript, highlights,
        video_title=args.title,
        video_duration=args.duration,
        max_reactions=args.max_reactions,
    )

    # Save
    output_path = Path(args.output).expanduser().resolve()
    output_path.write_text(json.dumps(script_to_dict(script), indent=2), encoding="utf-8")
    log.info("Saved reaction script (%d lines) to %s", len(script.lines), output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
