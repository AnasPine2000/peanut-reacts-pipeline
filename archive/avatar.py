#!/usr/bin/env python3
import os
import math
import pysrt
import numpy as np
from moviepy.editor import (VideoFileClip, ImageClip, TextClip, 
                            CompositeVideoClip, VideoClip)

def parse_srt(srt_path):
    """
    Parse the subtitles from an SRT file.
    Returns a list of tuples: (start_time_sec, end_time_sec, text)
    """
    subs = pysrt.open(srt_path)
    subtitle_entries = []
    for sub in subs:
        start_seconds = sub.start.hours * 3600 + sub.start.minutes * 60 + sub.start.seconds + sub.start.milliseconds/1000
        end_seconds = sub.end.hours * 3600 + sub.end.minutes * 60 + sub.end.seconds + sub.end.milliseconds/1000
        subtitle_entries.append((start_seconds, end_seconds, sub.text))
    return subtitle_entries

def get_subtitle_text(t, subtitle_entries):
    """
    For a given time t (in seconds), return the subtitle text active at that time.
    """
    for (start, end, text) in subtitle_entries:
        if start <= t < end:
            return text
    return ""

def compute_audio_amplitudes(audio_clip, fps=10):
    """
    Compute RMS amplitudes of the audio clip, sampling at 'fps' times per second.
    Returns a list of amplitude values.
    """
    duration = audio_clip.duration
    n_samples = int(duration * fps)
    amplitudes = []

    for i in range(n_samples):
        start_t = i / fps
        end_t = min((i + 1) / fps, duration)
        # Extract audio as a numpy array [samples, channels]
        audio_array = audio_clip.subclip(start_t, end_t).to_soundarray(nbytes=4, fps=44100)
        rms = np.sqrt(np.mean(audio_array**2))
        amplitudes.append(rms)
    return amplitudes

def make_avatar_frame(t, avatar_normal, avatar_open, subtitle_entries, volume_array, fps=10):
    """
    Generate a single frame for the animated avatar:
      - Chooses the avatar image (normal or reacting) based on the audio volume.
      - Overlays the active subtitle text if available.
    """
    # Determine the audio sample index
    index = int(math.floor(t * fps))
    if index >= len(volume_array):
        index = len(volume_array) - 1
    amplitude = volume_array[index]

    # Basic threshold to choose the avatar state
    threshold = 0.02  # Adjust this value as needed.
    if amplitude > threshold:
        avatar_img = avatar_open.get_frame(t)
    else:
        avatar_img = avatar_normal.get_frame(t)

    # Get the subtitle text for the current time
    subtitle_text = get_subtitle_text(t, subtitle_entries)
    if subtitle_text:
        txt_clip = (TextClip(txt=subtitle_text,
                             fontsize=40,
                             color='white',
                             size=(800, None),
                             method='caption')
                    .set_duration(0.1)
                    .set_pos(("center", "bottom")))
        subtitle_frame = txt_clip.get_frame(t)
    else:
        subtitle_frame = np.zeros((avatar_img.shape[0], avatar_img.shape[1], 4), dtype=avatar_img.dtype)

    # Ensure the avatar image has an alpha channel.
    if avatar_img.shape[2] == 3:
        alpha_channel = np.ones((avatar_img.shape[0], avatar_img.shape[1], 1), dtype=avatar_img.dtype) * 255
        avatar_img = np.concatenate((avatar_img, alpha_channel), axis=2)

    # Overlay the subtitle (if any) onto the avatar image.
    if subtitle_frame.shape[0] == avatar_img.shape[0] and subtitle_frame.shape[1] == avatar_img.shape[1]:
        alpha_sub = subtitle_frame[:,:,3] / 255.0
        for c in range(3):  # For R, G, B channels
            avatar_img[:,:,c] = avatar_img[:,:,c]*(1 - alpha_sub) + subtitle_frame[:,:,c]*alpha_sub

    return avatar_img

def main():
    # Input file paths (adjust if necessary)
    video_path = r"downloads\DUMBEST SIDEMEN AMONG US EVER.webm"
    srt_path = r"downloads\DUMBEST SIDEMEN AMONG US EVER.en.srt"
    avatar_normal_path = "avatar_normal.png"
    avatar_open_path = "avatar_open.png"
    output_path = "output_animated.mp4"

    # Parse subtitles from SRT file.
    subtitle_entries = parse_srt(srt_path)

    # Load the main video and extract its audio.
    video_clip = VideoFileClip(video_path)
    audio_clip = video_clip.audio

    # Compute audio amplitudes (sampled 10 times per second).
    fps_audio_analysis = 10
    volume_array = compute_audio_amplitudes(audio_clip, fps_audio_analysis)

    # Load avatar images as MoviePy ImageClips.
    avatar_normal_clip = ImageClip(avatar_normal_path).set_duration(video_clip.duration)
    avatar_open_clip = ImageClip(avatar_open_path).set_duration(video_clip.duration)

    # Create an animated avatar clip using our make_frame function.
    def make_frame(t):
        return make_avatar_frame(t, avatar_normal_clip, avatar_open_clip,
                                 subtitle_entries, volume_array, fps=fps_audio_analysis)

    avatar_animated_clip = VideoClip(make_frame, duration=video_clip.duration)

    # Resize the avatar clip to form a smaller reaction window (e.g., 30% of video width)
    avatar_window = avatar_animated_clip.resize(width=int(video_clip.w * 0.3))
    # Position the avatar window (e.g., bottom right corner)
    avatar_window = avatar_window.set_position(("right", "bottom"))

    # Create a composite video with the original video as background and the avatar overlay.
    composite = CompositeVideoClip([video_clip, avatar_window])
    composite = composite.set_audio(audio_clip)

    # Export the final video.
    composite.write_videofile(output_path, fps=video_clip.fps)
    print(f"Done! Output saved at: {output_path}")

if __name__ == "__main__":
    main()
