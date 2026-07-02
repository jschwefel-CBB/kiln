"""End-to-end integration: drop -> queue -> run (no-op steps) -> result -> defer -> drain."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from kiln.archiver import drain_pending
from kiln.config import Config
from kiln.queue import ProcessingQueue
from kiln.service import process_one
from kiln.watcher import scan_once


def _config(tmp_path: Path, masters=None, exports=None) -> Config:
    cfg = Config(
        inbox=tmp_path / "inbox",
        scratch_dir=tmp_path / "scratch",
        state_dir=tmp_path / "state",
        masters_archive=masters,
        exports_archive=exports,
        jobs={"transcode": True, "captions": True, "normalize": True,
              "chapters": True, "metadata": True, "upscale": False},
    )
    for d in (cfg.inbox, cfg.scratch_dir, cfg.state_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


def _drop_master(inbox: Path, job_id: str) -> Path:
    folder = inbox / job_id
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({"job_id": job_id}))
    (folder / "master.mov").write_bytes(b"MASTER BYTES")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))
    return folder


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
    # Real pipeline outputs are present (produced by the no-op steps).
    assert (pending / "upload.mp4").read_bytes() == b"MASTER BYTES"
    assert (pending / "captions.srt").exists()
    assert (pending / "transcript.txt").exists()
    assert (pending / "chapters.txt").exists()
    assert (pending / "metadata.md").exists()
    result = json.loads((pending / "result.json").read_text())
    assert result["ok"] is True

    # Now configure destinations and drain.
    cfg2 = _config(tmp_path, masters=tmp_path / "masters", exports=tmp_path / "exports")
    assert drain_pending(cfg2) == 1
    assert (tmp_path / "masters" / "2026-07-02_demo" / "master.mov").exists()
    assert (tmp_path / "exports" / "2026-07-02_demo" / "upload.mp4").exists()


def test_end_to_end_immediate_archive(tmp_path: Path) -> None:
    cfg = _config(tmp_path, masters=tmp_path / "masters", exports=tmp_path / "exports")
    _drop_master(cfg.inbox, "vid1")
    q = ProcessingQueue(cfg.state_dir)
    scan_once(cfg.inbox, q)
    assert process_one(cfg, q) == "vid1"
    # Straight to the two archives; nothing pending.
    assert (tmp_path / "masters" / "vid1" / "master.mov").exists()
    assert (tmp_path / "exports" / "vid1" / "upload.mp4").exists()
    assert not (cfg.state_dir / "pending-archive" / "vid1").exists()
    # done/ marker recorded.
    assert (cfg.state_dir / "done" / "vid1").exists()
    # scratch freed.
    assert not (cfg.scratch_dir / "vid1").exists()


def test_processing_failure_goes_to_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path, masters=tmp_path / "masters", exports=tmp_path / "exports")
    _drop_master(cfg.inbox, "bad1")
    q = ProcessingQueue(cfg.state_dir)
    scan_once(cfg.inbox, q)

    # Remove the master from the queued folder so run_job raises FileNotFoundError.
    queued = cfg.state_dir / "queued" / "bad1"
    (queued / "master.mov").unlink()

    assert process_one(cfg, q) == "bad1"
    assert (cfg.state_dir / "failed" / "bad1").is_dir()
    assert (cfg.state_dir / "failed" / "bad1" / "job.log").exists()
    assert not (tmp_path / "masters" / "bad1").exists()  # never archived
