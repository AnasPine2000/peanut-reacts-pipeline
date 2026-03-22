#!/usr/bin/env python3
"""
segment_video_keyword_debug.py

This script:
  1. Processes all video files in a given directory.
  2. For each video, it first checks if an SRT file (with the same title) exists in the downloads directory.
     If found, it loads the transcript from that SRT file. Otherwise, it falls back to an existing JSON
     transcript or transcribes the video using Whisper (splitting into chunks if needed).
  3. Searches each transcript for segments that match (exactly or fuzzy) a given keyword.
  4. For each matching segment, cuts a video clip of 20-30 seconds (randomly chosen)
     such that the matching segment’s midpoint is roughly in the center of the clip.
  5. Saves the resulting clips and logs detailed debugging information.
  6. Preserves transcript files to speed up subsequent runs.
  7. Finally concatenates all generated clips (from all videos) into a single combined video.

Requirements:
  - ffmpeg and ffprobe must be installed and in your PATH.
  - Python packages: openai-whisper, and any other required libraries.
  
Usage:
  python segment_video_keyword_debug.py <keyword> [input_directory]
"""

import os
import sys
import subprocess
import json
import logging
import random
import difflib
import whisper

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
    try:
        output = subprocess.check_output(cmd).decode().strip()
        duration = float(output)
        logging.debug(f"Video duration (seconds): {duration}")
        return duration
    except subprocess.CalledProcessError as e:
        logging.error(f"ffprobe error: {e}")
        sys.exit(1)
    except Exception as ex:
        logging.error(f"Error retrieving video duration: {ex}")
        sys.exit(1)

def check_file_type(video_path):
    """Log the file extension and basic info of the input video."""
    ext = os.path.splitext(video_path)[1].lower()
    logging.info(f"Input video file extension: {ext}")
    return ext

