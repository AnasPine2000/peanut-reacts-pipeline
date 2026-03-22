"""
Transcript topic segmentation.

Segments a video transcript into topics using sentence embeddings and
cosine similarity. Also includes keyword-from-filename extraction and
topic-to-keyword matching for targeted clip extraction.

Refactored from archive/segment_video.py and archive/segment.py.
"""

from __future__ import annotations

import json
import logging
import os
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from peanut_reacts.core.ffmpeg import concatenate, cut_segment
from peanut_reacts.core.text_match import fuzzy_match
from peanut_reacts.core.transcription import transcribe_video

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Segmentation algorithms
# ---------------------------------------------------------------------------

def segment_by_embeddings(
    transcript: list[dict],
    threshold: float = 0.7,
) -> list[list[dict]]:
    """Segment transcript into topics using sentence-transformer embeddings.

    When cosine similarity between consecutive segments falls below
    *threshold*, a new topic boundary is created.
    """
    from sentence_transformers import SentenceTransformer

    logger.info("Segmenting transcript into topics (threshold=%.2f) ...", threshold)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    texts = [seg["text"].strip() for seg in transcript]
    embeddings = model.encode(texts)

    topics: list[list[dict]] = []
    current: list[dict] = [transcript[0]]

    for i in range(1, len(transcript)):
        sim = cosine_similarity(
            embeddings[i - 1].reshape(1, -1),
            embeddings[i].reshape(1, -1),
        )[0][0]
        if sim < threshold:
            topics.append(current)
            current = []
        current.append(transcript[i])

    if current:
        topics.append(current)

    logger.info("Detected %d topic segment(s).", len(topics))
    return topics


def segment_by_duration(
    transcript: list[dict],
    min_duration: float = 900,
) -> list[list[dict]]:
    """Simple duration-based segmentation (fallback when no NLP model available).

    Splits transcript into chunks of at least *min_duration* seconds.
    """
    topics: list[list[dict]] = []
    current: list[dict] = []
    current_dur = 0.0

    for seg in transcript:
        current.append(seg)
        current_dur += seg["end"] - seg["start"]
        if current_dur >= min_duration:
            topics.append(current)
            current = []
            current_dur = 0.0

    if current:
        topics.append(current)

    return topics


# ---------------------------------------------------------------------------
# Keyword extraction from filenames
# ---------------------------------------------------------------------------

def extract_keywords_from_filename(video_path: Path, separator: str = "!") -> list[str]:
    """Extract keywords from a video filename using *separator*.

    Example: ``"TOPIC ONE! TOPIC TWO! TOPIC THREE.mp4"``
    yields ``["TOPIC ONE", "TOPIC TWO", "TOPIC THREE"]``
    """
    base = video_path.stem
    return [kw.strip() for kw in base.split(separator) if kw.strip()]


# ---------------------------------------------------------------------------
# Topic-keyword matching
# ---------------------------------------------------------------------------

def find_topics_for_keyword(
    topics: list[list[dict]],
    keyword: str,
    threshold: float = 0.3,
) -> list[list[dict]]:
    """Find topic segments whose combined text matches *keyword*."""
    matches: list[list[dict]] = []

    for topic in topics:
        topic_text = " ".join(seg["text"] for seg in topic).lower()
        kw_lower = keyword.lower()

        # Partial match: any significant word in keyword appears in topic
        words = [w for w in kw_lower.split() if len(w) > 2]
        partial = any(w in topic_text for w in words)

        # Fuzzy match
        fuzzy = fuzzy_match(kw_lower, topic_text, threshold=threshold)

        if partial or fuzzy:
            matches.append(topic)

    if matches:
        logger.info("Keyword '%s': %d matching topic(s).", keyword, len(matches))
    else:
        logger.warning("Keyword '%s': no matching topics.", keyword)

    return matches


# ---------------------------------------------------------------------------
# High-level: segment video by filename keywords
# ---------------------------------------------------------------------------

def segment_video_by_keywords(
    video_path: Path,
    output_dir: Path,
    *,
    similarity_threshold: float = 0.7,
    model_name: str = "base",
    device: str = "cuda",
) -> list[Path]:
    """Full pipeline: transcribe → segment → match keywords → cut clips.

    Keywords are extracted from the video filename (split on '!').
    Returns a list of output video paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    transcript = transcribe_video(video_path, model_name=model_name, device=device)
    topics = segment_by_embeddings(transcript, threshold=similarity_threshold)
    keywords = extract_keywords_from_filename(video_path)

    if not keywords:
        logger.error("No keywords extracted from filename: %s", video_path.name)
        return []

    logger.info("Keywords: %s", keywords)
    output_files: list[Path] = []

    for kw in keywords:
        matching_topics = find_topics_for_keyword(topics, kw)
        if not matching_topics:
            continue

        # Flatten matching topics into sorted segments
        flat = sorted(
            (seg for topic in matching_topics for seg in topic),
            key=lambda s: s["start"],
        )

        safe_kw = kw.replace(" ", "_").replace("/", "-")

        # Cut each segment
        temp_files: list[Path] = []
        clip_transcript: list[dict] = []

        for i, seg in enumerate(flat, 1):
            temp = output_dir / f"temp_{safe_kw}_part{i}.mp4"
            cut_segment(video_path, seg["start"], seg["end"], temp)
            temp_files.append(temp)
            clip_transcript.append({"start": seg["start"], "end": seg["end"], "text": seg["text"]})

        # Concatenate
        final_video = output_dir / f"final_segment_{safe_kw}.mp4"
        concatenate(temp_files, final_video)
        output_files.append(final_video)

        # Save transcript
        transcript_file = output_dir / f"final_segment_{safe_kw}_transcript.json"
        transcript_file.write_text(json.dumps(clip_transcript, indent=2), encoding="utf-8")

        # Clean temp files
        for t in temp_files:
            t.unlink(missing_ok=True)

    return output_files
