# Peanut Reacts

Automated YouTube reaction video generator with an animated peanut character. Give it a YouTube URL and it produces a reaction video where a peanut character comments on the best moments — driven by real viewer comments and AI.

## How It Works

```
YouTube URL
    │
    ├── yt-dlp downloads video + subtitles
    ├── YouTube API fetches top 500 comments
    ├── Timestamp clustering finds the best moments
    ├── LLM generates witty reactions at those moments
    ├── Edge TTS synthesizes speech with word-level timing
    ├── Peanut character animates with emotion-specific expressions
    └── FFmpeg composites facecam overlay + audio ducking
            │
            └── Final reaction video (.mp4)
```

## Quick Start

### 1. Install dependencies

```bash
pip install -e .
```

Requires Python 3.10+ and FFmpeg installed on your system.

### 2. Configure API keys

Copy `.env.example` to `.env` and add your keys:

```bash
cp .env.example .env
```

```env
YOUTUBE_API_KEY=your_youtube_data_api_v3_key
DEEPSEEK_API_KEY=your_deepseek_api_key
```

### 3. Generate a reaction video

**From a YouTube URL (one command does everything):**

```bash
peanut-react "https://www.youtube.com/watch?v=VIDEO_ID" -v
```

**From a local video file:**

```bash
peanut-react video.mp4 --info-json comments.info.json -v
```

## Configuration

Settings are loaded from (highest priority first):

1. CLI flags
2. Environment variables
3. `.env` file in project root
4. `config.yaml` in project root
5. Built-in defaults

### `.env` (API keys)

```env
YOUTUBE_API_KEY=...          # YouTube Data API v3
DEEPSEEK_API_KEY=...         # LLM provider
GROQ_API_KEY=...             # Alternative LLM
YTDLP_COOKIES_FILE=...      # Path to cookies.txt for age-restricted videos
```

### `config.yaml` (optional defaults)

```yaml
llm_provider: deepseek
tts_voice: en-US-GuyNeural
tts_rate: "+10%"
facecam_scale: 0.22
facecam_position: bottom-right
name_tag: PEANUT
max_reactions: 15
max_comments: 500
whisper_model: base
```

## CLI Commands

### Main command

| Command | Description |
|---|---|
| `peanut-react` | Full reaction video pipeline (one-click from URL) |

### Download tools

| Command | Description |
|---|---|
| `peanut-fetch-comments` | Fetch comments via YouTube Data API v3 |
| `peanut-download-comments` | Fetch comments via yt-dlp (needs cookies) |
| `peanut-download-playlist` | Download all videos from a playlist |
| `peanut-download-channel` | Download all videos from a channel |
| `peanut-download-subtitles` | Download subtitles for a playlist |
| `peanut-download-thumbnails` | Download thumbnails for a playlist |

### Analysis tools

| Command | Description |
|---|---|
| `peanut-extract-comments` | Extract timestamp highlights from .info.json |
| `peanut-search-keywords` | Search transcripts for keywords |
| `peanut-detect-loud` | Find loud/exciting moments in audio |
| `peanut-segment-topics` | Segment video by topic changes |

### Reaction pipeline tools

| Command | Description |
|---|---|
| `peanut-generate-reactions` | Generate LLM reaction script |
| `peanut-synthesize-tts` | Synthesize TTS audio with word timing |
| `peanut-render-synced` | Render speech-synced peanut animation |
| `peanut-render-character` | Render standalone peanut animation |
| `peanut-overlay` | Overlay GIF/video on base video |
| `peanut-concat` | Concatenate video clips |

## Progress Tracking

The pipeline automatically tracks which videos have been processed in `peanut_progress.json`. Re-running the same URL skips already-completed videos.

## Cookies for Age-Restricted Videos

If yt-dlp fails to download a video (sign-in required), export cookies from your browser:

1. Install the "Get cookies.txt LOCALLY" browser extension
2. Go to youtube.com while logged in
3. Export cookies to a file
4. Set the path in `.env`:

```env
YTDLP_COOKIES_FILE=C:/path/to/cookies.txt
```

## Architecture

```
src/peanut_reacts/
    core/           Shared utilities (ffmpeg, config, logging, progress)
    download/       YouTube video + comment downloading
    analysis/       Comment extraction, keyword search, loudness, topics
    character/      Peanut renderer, LLM reactions, TTS, speech sync
    compositing/    Video layout, overlay, concatenation, final pipeline
    cli/            17 CLI entry points
```

## License

Private project.
