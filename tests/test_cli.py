"""Tests for kiln.cli — doctor, status, run, serve --once."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from kiln import cli
from kiln.steps import _ffmpeg

_SAMPLE = Path(__file__).parent / "fixtures" / "sample.mp4"


def _write_config(tmp_path: Path, masters: str = "", exports: str = "") -> Path:
    for sub in ("inbox", "scratch", "state"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    body = f"""
inbox = "{tmp_path}/inbox"
scratch_dir = "{tmp_path}/scratch"
state_dir = "{tmp_path}/state"
masters_archive = "{masters}"
exports_archive = "{exports}"
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(body)
    return cfg


def test_doctor_runs_and_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _write_config(tmp_path)
    rc = cli.main(["--config", str(cfg), "doctor"])
    out = capsys.readouterr().out
    assert rc == 0  # inbox/scratch/state all writable; archives unset is fine
    assert "GPU" in out or "gpu" in out
    assert "masters_archive" in out
    assert "exports_archive" in out
    # Unset archives are reported as such, not as errors.
    assert "unset" in out.lower()


def test_run_enqueues_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _write_config(tmp_path)
    job = tmp_path / "drop" / "myvid"
    job.mkdir(parents=True)
    (job / "job.json").write_text("{}")
    (job / "master.mov").write_bytes(b"x")
    rc = cli.main(["--config", str(cfg), "run", str(job)])
    assert rc == 0
    assert "myvid" in capsys.readouterr().out
    assert (tmp_path / "state" / "queued" / "myvid").is_dir()


def test_status_reports_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _write_config(tmp_path)
    # Seed one queued job.
    q = tmp_path / "state" / "queued" / "seed"
    q.mkdir(parents=True)
    rc = cli.main(["--config", str(cfg), "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "queued" in out
    assert "seed" in out


@pytest.mark.skipif(not _ffmpeg.have_ffmpeg(), reason="ffmpeg required")
def test_serve_once_processes_a_drop(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, masters=str(tmp_path / "m"), exports=str(tmp_path / "e"))
    folder = tmp_path / "inbox" / "oneshot"
    folder.mkdir(parents=True)
    # Request only the headless ffmpeg steps and use the real sample clip.
    folder_job = {"job_id": "oneshot", "jobs": {"transcode": True, "normalize": True}}
    (folder / "job.json").write_text(json.dumps(folder_job))
    shutil.copyfile(_SAMPLE, folder / "master.mp4")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))

    rc = cli.main(["--config", str(cfg), "serve", "--once"])
    assert rc == 0
    assert (tmp_path / "m" / "oneshot" / "master.mp4").exists()
    assert (tmp_path / "e" / "oneshot" / "upload.mp4").exists()
