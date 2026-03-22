import os
import sys
import yt_dlp

def main():
    # Either get channel URL from command line or prompt user

    channel_url = r"https://www.youtube.com/@Sidemen"
    
    # Define folder name explicitly
    folder_name = "Sidemen Sundays"
    
    # Create the folder if it doesn't already exist
    if not os.path.exists(folder_name):
        os.makedirs(folder_name)

    # Define yt-dlp options
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        
        # Enable subtitles
        'writesubtitles': True,
        'writeautomaticsub': True,
        
        # Request .vtt but add a postprocessor that auto-converts to .srt
        'subtitlesformat': 'vtt',
        
        # If any download fails or a video is removed, skip it and continue
        'ignoreerrors': True,
        'playlistend': None,

        # Convert subtitles from .vtt to .srt (requires ffmpeg)
        'postprocessors': [
            {
                'key': 'FFmpegSubtitlesConvertor',
                'format': 'srt'
            }
        ],

        # Force all downloads into "Sidemen Sundays" folder
        'outtmpl': os.path.join(folder_name, '%(title)s.%(ext)s'),
    }

    # Download from the provided URL
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([channel_url])

if __name__ == "__main__":
    main()
