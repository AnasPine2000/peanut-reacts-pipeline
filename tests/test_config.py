"""Tests for peanut_reacts.core.config."""

from __future__ import annotations

from pathlib import Path

from peanut_reacts.core.config import PeanutConfig, _load_dotenv, load_config


class TestLoadDotenv:
    def test_parses_key_value(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=123\n", encoding="utf-8")
        result = _load_dotenv(env_file)
        assert result == {"FOO": "bar", "BAZ": "123"}

    def test_skips_comments_and_blanks(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nKEY=val\n", encoding="utf-8")
        result = _load_dotenv(env_file)
        assert result == {"KEY": "val"}

    def test_strips_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text('KEY="quoted"\nKEY2=\'single\'\n', encoding="utf-8")
        result = _load_dotenv(env_file)
        assert result["KEY"] == "quoted"
        assert result["KEY2"] == "single"

    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert _load_dotenv(tmp_path / "nope") == {}

    def test_skips_empty_values(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("EMPTY=\nFILLED=yes\n", encoding="utf-8")
        result = _load_dotenv(env_file)
        assert "EMPTY" not in result
        assert result["FILLED"] == "yes"


class TestPeanutConfig:
    def test_defaults(self):
        cfg = PeanutConfig()
        assert cfg.llm_provider == "deepseek"
        assert cfg.tts_voice == "en-US-GuyNeural"
        assert cfg.facecam_scale == 0.22
        assert cfg.max_reactions == 15

    def test_load_from_dotenv(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("YOUTUBE_API_KEY=test123\n", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        cfg = load_config(tmp_path)
        assert cfg.youtube_api_key == "test123"

    def test_invalid_float_falls_back(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("LLM_TEMPERATURE=not_a_number\n", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        cfg = load_config(tmp_path)
        assert cfg.llm_temperature == 0.8  # falls back to default

    def test_invalid_int_falls_back(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("MAX_REACTIONS=abc\n", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        cfg = load_config(tmp_path)
        assert cfg.max_reactions == 15  # falls back to default
