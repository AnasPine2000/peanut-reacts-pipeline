#!/usr/bin/env python3
"""
extract_loud_clips_parallel.py

This script:
  1. Downloads a YouTube playlist using yt-dlp (best video and audio).
  2. For each video (avg. ~1 hour, with likely loud moments):
     - Extracts and caches its audio as a WAV file.
     - Analyzes the audio by splitting it into 1-second windows and computing dBFS.
       The top 10–20% (default 15%) of windows are selected, and contiguous windows
       are merged into candidate peaks. For each candidate, a clip of random duration
       (between 5 and 15 seconds) is generated and its average dBFS is computed.
       The resulting timestamps and metrics are saved to a JSON cache for future use.
     - The candidate segments are then deduplicated so that clips overlapping
       (or shifted by less than 3 seconds) are not included twice.
     - Cuts the corresponding video segment using ffmpeg with NVENC acceleration.
  3. Processes videos concurrently using up to 22 threads.
  4. Randomly shuffles all extracted clips before concatenating them into one final video.
  
Requirements:
  - ffmpeg and ffprobe (with NVENC support) must be installed and in your PATH.
  - Python packages: yt-dlp, pydub, numpy.
"""

import os
import subprocess
import logging
import random
import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from yt_dlp import YoutubeDL
from pydub import AudioSegment

# Configure logging (minimal console output; use logging for status updates)
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Global counter for detected candidate segments (for reporting purposes)
global_loud_count = 0
loud_count_lock = threading.Lock()

# ------------------------------
# YouTube Playlist Download
# ------------------------------
def download_playlist(playlist_url, download_dir):
    """
    Download all videos in a YouTube playlist using yt-dlp.
    Downloads the best video and audio qualities.
    """
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
        'yesplaylist': True,
        'ignoreerrors': True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_url, download=True)
    logging.info("Playlist download complete.")
    return info

# ------------------------------
# Audio Extraction with Caching (Updated for WAV)
# ------------------------------
def extract_audio(video_path, audio_path):
    """
    Extract audio from a video using ffmpeg.
    Converts the input (even if webm) to a WAV file.
    If the audio file already exists, skip extraction (caching).
    """
    if os.path.exists(audio_path):
        logging.info(f"Audio already exists for '{video_path}'. Skipping extraction.")
        return audio_path

    logging.info(f"Extracting audio from '{video_path}' to '{audio_path}'...")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",                # no video
        "-ar", "44100",       # sampling rate
        "-ac", "2",           # stereo audio
        audio_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return audio_path

# ------------------------------
# Loud Segment Detection using Loudness Metrics
# ------------------------------
def detect_loud_segments_v2(audio_file, window_size_ms=1000, step_size_ms=1000,
                            min_clip_duration_ms=5000, max_clip_duration_ms=15000,
                            selection_percentage=15):
    """
    Analyze the audio file by splitting it into 1-second windows and computing the dBFS for each.
    Then select the top (selection_percentage)% windows (by loudness), merge contiguous windows into peaks,
    and for each peak generate a candidate clip with a random duration between min and max.
    For each candidate clip, the average dBFS is computed.
    
    Returns a list of dictionaries:
      [{"start": <seconds>, "end": <seconds>, "avg_dBFS": <value>}, ...]
    """
    logging.info(f"Analyzing audio for loud segments in '{audio_file}' using metric-based selection...")
    audio = AudioSegment.from_file(audio_file)
    duration_ms = len(audio)
    
    # Create candidate windows.
    windows = []
    num_windows = int((duration_ms - window_size_ms) / step_size_ms) + 1
    for i in range(num_windows):
        start = i * step_size_ms
        end = start + window_size_ms
        segment = audio[start:end]
        windows.append({"start": start, "end": end, "dBFS": segment.dBFS})
    
    # Select top X% windows.
    num_select = max(1, int(len(windows) * selection_percentage / 100.0))
    sorted_windows = sorted(windows, key=lambda w: w["dBFS"], reverse=True)
    selected_windows = sorted_windows[:num_select]
    
    # Merge contiguous selected windows into peaks.
    selected_windows = sorted(selected_windows, key=lambda w: w["start"])
    merged_peaks = []
    current_group = [selected_windows[0]]
    for w in selected_windows[1:]:
        # Merge if the window starts within the step size of the previous window.
        if w["start"] <= current_group[-1]["end"] + step_size_ms:
            current_group.append(w)
        else:
            peak = max(current_group, key=lambda x: x["dBFS"])
            merged_peaks.append(peak)
            current_group = [w]
    if current_group:
        peak = max(current_group, key=lambda x: x["dBFS"])
        merged_peaks.append(peak)
    
    # Generate final candidate segments from each peak.
    final_segments = []
    for peak in merged_peaks:
        clip_duration = random.randint(min_clip_duration_ms, max_clip_duration_ms)
        start_time_ms = peak["start"]
        if start_time_ms + clip_duration > duration_ms:
            start_time_ms = max(0, duration_ms - clip_duration)
        end_time_ms = start_time_ms + clip_duration
        segment_audio = audio[start_time_ms:end_time_ms]
        avg_dBFS = segment_audio.dBFS
        final_segments.append({
            "start": start_time_ms / 1000.0,
            "end": end_time_ms / 1000.0,
            "avg_dBFS": avg_dBFS
        })
    logging.info(f"Detected {len(final_segments)} candidate loud segment(s) in '{audio_file}'.")
    return final_segments

