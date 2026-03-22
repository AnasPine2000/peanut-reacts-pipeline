import os
import random
import uuid
import subprocess

def is_valid_video(video_path):
    """
    Check if the video file is valid by probing it with ffprobe.
    This function verifies that the file contains a valid video stream.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",  # check only the first video stream
        "-show_entries", "stream=codec_name",
        "-of", "default=nw=1:nk=1",
        video_path
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        # If ffprobe returns any output (codec name) it is likely valid.
        if result.stdout.strip():
            return True
        return False
    except subprocess.CalledProcessError:
        return False
    except Exception as e:
        print(f"Error while validating {video_path}: {e}")
        return False

def concatenate_video_files(video_paths, output_file):
    """
    Concatenate a list of video files into a single video using ffmpeg's concat demuxer.
    The list of files is randomized and written to a temporary file that ffmpeg uses.
    """
    # Shuffle the video paths randomly.
    random.shuffle(video_paths)

    # Create a temporary file list for the ffmpeg concat demuxer.
    list_file = f"{uuid.uuid4()}.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for video in video_paths:
            # Escape single quotes in the absolute path.
            abs_path = os.path.abspath(video).replace("'", "'\\''")
            f.write(f"file '{abs_path}'\n")

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

    # Clean up the temporary list file.
    os.remove(list_file)

def main():
    # Set up your parameters.
    key = "ksi"
    keyword = key + "newone"
    # Update your input folder path as needed.
    input_folder = r'C:\Users\anasm\Videos\4K Video Downloader+\downloads\KSIAMONGUS'
    final_output = keyword + ".mkv"

    # Define allowed video file extensions.
    allowed_extensions = (".mkv", ".webm", ".mp4", ".avi")
    # Gather all video files from the input folder that match the allowed extensions.
    video_files = [
        os.path.join(input_folder, f)
        for f in os.listdir(input_folder)
        if f.lower().endswith(allowed_extensions)
    ]

    if not video_files:
        print("No video files found in the input folder.")
        return

    # Validate each video file using ffprobe.
    valid_videos = []
    for video in video_files:
        if is_valid_video(video):
            valid_videos.append(video)
        else:
            print(f"Skipping invalid video file: {video}")

    if not valid_videos:
        print("No valid video files to process after validation.")
        return

    print("Concatenating valid video files without re-encoding...")

    # Concatenate all valid video files into the final output.
    concatenate_video_files(valid_videos, final_output)

    print(f"\nDONE! Final video saved to: {final_output}")

if __name__ == "__main__":
    main()
