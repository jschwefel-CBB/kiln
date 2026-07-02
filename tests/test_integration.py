"""End-to-end integration: drop -> queue -> run (real transcode+normalize) -> result -> defer -> drain.

Phase 3 wired the real step implementations, so these tests drive the *real* pipeline. To
stay headless (no network, no models, GPU-optional), the integration jobs request only the
two ffmpeg-driven steps — ``transcode`` and ``normalize`` — against the committed sample
clip. Whisper/Ollama/ESRGAN behavior is covered by each step's own unit test module. The
happy-path tests skip cleanly where ffmpeg is unavailable.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from kiln.archiver import drain_pending
from kiln.config import Config
from kiln.queue import ProcessingQueue
from kiln.service import process_one
from kiln.steps import _ffmpeg
from kiln.watcher import scan_once

_SAMPLE = Path(__file__).parent / "fixtures" / "sample.mp4"

# The integration jobs run only the headless ffmpeg steps.
_HEADLESS_JOBS = {"transcode": True, "normalize": True,
                  "captions": False, "chapters": False, "metadata": False, "upscale": False}

requires_ffmpeg = pytest.mark.skipif(not _ffmpeg.have_ffmpeg(), reason="ffmpeg required")


def _config(tmp_path: Path, masters=None, exports=None) -> Config:
    cfg = Config(
        inbox=tmp_path / "inbox",
        scratch_dir=tmp_path / "scratch",
        state_dir=tmp_path / "state",
        masters_archive=masters,
        exports_archive=exports,
        jobs=dict(_HEADLESS_JOBS),
    )
    for d in (cfg.inbox, cfg.scratch_dir, cfg.state_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


def _drop_master(inbox: Path, job_id: str) -> Path:
    """Drop the real sample clip as the job's master."""
    folder = inbox / job_id
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({"job_id": job_id}))
    shutil.copyfile(_SAMPLE, folder / "master.mp4")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))
    return folder


@requires_ffmpeg
def test_end_to_end_defer_then_drain(tmp_path: Path) -> None:
    # Archives unset -> processed job must land in pending-archive.
    cfg = _config(tmp_path)
    _drop_master(cfg.inbox, "2026-07-02_demo")
    q = ProcessingQueue(cfg.state_dir)

    assert scan_once(cfg.inbox, q) == ["2026-07-02_demo"]
    job_id = process_one(cfg, q)
    assert job_id == "2026-07-02_demo"

    pending = cfg.state_dir / "pending-archive" / "2026-07-02_demo"
    assert pending.is_dir()
    # Real transcode produced a valid, non-empty upload.mp4.
    upload = pending / "upload.mp4"
    assert upload.is_file() and upload.stat().st_size > 0
    assert _ffmpeg.probe_resolution(upload) == (320, 240)
    result = json.loads((pending / "result.json").read_text())
    assert result["ok"] is True

    # Now configure destinations and drain.
    cfg2 = _config(tmp_path, masters=tmp_path / "masters", exports=tmp_path / "exports")
    assert drain_pending(cfg2) == 1
    assert (tmp_path / "masters" / "2026-07-02_demo" / "master.mp4").exists()
    assert (tmp_path / "exports" / "2026-07-02_demo" / "upload.mp4").exists()


@requires_ffmpeg
def test_end_to_end_immediate_archive(tmp_path: Path) -> None:
    cfg = _config(tmp_path, masters=tmp_path / "masters", exports=tmp_path / "exports")
    _drop_master(cfg.inbox, "vid1")
    q = ProcessingQueue(cfg.state_dir)
    scan_once(cfg.inbox, q)
    assert process_one(cfg, q) == "vid1"
    # Straight to the two archives; nothing pending.
    assert (tmp_path / "masters" / "vid1" / "master.mp4").exists()
    assert (tmp_path / "exports" / "vid1" / "upload.mp4").exists()
    assert not (cfg.state_dir / "pending-archive" / "vid1").exists()
    # done/ marker recorded.
    assert (cfg.state_dir / "done" / "vid1").exists()
    # scratch freed.
    assert not (cfg.scratch_dir / "vid1").exists()


def test_processing_failure_goes_to_failed(tmp_path: Path) -> None:
    cfg = _config(tmp_path, masters=tmp_path / "masters", exports=tmp_path / "exports")
    _drop_master(cfg.inbox, "bad1")
    q = ProcessingQueue(cfg.state_dir)
    scan_once(cfg.inbox, q)

    # Remove the master from the queued folder so run_job raises FileNotFoundError.
    queued = cfg.state_dir / "queued" / "bad1"
    (queued / "master.mp4").unlink()

    assert process_one(cfg, q) == "bad1"
    assert (cfg.state_dir / "failed" / "bad1").is_dir()
    assert (cfg.state_dir / "failed" / "bad1" / "job.log").exists()
    assert not (tmp_path / "masters" / "bad1").exists()  # never archived