def cache_loud_segments(audio_file, cache_file, **kwargs):
    """
    Load loud segments (timestamps and metrics) from cache if available; otherwise, compute and save to cache.
    """
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                segments = json.load(f)
            logging.info(f"Loaded cached segments from '{cache_file}'.")
            return segments
        except Exception as e:
            logging.warning(f"Failed to load cache from '{cache_file}': {e}")
    segments = detect_loud_segments_v2(audio_file, **kwargs)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(segments, f)
    return segments

# ------------------------------
# Deduplicate Overlapping Segments
# ------------------------------
def deduplicate_segments(segments, shift=3):
    """
    Given a list of candidate segments (each with "start", "end", and "avg_dBFS"),
    remove segments that are nearly overlapping.
    
    If the start time of a candidate is within `shift` seconds of the previous candidate's end,
    keep the one with the higher average dBFS.
    """
    if not segments:
        return segments
    segments = sorted(segments, key=lambda s: s["start"])
    deduped = [segments[0]]
    for seg in segments[1:]:
        last = deduped[-1]
        if seg["start"] < last["end"] + shift:
            if seg["avg_dBFS"] > last["avg_dBFS"]:
                deduped[-1] = seg
        else:
            deduped.append(seg)
    return deduped

# ------------------------------
# Video Cutting Function using NVENC
# ------------------------------
def cut_video_segment(video_path, start_time, end_time, output_file):
    """
    Cut a video segment using ffmpeg with NVENC for hardware acceleration.
    """
    logging.info(f"Cutting segment: '{output_file}' (from {start_time:.2f}s to {end_time:.2f}s)")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ss", str(start_time),
        "-to", str(end_time),
        "-c:v", "h264_nvenc",
        "-c:a", "copy",
        output_file
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# ------------------------------
# Concatenate Clips (with Random Shuffle)
# ------------------------------
def concatenate_clips(clips, output_file):
    """
    Randomly shuffle and concatenate video clips using ffmpeg's concat demuxer.
    """
    random.shuffle(clips)
    list_file = "clips_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for clip in clips:
            f.write(f"file '{os.path.abspath(clip)}'\n")
    logging.info("Concatenating clips into final video...")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        output_file
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    os.remove(list_file)
    logging.info(f"Final video saved as '{output_file}'.")

# ------------------------------
# Process a Single Video
# ------------------------------
def process_video(video_path, output_dir):
    """
    Process a single video:
      - Extract and cache its audio (WAV).
      - Detect loud segments (using caching with the metric-based method).
      - Deduplicate overlapping segments (with a shift threshold of 3 seconds).
      - For each segment, cut the corresponding video clip using NVENC.
    Returns a list of clip file paths.
    """
    global global_loud_count
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    logging.info(f"Processing video: '{video_path}'")
    
    audio_path = os.path.join(output_dir, f"{base_name}.wav")
    segments_cache = os.path.join(output_dir, f"{base_name}_segments.json")
    
    extract_audio(video_path, audio_path)
    
    segments = cache_loud_segments(audio_path, segments_cache,
                                   window_size_ms=1000, step_size_ms=1000,
                                   min_clip_duration_ms=5000, max_clip_duration_ms=15000,
                                   selection_percentage=15)
    
    segments = deduplicate_segments(segments, shift=3)
    
    with loud_count_lock:
        global_loud_count += len(segments)
    
    clip_files = []
    for idx, seg in enumerate(segments, start=1):
        clip_filename = os.path.join(output_dir, f"{base_name}_loud_clip_{idx:03d}.mp4")
        if not os.path.exists(clip_filename):
            cut_video_segment(video_path, seg["start"], seg["end"], clip_filename)
        clip_files.append(clip_filename)
    return clip_files

# ------------------------------
# Main Workflow
# ------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Extract the loudest (top 10–20%) clips from videos in a YouTube playlist and concatenate them (randomized order)."
    )
    parser.add_argument("--playlist_url", default="https://www.youtube.com/playlist?list=PLhjLcvbbPVrVOY5w5Pl7KuSe5fGroQJJD", help="YouTube playlist URL")
    parser.add_argument("--download_dir", default="downloads", help="Directory to download videos")
    parser.add_argument("--output_dir", default="output_clips", help="Directory to store clips and cache")
    parser.add_argument("--final_video", default="final_loud_compilation.mp4", help="Filename for the final concatenated video")
    args = parser.parse_args()
    
    os.makedirs(args.download_dir, exist_ok=True)
    os.makedirs(args.output_dir, exist_ok=True)
    
    logging.info(f"Downloading playlist: '{args.playlist_url}'")
    download_playlist(args.playlist_url, args.download_dir)
    
    video_files = [os.path.join(args.download_dir, f) for f in os.listdir(args.download_dir) if f.endswith(".mp4")]
    
    all_clip_files = []
    with ThreadPoolExecutor(max_workers=22) as executor:
        future_to_video = {executor.submit(process_video, video, args.output_dir): video for video in video_files}
        for future in as_completed(future_to_video):
            video = future_to_video[future]
            try:
                clip_files = future.result()
                all_clip_files.extend(clip_files)
            except Exception as e:
                logging.error(f"Error processing '{video}': {e}")
    
    logging.info(f"Total candidate loud segments detected across videos: {global_loud_count}")
    
    if all_clip_files:
        concatenate_clips(all_clip_files, args.final_video)
    else:
        logging.info("No loud clips detected. Final video not created.")
    
    logging.info("Processing complete.")

if __name__ == "__main__":
    main()
