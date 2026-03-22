import subprocess
from pathlib import Path

BASE_VIDEO = r"downloads\1 - W2S finally gets an IMPOSTER WIN on AMONG US.mkv"
GIF_PATH   = r"C:\Users\anasm\Videos\4K Video Downloader+\output\peanut.gif"
OUT_FILE   = r"peanut reacts to 224.mp4"

# Tweak these:
SCALE = 1.25                 # overlay width as fraction of base width
X = "W-w-40"                 # position expression
Y = "H-h-40"

vf = (
    f"[1:v]format=rgba[gif];"
    f"[gif][0:v]scale2ref=w=main_w*{SCALE}:h=-1[ov][base];"
    f"[base][ov]overlay=x={X}:y={Y}:format=auto[v]"
)

cmd = [
    "ffmpeg", "-y",
    "-i", BASE_VIDEO,
    "-stream_loop", "-1", "-i", GIF_PATH,
    "-filter_complex", vf,
    "-map", "[v]",
    "-map", "0:a?",
    "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "192k",
    "-shortest",
    OUT_FILE,
]

print("Running FFmpeg:\n", " ".join(cmd))
subprocess.run(cmd, check=True)
print(f"\nDone: {OUT_FILE}")
