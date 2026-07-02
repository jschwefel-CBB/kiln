"""Tests for kiln.config — TOML loading, validation, env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from kiln.config import Config, load


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body)
    return p


MINIMAL = """
inbox = "{d}/inbox"
scratch_dir = "{d}/scratch"
state_dir = "{d}/state"
masters_archive = ""
exports_archive = ""
"""


def test_load_minimal(tmp_path: Path) -> None:
    cfg = load(_write(tmp_path, MINIMAL.format(d=tmp_path)))
    assert isinstance(cfg, Config)
    assert cfg.inbox == tmp_path / "inbox"
    assert cfg.scratch_dir == tmp_path / "scratch"
    assert cfg.state_dir == tmp_path / "state"
    # Empty-string archive paths mean "not configured".
    assert cfg.masters_archive is None
    assert cfg.exports_archive is None
    # Defaults applied.
    assert cfg.whisper_model == "auto"
    assert cfg.llm_model == "llama3.1:8b"
    assert cfg.codec == "auto"
    assert cfg.target_lufs == -14
    assert cfg.prune_masters is False
    assert cfg.retention_days == 90
    assert cfg.submit_host == "127.0.0.1"
    assert cfg.submit_port == 8765


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KILN_INBOX", str(tmp_path / "override-inbox"))
    monkeypatch.setenv("KILN_TARGET_LUFS", "-16")
    cfg = load(_write(tmp_path, MINIMAL.format(d=tmp_path)))
    assert cfg.inbox == tmp_path / "override-inbox"
    assert cfg.target_lufs == -16


def test_configured_archives(tmp_path: Path) -> None:
    body = MINIMAL.format(d=tmp_path).replace(
        'masters_archive = ""', f'masters_archive = "{tmp_path}/m"'
    ).replace(
        'exports_archive = ""', f'exports_archive = "{tmp_path}/e"'
    )
    cfg = load(_write(tmp_path, body))
    assert cfg.masters_archive == tmp_path / "m"
    assert cfg.exports_archive == tmp_path / "e"


def test_missing_required_raises(tmp_path: Path) -> None:
    body = 'scratch_dir = "{d}/s"\nstate_dir = "{d}/st"\nmasters_archive = ""\nexports_archive = ""\n'.format(d=tmp_path)
    with pytest.raises(ValueError, match="inbox"):
        load(_write(tmp_path, body))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "does-not-exist.toml")