def split_video_ffmpeg(video_path, chunk_duration=900):
    """
    Split the video into chunks of chunk_duration seconds using ffmpeg.
    Returns a list of chunk file paths.
    """
    base_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    split_pattern = os.path.join(base_dir, f"{base_name}_chunk_%03d{os.path.splitext(video_path)[1]}")
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite if necessary
        "-i", video_path,
        "-c", "copy",
        "-map", "0",
        "-segment_time", str(chunk_duration),
        "-f", "segment",
        split_pattern
    ]
    logging.info("Splitting video into chunks for transcription...")
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logging.debug(f"ffmpeg split output: {result.stdout.decode('utf-8')}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error splitting video: {e.stderr.decode('utf-8')}")
        sys.exit(1)
    
    chunk_files = sorted([
        os.path.join(base_dir, f)
        for f in os.listdir(base_dir)
        if f.startswith(f"{base_name}_chunk_") and f.endswith(os.path.splitext(video_path)[1])
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
    try:
        with open(transcript_filename, "w", encoding="utf-8") as f:
            json.dump(segments, f, indent=2)
        logging.info(f"[Transcription] Saved transcript for chunk {chunk_index+1} to {transcript_filename}")
    except Exception as ex:
        logging.error(f"Error saving transcript: {ex}")
    return segments, transcript_filename

# ------------------------------
# SRT Parsing (for existing subtitles)
# ------------------------------

def srt_timestamp_to_seconds(ts):
    """Convert SRT timestamp (HH:MM:SS,ms) to seconds."""
    h, m, s_ms = ts.split(':')
    s, ms = s_ms.split(',')
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000.0

def parse_srt(srt_file):
    """
    Parse an SRT file into a transcript list.
    Each entry is a dict with keys "start", "end", and "text".
    """
    transcript = []
    try:
        with open(srt_file, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as ex:
        logging.error(f"Error reading SRT file {srt_file}: {ex}")
        return transcript

    blocks = content.strip().split('\n\n')
    for block in blocks:
        lines = block.splitlines()
        if len(lines) >= 3:
            # First line is a counter, second line contains the timecodes, rest is text.
            time_line = lines[1]
            try:
                start_str, end_str = time_line.split(' --> ')
                start = srt_timestamp_to_seconds(start_str.strip())
                end = srt_timestamp_to_seconds(end_str.strip())
            except Exception as ex:
                logging.error(f"Error parsing time in block: {block}. Exception: {ex}")
                continue
            text = " ".join(lines[2:])
            transcript.append({"start": start, "end": end, "text": text})
    logging.info(f"Parsed {len(transcript)} segments from SRT file {srt_file}")
    return transcript

# ------------------------------
# Keyword Matching Functions
# ------------------------------

def fuzzy_contains(text, keyword, threshold=0.6):
    """
    Check if any word in text fuzzily matches the keyword.
    Returns True if a close enough match is found.
    """
    text_words = text.lower().split()
    keyword_lower = keyword.lower()
    for word in text_words:
        ratio = difflib.SequenceMatcher(None, word, keyword_lower).ratio()
        if ratio >= threshold:
            return True
    return False

def find_keyword_matches(transcript, keyword, threshold=0.6):
    """
    Search through the transcript segments and return those that either
    contain the keyword (exact match) or fuzzy match it.
    """
    matches = []
    for seg in transcript:
        seg_text = seg.get("text", "")
        if keyword.lower() in seg_text.lower() or fuzzy_contains(seg_text, keyword, threshold):
            matches.append(seg)
    logging.info(f"Found {len(matches)} matching segment(s) for keyword '{keyword}'.")
    return matches

# ------------------------------
# Video Cutting Function
# ------------------------------

def cut_video_segment_range(full_video_path, start_time, end_time, output_file):
    """
    Cut the video segment (from start_time to end_time, in seconds) from the original full video.
    Uses re-encoding for MP4 outputs (for compatibility) and stream copy otherwise.
    """
    logging.info(f"Cutting video segment: {output_file} (from {start_time:.2f}s to {end_time:.2f}s)")
    ext = os.path.splitext(output_file)[1].lower()
    if ext == ".mp4":
        # Re-encode to ensure compatibility
        cmd = [
            "ffmpeg", "-y",
            "-i", full_video_path,
            "-ss", str(start_time),
            "-to", str(end_time),
            "-c:v", "libx264", "-c:a", "aac",
            output_file
        ]
    else:
        # For same container copy (e.g., webm)
        cmd = [
            "ffmpeg", "-y",
            "-i", full_video_path,
            "-ss", str(start_time),
            "-to", str(end_time),
            "-c", "copy",
            output_file
        ]
    
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logging.debug(f"ffmpeg cutting output: {result.stdout.decode('utf-8')}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error cutting video segment: {e.stderr.decode('utf-8')}")
        return None
    return output_file

def create_keyword_clip(video_path, match_segment, video_duration, output_dir, keyword, idx):
    """
    Create a clip of random duration (20-30 seconds) such that the midpoint of the
    matching segment is in the middle of the clip.
    """
    mid_time = (match_segment["start"] + match_segment["end"]) / 2
    clip_length = random.randint(20, 30)
    half_length = clip_length / 2
    clip_start = mid_time - half_length
    clip_end = mid_time + half_length

    # Adjust boundaries if they exceed video limits.
    if clip_start < 0:
        clip_start = 0
        clip_end = clip_length
    if clip_end > video_duration:
        clip_end = video_duration
        clip_start = max(0, video_duration - clip_length)

    # Use the input file's extension for the output clip.
    input_ext = os.path.splitext(video_path)[1]
    safe_keyword = "".join(c for c in keyword if c.isalnum() or c.isspace()).strip().replace(" ", "_")
    video_base = os.path.splitext(os.path.basename(video_path))[0]
    clip_filename = os.path.join(output_dir, f"{video_base}_{safe_keyword}_clip_{idx}{input_ext}")
    
    output = cut_video_segment_range(video_path, clip_start, clip_end, clip_filename)
    if output:
        logging.info(f"Created keyword clip: {output}")
    else:
        logging.error(f"Failed to create clip for match at {mid_time:.2f}s")
    return output

# ------------------------------
# Concatenation Function
# ------------------------------

def concatenate_clips(clip_files, output_file):
    """
    Concatenate multiple video clips into a single video using ffmpeg's concat demuxer.
    A temporary file list is created in the same directory as the output_file.
    """
    list_filename = os.path.join(os.path.dirname(output_file), "concat_list.txt")
    try:
        with open(list_filename, "w", encoding="utf-8") as f:
            for clip in clip_files:
                f.write(f"file '{clip}'\n")
        logging.info(f"Created concat list file: {list_filename}")
    except Exception as ex:
        logging.error(f"Error writing concat list file: {ex}")
        return None

    cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", list_filename,
        "-c", "copy",
        output_file
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logging.info(f"Successfully concatenated clips into: {output_file}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error concatenating clips: {e.stderr.decode('utf-8')}")
        return None
    return output_file

# ------------------------------
# Cleanup function (preserving transcripts)
# ------------------------------

def cleanup_intermediate_files(files_list):
    """
    Delete intermediate files except transcript files (ending with .transcript.json)
    to speed up subsequent keyword searches.
    """
    for file in files_list:
        if file.endswith(".transcript.json"):
            logging.info(f"Preserving transcript file: {file}")
            continue
        try:
            os.remove(file)
            logging.info(f"Deleted intermediate file: {file}")
        except Exception as e:
            logging.error(f"Error deleting file {file}: {e}")

# ------------------------------
# Process a single video file
# ------------------------------

def process_video(video_path, keyword, output_dir):
    logging.info(f"Processing video: {video_path}")
    file_ext = check_file_type(video_path)
    duration = get_video_duration(video_path)
    logging.info(f"Video duration: {duration/3600:.2f} hours")
    
    base_name = os.path.splitext(video_path)[0]
    transcript = []
    intermediate_files = []
    
    # First, check if an SRT file exists in the downloads directory (e.g. "video.en.srt")
    srt_path = base_name + ".en.srt"
    if os.path.exists(srt_path):
        transcript = parse_srt(srt_path)
        logging.info(f"Loaded transcript from SRT file {srt_path}")
    # Otherwise, try loading an existing JSON transcript
    elif os.path.exists(base_name + "_full_transcript.json"):
        transcript_file_json = base_name + "_full_transcript.json"
        try:
            with open(transcript_file_json, "r", encoding="utf-8") as f:
                transcript = json.load(f)
            logging.info(f"Loaded transcript from {transcript_file_json}")
        except Exception as ex:
            logging.error(f"Error loading transcript: {ex}")
    
    # If no existing transcript is found, transcribe the video
    if not transcript:
        if duration > 3600:
            chunk_duration = 900  # 15 minutes per chunk
            chunk_files = split_video_ffmpeg(video_path, chunk_duration=chunk_duration)
        else:
            chunk_files = [video_path]
    
        for idx, chunk in enumerate(chunk_files):
            offset = idx * 900  # seconds offset per chunk
            segments, transcript_file = transcribe_chunk(chunk, offset, idx)
            transcript.extend(segments)
            if chunk != video_path:
                intermediate_files.append(chunk)
            intermediate_files.append(transcript_file)
    
        # Save the full transcript for later runs.
        transcript_file_json = base_name + "_full_transcript.json"
        try:
            with open(transcript_file_json, "w", encoding="utf-8") as f:
                json.dump(transcript, f, indent=2)
            logging.info(f"Saved full transcript to {transcript_file_json}")
        except Exception as ex:
            logging.error(f"Error saving full transcript: {ex}")
    
    # Search the transcript for keyword matches.
    logging.info("Searching transcript for matching segments...")
    matches = find_keyword_matches(transcript, keyword)
    if not matches:
        logging.warning(f"No matching segments found for keyword '{keyword}' in video {video_path}")
        return [], intermediate_files
    
    # Create clip files for each matching segment.
    clip_files = []
    for idx, match in enumerate(matches, start=1):
        clip = create_keyword_clip(video_path, match, duration, output_dir, keyword, idx)
        if clip:
            clip_files.append(clip)
    return clip_files, intermediate_files

# ------------------------------
# Main workflow: process all videos in a directory
# ------------------------------

def main():
    # For this example, we hardcode the keyword and input directory.
    keyword = "I am the imposter"
    input_directory = 'downloads'
    
    # Find all video files with common extensions.
    video_extensions = ('.mp4', '.webm', '.mkv', '.avi')
    video_files = [os.path.join(input_directory, f)
                   for f in os.listdir(input_directory)
                   if f.lower().endswith(video_extensions)]
    
    if not video_files:
        logging.error("No video files found in the specified directory.")
        sys.exit(1)
    
    # Create an output folder for keyword clips.
    final_output_dir = os.path.join(input_directory, "keyword_clips")
    os.makedirs(final_output_dir, exist_ok=True)
    
    all_clip_files = []
    all_intermediate_files = []
    
    for video in video_files:
        clip_files, intermediate_files = process_video(video, keyword, final_output_dir)
        all_clip_files.extend(clip_files)
        all_intermediate_files.extend(intermediate_files)
    
    # Concatenate all generated clips into one final video (standardized as MP4).
    if all_clip_files:
        safe_keyword = "".join(c for c in keyword if c.isalnum() or c.isspace()).strip().replace(" ", "_")
        combined_video = os.path.join(final_output_dir, f"combined_{safe_keyword}_clips.mp4")
        concat_result = concatenate_clips(all_clip_files, combined_video)
        if concat_result:
            logging.info(f"Final combined video is located at: {concat_result}")
        else:
            logging.error("Failed to create the combined video.")
    else:
        logging.warning("No clip files were generated; skipping concatenation.")
    
    # Cleanup intermediate files (preserve transcript files).
    logging.info("Cleaning up intermediate files...")
    cleanup_intermediate_files(all_intermediate_files)
    logging.info("Processing complete.")

if __name__ == "__main__":
    main()
