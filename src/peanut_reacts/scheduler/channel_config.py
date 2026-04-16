"""Multi-channel configuration loader from channels.yaml."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

@dataclass
class ChannelConfig:
    id: str
    name: str
    character: str
    source_type: str  # playlist | twitch_vod | lofi | reddit
    schedule: str  # cron expression

    source_urls: list[str] = field(default_factory=list)
    subreddits: list[str] = field(default_factory=list)
    tts_voice: str = "en-US-GuyNeural"
    tts_rate: str = "+10%"
    llm_personality: str = ""
    reactions_per_minute: float = 6.0
    gameplay_volume: float = 0.12
    reaction_boost: float = 5.0
    oauth_token: str = ""
    client_secrets: str = ""
    upload_privacy: str = "public"
    max_videos_per_run: int = 2
    max_segments_per_run: int = 5
    video_format: str = "standard"  # standard | compilation
    compilation_hours: float = 5.0
    tags: list[str] = field(default_factory=list)

    # Twitch-specific
    segment_by_topic: bool = False
    min_segment_minutes: int = 10
    max_segment_minutes: int = 30

    # Lofi-specific
    stream_24_7: bool = False
    stream_rtmp_url: str = ""
    stream_key: str = ""
    music_source: str = "ai_generated"
    tracks_per_mix: int = 15
    mix_duration_minutes: int = 120

    # Reddit-specific
    min_score: int = 5000
    stories_per_video: int = 3

    # Ambient-specific
    target_hours: float = 8.0
    themes: list[str] = field(default_factory=list)
    use_ai_visuals: bool = True
    spotify_distribute: bool = False


@dataclass
class GlobalSettings:
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.85
    ffmpeg_encoder: str = "h264_nvenc"
    ffmpeg_fallback: str = "libx264"
    output_dir: str = "production"
    db_path: str = "pipeline.db"
    log_dir: str = "logs"
    discord_webhook_url: str = ""
    status_push_interval_minutes: int = 5
    hf_space_repo: str = ""


@dataclass
class PipelineConfig:
    channels: list[ChannelConfig]
    settings: GlobalSettings


def load_channels(config_path: Optional[Path] = None) -> PipelineConfig:
    if config_path is None:
        for p in [Path("channels.yaml"), Path(__file__).resolve().parents[3] / "channels.yaml"]:
            if p.exists():
                config_path = p
                break
    if config_path is None or not config_path.exists():
        raise FileNotFoundError(f"channels.yaml not found (tried {config_path})")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    channels = []
    for ch in raw.get("channels", []):
        known = {f.name for f in ChannelConfig.__dataclass_fields__.values()}
        filtered = {k: v for k, v in ch.items() if k in known}
        channels.append(ChannelConfig(**filtered))

    settings_raw = raw.get("settings", {})
    known_s = {f.name for f in GlobalSettings.__dataclass_fields__.values()}
    settings = GlobalSettings(**{k: v for k, v in settings_raw.items() if k in known_s})

    log.info("Loaded %d channels from %s", len(channels), config_path)
    return PipelineConfig(channels=channels, settings=settings)


def get_channel(config: PipelineConfig, channel_id: str) -> ChannelConfig:
    for ch in config.channels:
        if ch.id == channel_id:
            return ch
    raise ValueError(f"Channel '{channel_id}' not found in config")
