import os
import subprocess

def download_subtitles(playlist_url, output_dir="subtitles"):
    # Create output directory if it doesn't exist
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Extract video URLs from the playlist
    print("Fetching video URLs from the playlist...")
    command = [
        "yt-dlp",
        "--flat-playlist",
        "--print", "%(url)s",
        playlist_url
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    
    video_urls = result.stdout.strip().split("\n")
    if not video_urls:
        print("No videos found in the playlist.")
        return

    print(f"Found {len(video_urls)} videos. Downloading subtitles...")

    # Download subtitles for each video
    for video_url in video_urls:
        print(f"Downloading subtitles for: {video_url}")
        subprocess.run([
            "yt-dlp",
            "--write-sub",             # Download subtitles if available
            "--write-auto-sub",        # Fallback to auto-generated subtitles if necessary
            "--sub-lang", "en",        # Change language if needed
            "--convert-subs", "srt",   # Convert subtitles to SRT format
            "--skip-download",         # Do NOT download the video
            "writethumbnail", True,
            "write_thumbnail", True,
            "-o", f"{output_dir}/%(title)s.%(ext)s",
            video_url
        ])

    print("Subtitles download completed!")


# Example usage
playlist_url = r"https://www.youtube.com/playlist?list=PL6_iWvoCGAJmEIgg6LoDGSWZ_7quqcVG0"
download_subtitles(playlist_url)
