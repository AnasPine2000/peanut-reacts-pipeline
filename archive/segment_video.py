#!/usr/bin/env python3
"""
segment_video.py

This script:
  1. If the input video is long (e.g., > 1 hour), splits it into smaller chunks using ffmpeg.
  2. Transcribes each video chunk using Whisper (with GPU acceleration) and saves transcript JSON files.
  3. Combines the individual transcripts (adjusting time codes) into one full transcript.
     If the full transcript already exists, transcription is skipped.
  4. Segments the full transcript into topics using sentence embeddings.
  5. Extracts keywords from the original video file name (using '!' as separator) and, for each keyword,
     searches the transcript topics (using partial and fuzzy matching) to find the relevant segments.
  6. Flattens all matching segments for each keyword, cuts those segments from the original full video,
     concatenates them into one final video file, and saves a transcript for the concatenated segment.
  7. Deletes all intermediate chunk files and chunk transcript JSON files, leaving only the full transcript,
     each concatenated segment’s transcript, and the concatenated video segments in a separate folder.

Usage:
    python segment_video.py <video_file>
    
Requirements:
  - ffmpeg and ffprobe must be installed and in your PATH.
  - Python packages: openai-whisper, sentence-transformers, numpy, scikit-learn
    Install via:
      pip install --upgrade openai-whisper sentence-transformers numpy scikit-learn
"""

import os
import sys
import subprocess
import whisper
from sentence_transformers import SentenceTransformer
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import json
import logging
from difflib import SequenceMatcher

# Configure logging for debugging
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

# ------------------------------
# Helper functions for video info
# ------------------------------

