#!/usr/bin/env python3
"""
segment_video_deepseek.py

This script:
  1. Transcribes a full video (splitting into chunks if needed) using Whisper.
  2. Combines the chunk transcripts into one full transcript.
  3. Uses DeepSeek NLP (or a dummy segmentation) to segment the full transcript into topics (each 15-90 minutes).
  4. Cuts from the original video a clip for each topic (using ffmpeg) and saves a transcript.
  
Requirements:
  - ffmpeg and ffprobe must be installed and in your PATH.
  - Python packages: openai-whisper, deepseek (or your DeepSeek NLP integration),
    and any other required libraries.
"""

import os
import sys
import subprocess
import json
import logging
import whisper

# Import your deepseek NLP module (adjust the import to your actual package)
import deepseek  # This is a placeholder for your DeepSeek NLP library

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
    logging.info("[Transcription] Loading Whisper model on GPU...")
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
# YouTube Title Generation Helper Function
# ------------------------------

def generate_youtube_title(segments):
    """
    Generate an eye-catching YouTube title for a clip by summarizing its content.
    This simple implementation concatenates the text from all segments,
    extracts the first sentence as a summary, and prepends an attention-grabbing phrase.
    Replace or extend this with a proper summarization model for improved results.
    """
    combined_text = " ".join(seg["text"].strip() for seg in segments)
    # Split by common sentence terminators.
    sentences = combined_text.split('.')
    if sentences:
        summary = sentences[0].strip()
    else:
        summary = combined_text[:50]
    
    # Create an eye-catching title.
    title = f"Must Watch! {summary}..."
    
    # Optionally, further processing can be done here (e.g., capitalization, removing extra spaces).
    # For safe filenames, we'll remove non-alphanumeric characters.
    safe_title = "".join(c for c in title if c.isalnum() or c.isspace()).strip()
    return safe_title or "Untitled"

# ------------------------------
# DeepSeek NLP-based Transcript Segmentation
# ------------------------------

def segment_topics_deepseek(transcript, min_duration=900, max_duration=5400):
    """
    Dummy segmentation function.
    Splits the transcript into topics such that each topic is at least min_duration seconds long.
    This is a simple placeholder and may not reflect real NLP segmentation.
    """
    topics = []
    current_topic = []
    current_duration = 0
    
    for seg in transcript:
        current_topic.append(seg)
        current_duration += seg["end"] - seg["start"]
        
        # When the current topic reaches the minimum duration, close it out.
        if current_duration >= min_duration:
            topics.append(current_topic)
            current_topic = []
            current_duration = 0
    
    if current_topic:
        topics.append(current_topic)
    
    return topics

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
    video_path = r"C:\Users\anasm\Videos\4K Video Downloader+\SUPERBOWL DRAMA! EAGLES W! HAMAS POSTPONES HOSTAGE RELEASE! TRUMP SAYS NO RIGHT TO RETURN FOR PALESTINIANS ONCE US OWNS GAZA (GENOCIDE).mp4"  # update this path as needed
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
        with open(full_transcript_file, "r", encoding="utf-8") as f:
            full_transcript = json.load(f)
        logging.info(f"Loaded full transcript from {full_transcript_file}")
    else:
        # If video is longer than 1 hour, split into chunks; else process as one chunk.
        if duration > 3600:
            chunk_duration = 900  # 15 minutes per chunk
            chunk_files = split_video_ffmpeg(video_path, chunk_duration=chunk_duration)
        else:
            chunk_files = [video_path]
    
        # Transcribe each chunk and adjust time codes.
        for idx, chunk in enumerate(chunk_files):
            offset = idx * 900  # seconds offset per chunk
            segments, transcript_file = transcribe_chunk(chunk, offset, idx)
            full_transcript.extend(segments)
            if chunk != video_path:
                intermediate_files.append(chunk)
            intermediate_files.append(transcript_file)
    
        # Save the full transcript.
        with open(full_transcript_file, "w", encoding="utf-8") as f:
            json.dump(full_transcript, f, indent=2)
        logging.info(f"Saved full transcript to {full_transcript_file}")
    
    # --------- DeepSeek Transcript Segmentation ---------
    # Segment the full transcript into topics (clips between 15 and 90 minutes).
    topics = segment_topics_deepseek(full_transcript, min_duration=900, max_duration=5400)
    
    # For each topic returned by DeepSeek, extract the corresponding video clip.
    for idx, topic in enumerate(topics, start=1):
        # Determine clip boundaries from the transcript segments.
        # If DeepSeek returns a dict with a "title", use it; otherwise, generate a YouTube-style title.
        if isinstance(topic, dict):
            segments = topic.get("segments", [])
            if "title" in topic and topic["title"].strip():
                title = topic["title"]
            else:
                title = generate_youtube_title(segments)
        else:
            segments = topic  # assume topic is a list of transcript segments
            title = generate_youtube_title(segments)
        
        if not segments:
            logging.warning(f"Topic {idx} has no transcript segments; skipping.")
            continue

        start_time = segments[0]["start"]
        end_time = segments[-1]["end"]
        safe_title = title.replace(" ", "_").replace("/", "-")
        clip_filename = os.path.join(final_output_dir, f"{safe_title}.mp4")
        
        cut_video_segment_range(video_path, start_time, end_time, clip_filename)
        logging.info(f"Created clip: {clip_filename} (Duration: {end_time - start_time:.2f} seconds)")
        
        # Save the topic transcript as a separate file.
        topic_transcript_file = os.path.join(final_output_dir, f"{safe_title}_transcript.json")
        with open(topic_transcript_file, "w", encoding="utf-8") as f:
            json.dump(segments, f, indent=2)
        logging.info(f"Saved topic transcript to: {topic_transcript_file}")
    
    # Cleanup intermediate chunk and transcript files.
    logging.info("Cleaning up intermediate files...")
    cleanup_intermediate_files(intermediate_files)
    
    logging.info("Processing complete.")
    logging.info(f"Final clips and transcripts are located in: {final_output_dir}")

if __name__ == "__main__":
    main()
