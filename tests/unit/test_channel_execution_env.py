"""Tests for the execution_env filter in ChannelConfig + scheduler.engine."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────
# ChannelConfig default value
# ─────────────────────────────────────────────────────────────────────

def test_channel_config_defaults_to_vps():
    from peanut_reacts.scheduler.channel_config import ChannelConfig
    c = ChannelConfig(id="x", name="x", character="x",
                      source_type="playlist", schedule="0 0 * * *")
    assert c.execution_env == "vps"


def test_channel_config_accepts_local():
    from peanut_reacts.scheduler.channel_config import ChannelConfig
    c = ChannelConfig(id="x", name="x", character="x",
                      source_type="playlist", schedule="0 0 * * *",
                      execution_env="local")
    assert c.execution_env == "local"


# ─────────────────────────────────────────────────────────────────────
# load_channels respects the yaml field
# ─────────────────────────────────────────────────────────────────────

def test_load_channels_respects_execution_env(tmp_path):
    from peanut_reacts.scheduler.channel_config import load_channels

    yaml = """channels:
  - id: cloud_ch
    name: Cloud
    character: x
    source_type: playlist
    schedule: "0 0 * * *"
    execution_env: vps
  - id: local_ch
    name: Local
    character: x
    source_type: playlist
    schedule: "0 0 * * *"
    execution_env: local
  - id: default_ch
    name: Default
    character: x
    source_type: playlist
    schedule: "0 0 * * *"
"""
    yaml_path = tmp_path / "channels.yaml"
    yaml_path.write_text(yaml, encoding="utf-8")
    cfg = load_channels(yaml_path)

    by_id = {c.id: c for c in cfg.channels}
    assert by_id["cloud_ch"].execution_env == "vps"
    assert by_id["local_ch"].execution_env == "local"
    assert by_id["default_ch"].execution_env == "vps"   # default


# ─────────────────────────────────────────────────────────────────────
# engine.setup() skips channels not matching PEANUT_SCHEDULER_ENV
# ─────────────────────────────────────────────────────────────────────

def _make_channel(id_, env):
    from peanut_reacts.scheduler.channel_config import ChannelConfig
    return ChannelConfig(id=id_, name=id_.upper(), character="x",
                         source_type="playlist",
                         schedule="0 0 * * *",
                         execution_env=env)


def _fake_pipeline_config(channels):
    from peanut_reacts.scheduler.channel_config import PipelineConfig, GlobalSettings
    return PipelineConfig(channels=channels, settings=GlobalSettings())


@patch("peanut_reacts.scheduler.engine.BackgroundScheduler")
def test_engine_only_fires_matching_env_vps(mock_scheduler_cls, tmp_path, monkeypatch):
    from peanut_reacts.scheduler.engine import PipelineEngine
    from peanut_reacts.scheduler.db import PipelineDB

    monkeypatch.setenv("PEANUT_SCHEDULER_ENV", "vps")

    mock_sched = MagicMock()
    mock_scheduler_cls.return_value = mock_sched

    cfg = _fake_pipeline_config([
        _make_channel("vps_only", "vps"),
        _make_channel("local_only", "local"),
    ])
    db = PipelineDB(tmp_path / "test.db")
    engine = PipelineEngine(cfg, db)
    engine.setup()

    # Collect which channel_ids got add_job'd
    scheduled = [
        call.kwargs.get("id", "")
        for call in mock_sched.add_job.call_args_list
    ]
    assert "pipeline_vps_only" in scheduled
    assert "pipeline_local_only" not in scheduled


@patch("peanut_reacts.scheduler.engine.BackgroundScheduler")
def test_engine_only_fires_matching_env_local(mock_scheduler_cls, tmp_path, monkeypatch):
    from peanut_reacts.scheduler.engine import PipelineEngine
    from peanut_reacts.scheduler.db import PipelineDB

    monkeypatch.setenv("PEANUT_SCHEDULER_ENV", "local")

    mock_sched = MagicMock()
    mock_scheduler_cls.return_value = mock_sched

    cfg = _fake_pipeline_config([
        _make_channel("vps_only", "vps"),
        _make_channel("local_only", "local"),
    ])
    db = PipelineDB(tmp_path / "test.db")
    engine = PipelineEngine(cfg, db)
    engine.setup()

    scheduled = [
        call.kwargs.get("id", "")
        for call in mock_sched.add_job.call_args_list
    ]
    assert "pipeline_local_only" in scheduled
    assert "pipeline_vps_only" not in scheduled


@patch("peanut_reacts.scheduler.engine.BackgroundScheduler")
def test_engine_defaults_to_vps_when_env_unset(mock_scheduler_cls, tmp_path, monkeypatch):
    from peanut_reacts.scheduler.engine import PipelineEngine
    from peanut_reacts.scheduler.db import PipelineDB

    monkeypatch.delenv("PEANUT_SCHEDULER_ENV", raising=False)

    mock_sched = MagicMock()
    mock_scheduler_cls.return_value = mock_sched

    cfg = _fake_pipeline_config([
        _make_channel("vps_only", "vps"),
        _make_channel("local_only", "local"),
    ])
    db = PipelineDB(tmp_path / "test.db")
    engine = PipelineEngine(cfg, db)
    engine.setup()

    scheduled = [
        call.kwargs.get("id", "")
        for call in mock_sched.add_job.call_args_list
    ]
    # Default is "vps" — so only vps_only should fire
    assert "pipeline_vps_only" in scheduled
    assert "pipeline_local_only" not in scheduled
