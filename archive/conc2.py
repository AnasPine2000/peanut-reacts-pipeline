import os
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

def reencode_to_mp4_nvenc(input_file, output_file, video_bitrate="5M", audio_bitrate="192k"):
    """
    Re-encode a single WebM file to MP4 using GPU-accelerated (NVENC) H.264 encoding.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-hwaccel", "cuda",
        "-i", input_file,
        "-c:v", "h264_nvenc",
        "-preset", "fast",
        "-b:v", video_bitrate,
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        output_file
    ]
    subprocess.run(cmd, check=True)

def parallel_reencode(input_folder, temp_folder, workers=4):
    """
    Re-encode all .webm files in 'input_folder' to .mp4 in 'temp_folder'
    using ThreadPoolExecutor (parallel GPU encodes).
    """
    if not os.path.exists(temp_folder):
        os.makedirs(temp_folder)

    # Gather all WebM files
    webm_files = sorted(
        f for f in os.listdir(input_folder) if f.lower().endswith(".webm")
    )
    if not webm_files:
        print("No .webm files found in the input folder.")
        return []

    encoded_files = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_file = {}
        for i, webm_name in enumerate(webm_files, start=1):
            in_path = os.path.join(input_folder, webm_name)
            out_name = os.path.splitext(webm_name)[0] + ".mp4"
            out_path = os.path.join(temp_folder, out_name)
            future = executor.submit(reencode_to_mp4_nvenc, in_path, out_path)
            future_to_file[future] = webm_name

        total = len(webm_files)
        completed = 0

        for future in as_completed(future_to_file):
            original_name = future_to_file[future]
            try:
                future.result()  # Raises if ffmpeg failed
                completed += 1
                print(f"[{completed}/{total}] Encoded: {original_name}")
                mp4_name = os.path.splitext(original_name)[0] + ".mp4"
                encoded_files.append(os.path.join(temp_folder, mp4_name))
            except Exception as e:
                print(f"Failed to encode {original_name}: {e}")

    return encoded_files

def concatenate_with_transitions(mp4_paths, transition_path, output_file):
    """
    Concatenate MP4 files with a transition video inserted between each main clip.
    Ensure all videos (main clips and transition) share the same encoding parameters.
    """
    list_file = f"{uuid.uuid4()}.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for i, mp4 in enumerate(mp4_paths):
            f.write(f"file '{os.path.abspath(mp4)}'\n")
            # Insert the transition clip between main clips, except after the last one.
            if i < len(mp4_paths) - 1:
                f.write(f"file '{os.path.abspath(transition_path)}'\n")
    
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        output_file
    ]
    subprocess.run(cmd, check=True)
    os.remove(list_file)

def add_facecam_overlay(main_video, facecam_video, output_file, x=10, y=10):
    """
    Overlays the face cam video over the main video at position (x, y).
    By default, positions the face cam in the top-right corner (with x margin from the right edge).
    Adjust durations or scaling as needed if the face cam and main video lengths differ.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", main_video,
        "-i", facecam_video,
        "-filter_complex", f"[0:v][1:v]overlay=W-w-{x}:{y}",
        "-c:a", "copy",
        output_file
    ]
    subprocess.run(cmd, check=True)

def main():
    # Configure paths for your setup:
    input_folder = r"downloads\keyword_clips"            # Folder containing your .webm files
    temp_folder = r"downloads\keyword_clips\temp"          # Temporary folder for intermediate .mp4 files
    merged_with_transitions = r"merged_with_transitions.mp4" # Output file after concatenation with transitions
    transition_video = r"transition1.mp4"           # Pre-made transition video file
    facecam_video = r"downloads\SIDEMEN $20,000 AMONG US vs MR BEAST.webm"                 # Face cam video file
    final_output = r"final_video.mp4"                      # Final output file with overlay

    print("Re-encoding all .webm files to .mp4 using GPU NVENC in parallel...")
    encoded_files = parallel_reencode(input_folder, temp_folder, workers=4)

    if not encoded_files:
        print("No files encoded. Exiting.")
        return

    print("\nConcatenating all encoded .mp4 files with transitions into final output...")
    concatenate_with_transitions(encoded_files, transition_video, merged_with_transitions)
    print(f"Merged video with transitions saved to: {merged_with_transitions}")

    print("\nOverlaying face cam video onto merged video...")
    add_facecam_overlay(merged_with_transitions, facecam_video, final_output)
    print(f"Final video with face cam overlay saved to: {final_output}")

if __name__ == "__main__":
    main()