def get_video_duration(video_path):
    """Return the duration (in seconds) of the video using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    output = subprocess.check_output(cmd).decode().strip()
    return float(output)

def split_video_ffmpeg(video_path, chunk_duration=900):
    """
    Split the video into chunks of chunk_duration seconds using ffmpeg.
    Returns a list of chunk file paths.
    """
    base_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    # Pattern: <originalname>_chunk_000.mp4, etc.
    split_pattern = os.path.join(base_dir, f"{base_name}_chunk_%03d.mp4")
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-c", "copy",
        "-map", "0",
        "-segment_time", str(chunk_duration),
        "-f", "segment",
        split_pattern
    ]
    logging.info("Splitting video into chunks for transcription...")
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Collect the created chunk files (assumes sequential numbering).
    chunk_files = sorted([
        os.path.join(base_dir, f)
        for f in os.listdir(base_dir)
        if f.startswith(f"{base_name}_chunk_") and f.endswith(".mp4")
    ])
    logging.info(f"Created {len(chunk_files)} chunk(s) for transcription.")
    return chunk_files

# ------------------------------
# Transcription functions
# ------------------------------

def extract_transcript(video_path):
    """
    Use Whisper to transcribe the video.
    Returns a list of segments, each a dict with keys "start", "end", "text", etc.
    """
    logging.info(f"[Transcription] Loading Whisper model on GPU...")
    model = whisper.load_model("base", device="cuda")  # Options: tiny, base, small, medium, large
    logging.info(f"[Transcription] Transcribing {video_path} ... (this may take a while)")
    result = model.transcribe(video_path)
    segments = result.get("segments", [])
    if not segments:
        logging.error("No transcript segments found.")
        sys.exit(1)
    return segments

def transcribe_chunk(chunk_file, offset, chunk_index):
    """
    Transcribe a video chunk, adjust its transcript time codes by the given offset,
    and save the transcript segments to a JSON file.
    Returns the adjusted transcript segments and the transcript filename.
    """
    segments = extract_transcript(chunk_file)
    # Adjust time codes by the offset (in seconds)
    for seg in segments:
        seg["start"] += offset
        seg["end"] += offset
    transcript_filename = f"{os.path.splitext(chunk_file)[0]}.transcript.json"
    with open(transcript_filename, "w", encoding="utf-8") as f:
        json.dump(segments, f, indent=2)
    logging.info(f"[Transcription] Saved transcript for chunk {chunk_index+1} to {transcript_filename}")
    return segments, transcript_filename

# ------------------------------
# Segmentation & Keyword Matching
# ------------------------------

def segment_topics(transcript_segments, threshold=0.7):
    """
    Segment the transcript into topics using sentence embeddings.
    When the cosine similarity between consecutive transcript segments
    falls below the threshold, a new topic is assumed.
    Returns a list of topics (each topic is a list of transcript segments).
    """
    logging.info("[Segmentation] Segmenting full transcript into topics...")
    st_model = SentenceTransformer('all-MiniLM-L6-v2')
    texts = [seg["text"].strip() for seg in transcript_segments]
    embeddings = st_model.encode(texts)
    
    topics = []  # Each topic is a list of transcript segments.
    current_topic = [transcript_segments[0]]
    
    for i in range(1, len(transcript_segments)):
        prev_emb = embeddings[i - 1].reshape(1, -1)
        curr_emb = embeddings[i].reshape(1, -1)
        sim = cosine_similarity(prev_emb, curr_emb)[0][0]
        if sim < threshold:
            topics.append(current_topic)
            current_topic = []
        current_topic.append(transcript_segments[i])
    if current_topic:
        topics.append(current_topic)
    
    logging.info(f"[Segmentation] Detected {len(topics)} topic segment(s) in the full transcript.")
    return topics

def extract_keywords_from_filename(video_path):
    """
    Extract keywords from the video file name.
    Assumes the file name (without extension) contains keywords separated by '!'
    For example: "ELON TAKEOVER! SWEDEN MASS SHOOTING! USAID DOWN! ..."
    """
    base = os.path.splitext(os.path.basename(video_path))[0]
    keywords = [kw.strip() for kw in base.split('!') if kw.strip()]
    return keywords

def fuzzy_match(a, b, threshold=0.3):
    """Return True if the fuzzy similarity between strings a and b is at least threshold."""
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= threshold

def find_segments_for_keywords(topics, keywords):
    """
    For each keyword, search through the topic segments (the full transcript segmentation)
    using partial and fuzzy matching. Returns a dictionary mapping each keyword to a list
    of matching topic segments.
    """
    keyword_segments = {kw: [] for kw in keywords}
    for topic in topics:
        # Combine the text from all segments in this topic.
        topic_text = " ".join(seg["text"] for seg in topic).lower()
        for kw in keywords:
            kw_lower = kw.lower()
            # Partial match: check if any word in the keyword appears in the topic text.
            words = [w for w in kw_lower.split() if len(w) > 2]
            partial_match = any(word in topic_text for word in words)
            # Fuzzy match: compare full keyword with topic text.
            fuzzy = fuzzy_match(kw_lower, topic_text, threshold=0.3)
            if partial_match or fuzzy:
                keyword_segments[kw].append(topic)
    for kw, segs in keyword_segments.items():
        if not segs:
            logging.warning(f"No segment found containing the keyword: {kw}")
        else:
            logging.info(f"Found {len(segs)} matching topic segment(s) for keyword: {kw}")
    return keyword_segments

def flatten_topic_segments(topic_list):
    """
    Given a list of topic segments (each is a list of transcript segments),
    flatten them into one sorted list of individual transcript segments.
    """
    flat = []
    for topic in topic_list:
        flat.extend(topic)
    flat.sort(key=lambda x: x["start"])
    return flat

# ------------------------------
# Video Concatenation Function
# ------------------------------

def concatenate_video_segments(temp_files, output_file):
    """
    Concatenates the list of temporary video segment files into a single video file
    using ffmpeg's concat demuxer.
    """
    list_filename = os.path.join(os.path.dirname(output_file), f"concat_list_{os.path.basename(output_file)}.txt")
    with open(list_filename, "w", encoding="utf-8") as f:
        for temp_file in temp_files:
            f.write(f"file '{temp_file}'\n")
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_filename,
        "-c", "copy",
        output_file
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    os.remove(list_filename)
    logging.info(f"Concatenated {len(temp_files)} segments into {output_file}")

# ------------------------------
# Cutting functions (using original full video)
# ------------------------------

def cut_video_segment_range(full_video_path, start_time, end_time, output_file):
    """
    Cut the video segment (from start_time to end_time, in seconds) from the original full video.
    """
    logging.info(f"Cutting video segment: {output_file} (from {start_time:.2f}s to {end_time:.2f}s)")
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite output file if it exists.
        "-i", full_video_path,
        "-ss", str(start_time),
        "-to", str(end_time),
        "-c", "copy",
        output_file
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# ------------------------------
# Cleanup function
# ------------------------------

def cleanup_intermediate_files(files_list):
    """Delete the files specified in files_list."""
    for file in files_list:
        try:
            os.remove(file)
            logging.info(f"Deleted intermediate file: {file}")
        except Exception as e:
            logging.error(f"Error deleting file {file}: {e}")

# ------------------------------
# Main workflow
# ------------------------------

def main():
    # Specify your video file path here.
    video_path = r"C:\Users\anasm\Videos\4K Video Downloader+\TRUMP IN JAPAN! DOGE LATEST DRAMA! ICE RAIDS! CRACK ON USAID! USAID CONSPIRACY! TECH BILLIONAIRES DARK GOTHIC MAGA!.mp4"
    if not os.path.exists(video_path):
        logging.error(f"Error: video file '{video_path}' does not exist.")
        sys.exit(1)
    
    duration = get_video_duration(video_path)
    logging.info(f"Video duration: {duration/3600:.2f} hours")
    
    # Create an output folder for final segments.
    base_dir = os.path.dirname(video_path)
    final_output_dir = os.path.join(base_dir, "final_segments")
    os.makedirs(final_output_dir, exist_ok=True)
    
    # Define the full transcript file name.
    full_transcript_file = os.path.splitext(video_path)[0] + "_full_transcript.json"
    full_transcript = []
    intermediate_files = []  # For chunk files and individual transcript JSONs.
    
    if os.path.exists(full_transcript_file):
        # Skip transcription if full transcript exists.
        with open(full_transcript_file, "r", encoding="utf-8") as f:
            full_transcript = json.load(f)
        logging.info(f"Loaded full transcript from {full_transcript_file}")
    else:
        # Split the video if it's longer than 1 hour; else process as one chunk.
        if duration > 3600:
            chunk_duration = 900  # 15 minutes per chunk
            chunk_files = split_video_ffmpeg(video_path, chunk_duration=chunk_duration)
        else:
            chunk_files = [video_path]
    
        # Transcribe each chunk and adjust time codes.
        for idx, chunk in enumerate(chunk_files):
            offset = idx * 900  # in seconds
            segments, transcript_file = transcribe_chunk(chunk, offset, idx)
            full_transcript.extend(segments)
            if chunk != video_path:
                intermediate_files.append(chunk)
            intermediate_files.append(transcript_file)
    
        # Save the full transcript.
        with open(full_transcript_file, "w", encoding="utf-8") as f:
            json.dump(full_transcript, f, indent=2)
        logging.info(f"Saved full transcript to {full_transcript_file}")
    
    # Proceed with segmentation and keyword matching.
    topics = segment_topics(full_transcript, threshold=0.7)
    keywords = extract_keywords_from_filename(video_path)
    if keywords:
        logging.info(f"Extracted keywords from filename: {keywords}")
    else:
        logging.error("No keywords extracted from filename.")
        sys.exit(1)
    
    raw_keyword_segments = find_segments_for_keywords(topics, keywords)
    
    # For each keyword, flatten all matching topic segments into one sorted list.
    final_keyword_segments = {}
    for kw, topic_segments in raw_keyword_segments.items():
        if topic_segments:
            flat_segments = []
            for topic in topic_segments:
                flat_segments.extend(topic)
            flat_segments.sort(key=lambda x: x["start"])
            final_keyword_segments[kw] = flat_segments
            logging.info(f"Keyword '{kw}': {len(flat_segments)} individual segment(s) after flattening.")
        else:
            final_keyword_segments[kw] = []
    
    # For each keyword, cut all matching segments and concatenate them.
    for kw, segments in final_keyword_segments.items():
        safe_kw = kw.replace(" ", "_").replace("/", "-")
        if not segments:
            logging.warning(f"No final segments found for keyword: {kw}")
            continue
        
        # Create temporary files for each segment.
        temp_files = []
        concatenated_transcript = []
        for i, seg in enumerate(segments, start=1):
            temp_filename = os.path.join(final_output_dir, f"temp_{safe_kw}_part{i}.mp4")
            cut_video_segment_range(video_path, seg["start"], seg["end"], temp_filename)
            temp_files.append(temp_filename)
            concatenated_transcript.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"]
            })
        
        # Concatenate all temporary segment files into one final video for this keyword.
        final_video = os.path.join(final_output_dir, f"final_segment_{safe_kw}.mp4")
        concatenate_video_segments(temp_files, final_video)
        
        # Save the concatenated transcript for this keyword.
        final_transcript_filename = os.path.join(final_output_dir, f"final_segment_{safe_kw}_transcript.json")
        with open(final_transcript_filename, "w", encoding="utf-8") as f:
            json.dump(concatenated_transcript, f, indent=2)
        logging.info(f"Saved concatenated transcript for keyword '{kw}' to {final_transcript_filename}")
        
        # Delete the temporary segment files.
        for tf in temp_files:
            try:
                os.remove(tf)
                logging.info(f"Deleted temporary segment file: {tf}")
            except Exception as e:
                logging.error(f"Error deleting temporary file {tf}: {e}")
    
    # Cleanup intermediate chunk files and transcript JSONs.
    logging.info("Cleaning up intermediate files...")
    cleanup_intermediate_files(intermediate_files)
    
    logging.info("Processing complete.")
    logging.info(f"Final segments, their transcripts, and the full transcript are located in: {final_output_dir}")

if __name__ == "__main__":
    main()
