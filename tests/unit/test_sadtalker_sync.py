"""Unit tests for peanut_reacts.character.sadtalker_sync (no GPU required).

We mock subprocess everywhere — real SadTalker rendering is covered by the
`-m gpu` suite that only runs on the dev machine.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from peanut_reacts.character.sadtalker_sync import (
    SadTalkerNoFaceError,
    _default_sadtalker_home,
    _default_sadtalker_python,
    _ensure_wav,
    sadtalker_available,
)


def test_default_python_respects_env_var(monkeypatch):
    monkeypatch.setenv("SADTALKER_PYTHON", "/custom/path/python")
    # Path normalizes separators per-OS; compare as Path, not string.
    assert _default_sadtalker_python() == Path("/custom/path/python")


def test_default_home_respects_env_var(monkeypatch):
    monkeypatch.setenv("SADTALKER_HOME", "/other/sadtalker")
    assert _default_sadtalker_home() == Path("/other/sadtalker")


def test_sadtalker_available_false_when_python_missing(monkeypatch):
    monkeypatch.setenv("SADTALKER_PYTHON", "/nonexistent/python.exe")
    monkeypatch.setenv("SADTALKER_HOME", "/nonexistent/sadtalker")
    assert sadtalker_available() is False


def test_sadtalker_available_false_when_home_missing(tmp_path, monkeypatch):
    # Python exists (use sys.executable), home doesn't
    import sys
    monkeypatch.setenv("SADTALKER_PYTHON", sys.executable)
    monkeypatch.setenv("SADTALKER_HOME", str(tmp_path / "nonexistent"))
    assert sadtalker_available() is False


def test_ensure_wav_returns_input_when_already_wav(tmp_path):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"FAKE_WAV_DATA")
    out = _ensure_wav(wav, tmp_path / "cache")
    assert out == wav


@patch("peanut_reacts.character.sadtalker_sync.subprocess.run")
def test_ensure_wav_converts_mp3(mock_run, tmp_path):
    mp3 = tmp_path / "audio.mp3"
    mp3.write_bytes(b"FAKE_MP3_DATA")
    cache_dir = tmp_path / "cache"

    # Simulate successful ffmpeg conversion by creating the expected output
    def fake_run(cmd, **kwargs):
        # Extract the last arg (output path) and create a fake wav
        Path(cmd[-1]).write_bytes(b"WAV")
        return MagicMock(returncode=0)

    mock_run.side_effect = fake_run
    out = _ensure_wav(mp3, cache_dir)
    assert out.exists()
    assert out.suffix == ".wav"
    mock_run.assert_called_once()
    # First arg must be ffmpeg
    assert mock_run.call_args[0][0][0] == "ffmpeg"


def test_sadtalker_no_face_error_is_runtime_error():
    """Error subclasses RuntimeError so general exception handlers still catch it
    while specific handlers can detect it separately."""
    try:
        raise SadTalkerNoFaceError("test")
    except RuntimeError as e:
        assert "test" in str(e)
