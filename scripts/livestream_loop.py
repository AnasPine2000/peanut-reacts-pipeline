"""24/7 looping livestream of Peanut Reacts compilations to YouTube Live.

Loops all compilation videos continuously via FFmpeg RTMP.
Restart-safe: if FFmpeg dies, it restarts automatically.

Usage:
    python scripts/livestream_loop.py
    # Or run in background:
    pythonw scripts/livestream_loop.py
"""
import subprocess
import sys
import time
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/livestream.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("livestream")

# ── Config ──────────────────────────────────────────────────────────
RTMP_URL = "rtmp://a.rtmp.youtube.com/live2"
STREAM_KEY = "8wbg-ktmx-fatp-zjpw-bmpt"
PROJECT = Path(__file__).resolve().parent.parent

# All compilation videos to loop (in order)
VIDEOS = [
    PROJECT / "production" / "mega_reaction" / "MEGA_REACTION_FINAL.mp4",
    PROJECT / "production" / "mega_batch2" / "MEGA_BATCH2_FINAL.mp4",
    PROJECT / "production" / "mega_batch3" / "BATCH3_FINAL.mp4",
    PROJECT / "production" / "mega_batch4" / "BATCH4_FINAL.mp4",
    PROJECT / "production" / "mega_batch5" / "BATCH5_LOUD.mp4",
    PROJECT / "production" / "miniminter_mega" / "MINIMINTER_MEGA_FINAL.mp4",
]

# Filter to existing files only
VIDEOS = [v for v in VIDEOS if v.exists()]


def build_concat_file():
    """Create FFmpeg concat file listing all videos in loop order."""
    concat_path = PROJECT / "production" / "livestream_concat.txt"
    lines = []
    for v in VIDEOS:
        # Use forward slashes for FFmpeg compatibility on Windows
        safe = str(v).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    concat_path.write_text("\n".join(lines), encoding="utf-8")
    return concat_path


def start_stream_single(video_path: Path):
    """Stream a single video to YouTube RTMP."""
    cmd = [
        "ffmpeg",
        "-re",                          # Read at real-time speed
        "-i", str(video_path).replace("\\", "/"),
        "-c:v", "libx264",             # Re-encode for stream compatibility
        "-preset", "veryfast",
        "-maxrate", "4500k",
        "-bufsize", "9000k",
        "-pix_fmt", "yuv420p",
        "-g", "50",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-f", "flv",
        f"{RTMP_URL}/{STREAM_KEY}",
    ]

    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def get_duration(path: Path) -> float:
    """Get video duration in seconds."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0


def main():
    Path("logs").mkdir(exist_ok=True)

    if not VIDEOS:
        log.error("No videos found! Check production/ directory.")
        return

    total_hours = sum(get_duration(v) for v in VIDEOS) / 3600
    log.info("=" * 60)
    log.info("PEANUT REACTS 24/7 LIVESTREAM")
    log.info("Videos: %d files, %.1f hours per loop", len(VIDEOS), total_hours)
    for v in VIDEOS:
        log.info("  - %s (%.1f min)", v.name, get_duration(v) / 60)
    log.info("=" * 60)

    loop_count = 0
    max_loops = 1000

    while loop_count < max_loops:
        log.info("=== Loop #%d starting ===", loop_count)
        for i, video in enumerate(VIDEOS):
            log.info("Playing video %d/%d: %s (%.1f min)",
                     i + 1, len(VIDEOS), video.name, get_duration(video) / 60)

            proc = start_stream_single(video)
            log.info("FFmpeg PID: %d", proc.pid)

            proc.wait()
            exit_code = proc.returncode

            if exit_code != 0:
                stderr = proc.stderr.read().decode("utf-8", errors="replace")[-300:]
                log.warning("FFmpeg exit %d: %s", exit_code, stderr)
                time.sleep(5)
            else:
                log.info("Video %s finished, moving to next...", video.name)
                time.sleep(1)

        loop_count += 1
        log.info("Loop #%d complete. Starting over...", loop_count)

    log.info("Max loops (%d) reached. Stopping.", max_loops)


if __name__ == "__main__":
    main()
