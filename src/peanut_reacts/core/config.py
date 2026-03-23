"""
Centralised configuration loader.

Reads settings from (in priority order):
1. CLI flags (highest priority)
2. Environment variables
3. .env file in project root
4. config.yaml in project root
5. Built-in defaults (lowest priority)

Usage:
    cfg = load_config()
    print(cfg.youtube_api_key)
    print(cfg.llm_provider)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _find_project_root() -> Path:
    """Walk up from CWD or this file to find pyproject.toml."""
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        p = start
        for _ in range(10):
            if (p / "pyproject.toml").exists():
                return p
            if p.parent == p:
                break
            p = p.parent
    return Path.cwd()


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict (simple KEY=VALUE format)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value:
                env[key] = value
    return env


def _load_yaml(path: Path) -> dict:
    """Load a YAML config file (optional dependency)."""
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        # Fall back to simple key: value parsing
        result: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip().strip('"').strip("'")
        return result


@dataclass
class PeanutConfig:
    """Resolved configuration for the peanut-reacts pipeline."""

    # API keys
    youtube_api_key: str = ""
    deepseek_api_key: str = ""
    groq_api_key: str = ""

    # LLM defaults
    llm_provider: str = "deepseek"
    llm_model: str = ""
    llm_temperature: float = 0.8

    # TTS defaults
    tts_voice: str = "en-US-GuyNeural"
    tts_rate: str = "+10%"

    # Layout defaults
    facecam_scale: float = 0.22
    facecam_position: str = "bottom-right"
    name_tag: str = "PEANUT"

    # Download
    cookies_file: str = ""
    output_dir: str = "downloads"
    work_dir: str = "peanut_work"

    # Pipeline
    max_reactions: int = 15
    max_comments: int = 500
    whisper_model: str = "base"
    whisper_device: str = "cuda"

    # Progress tracking
    progress_file: str = "peanut_progress.json"

    # Project root (not configurable, auto-detected)
    project_root: str = ""


def load_config(config_dir: Optional[Path] = None) -> PeanutConfig:
    """Load configuration from .env, config.yaml, and environment variables.

    Returns a PeanutConfig with all values resolved.
    """
    root = Path(config_dir) if config_dir else _find_project_root()

    # Layer 1: defaults (from dataclass)
    cfg = PeanutConfig(project_root=str(root))

    # Layer 2: config.yaml
    yaml_data = _load_yaml(root / "config.yaml")

    # Layer 3: .env file
    dotenv = _load_dotenv(root / ".env")

    # Layer 4: actual environment variables (highest priority)
    def _get(env_key: str, yaml_key: str = "", default: str = "") -> str:
        """Resolve a value from env > .env > yaml > default."""
        return (
            os.environ.get(env_key, "")
            or dotenv.get(env_key, "")
            or yaml_data.get(yaml_key or env_key.lower(), "")
            or default
        )

    cfg.youtube_api_key = _get("YOUTUBE_API_KEY", "youtube_api_key")
    cfg.deepseek_api_key = _get("DEEPSEEK_API_KEY", "deepseek_api_key")
    cfg.groq_api_key = _get("GROQ_API_KEY", "groq_api_key")

    cfg.llm_provider = _get("LLM_PROVIDER", "llm_provider", "deepseek")
    cfg.llm_model = _get("LLM_MODEL", "llm_model", "")
    cfg.llm_temperature = float(_get("LLM_TEMPERATURE", "llm_temperature", "0.8"))

    cfg.tts_voice = _get("TTS_VOICE", "tts_voice", "en-US-GuyNeural")
    cfg.tts_rate = _get("TTS_RATE", "tts_rate", "+10%")

    cfg.facecam_scale = float(_get("FACECAM_SCALE", "facecam_scale", "0.22"))
    cfg.facecam_position = _get("FACECAM_POSITION", "facecam_position", "bottom-right")
    cfg.name_tag = _get("NAME_TAG", "name_tag", "PEANUT")

    cfg.cookies_file = _get("YTDLP_COOKIES_FILE", "cookies_file", "")
    cfg.output_dir = _get("OUTPUT_DIR", "output_dir", "downloads")
    cfg.work_dir = _get("WORK_DIR", "work_dir", "peanut_work")

    cfg.max_reactions = int(_get("MAX_REACTIONS", "max_reactions", "15"))
    cfg.max_comments = int(_get("MAX_COMMENTS", "max_comments", "500"))
    cfg.whisper_model = _get("WHISPER_MODEL", "whisper_model", "base")
    cfg.whisper_device = _get("WHISPER_DEVICE", "whisper_device", "cuda")

    cfg.progress_file = _get("PROGRESS_FILE", "progress_file", "peanut_progress.json")

    return cfg
