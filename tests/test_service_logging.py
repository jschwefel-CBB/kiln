"""The service loop logs its lifecycle to the "kiln" logger (→ journal via stderr).

Covers docs/follow-ups.md gap 2: journalctl -u kiln previously showed only systemd's
start/stop lines. These tests assert the service now emits application-level records for
startup and per-job outcome. They need no ffmpeg — startup runs on an empty inbox, and the
failure path is forced with a missing master.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from kiln.config import Config
from kiln.queue import ProcessingQueue
from kiln.service import process_one, run
from kiln.watcher import scan_once


def _config(tmp_path: Path) -> Config:
    cfg = Config(
        inbox=tmp_path / "inbox",
        scratch_dir=tmp_path / "scratch",
        state_dir=tmp_path / "state",
        masters_archive=None,
        exports_archive=None,
        jobs={"transcode": True, "normalize": True},
    )
    for d in (cfg.inbox, cfg.scratch_dir, cfg.state_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


def test_once_pass_is_logged(tmp_path: Path, caplog) -> None:
    cfg = _config(tmp_path)
    with caplog.at_level(logging.INFO, logger="kiln"):
        run(cfg, once=True)  # empty inbox: one idle pass, then return
    messages = [r.getMessage() for r in caplog.records if r.name == "kiln"]
    # The --once path logs a recognizable start line naming the inbox it scanned.
    assert any("single pass" in m and str(cfg.inbox) in m for m in messages), messages


def test_service_startup_banner_names_submit_endpoint(tmp_path: Path, caplog) -> None:
    """The long-running startup banner reports the submit endpoint host:port for ops.

    Run the loop briefly in a thread, then stop it by shutting the submit server — enough
    to capture the one-time banner without blocking.
    """
    import dataclasses
    import threading
    import time

    # A high, unlikely-to-collide port so the abandoned daemon loop can't clash with the
    # real service's default 8765 (this thread runs until the process exits).
    cfg = dataclasses.replace(_config(tmp_path), submit_port=53917)
    with caplog.at_level(logging.INFO, logger="kiln"):
        t = threading.Thread(target=run, args=(cfg,), kwargs={"once": False}, daemon=True)
        t.start()
        # Give the loop a moment to emit the banner, then interrupt via the port being freed
        # is not exposed; instead poll for the banner and stop the test thread by process
        # exit (daemon). A short sleep is enough since the banner is logged before the loop.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if any(str(cfg.submit_port) in r.getMessage()
                   for r in caplog.records if r.name == "kiln"):
                break
            time.sleep(0.05)
    messages = [r.getMessage() for r in caplog.records if r.name == "kiln"]
    assert any("started" in m and str(cfg.submit_port) in m for m in messages), messages


def test_job_failure_is_logged(tmp_path: Path, caplog) -> None:
    cfg = _config(tmp_path)
    # Drop a job whose master we then delete so run_job raises FileNotFoundError.
    folder = cfg.inbox / "bad1"
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({"job_id": "bad1"}))
    (folder / "master.mp4").write_bytes(b"x")
    import os
    old = 0
    for p in folder.iterdir():
        os.utime(p, (old, old))
    q = ProcessingQueue(cfg.state_dir)
    scan_once(cfg.inbox, q)
    (cfg.state_dir / "queued" / "bad1" / "master.mp4").unlink()

    with caplog.at_level(logging.INFO, logger="kiln"):
        assert process_one(cfg, q) == "bad1"
    records = [r for r in caplog.records if r.name == "kiln"]
    # The failure is logged at WARNING or higher, and names the job.
    failed = [r for r in records if r.levelno >= logging.WARNING and "bad1" in r.getMessage()]
    assert failed, [r.getMessage() for r in records]


def test_job_completion_is_logged(tmp_path: Path, caplog, monkeypatch) -> None:
    """A successfully consumed job logs an INFO line naming it. run_job is stubbed so the
    test needs no ffmpeg/GPU — logging is orthogonal to the step implementations."""
    import os

    import kiln.service as service

    cfg = _config(tmp_path)

    def fake_run_job(job, config, hardware=None):
        # Stand in for a successful run: create the scratch workdir (process_one frees it)
        # and write result.json so the job is a normal, fully-consumed success.
        wd = config.scratch_dir / job.job_id
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "result.json").write_text(json.dumps({"job_id": job.job_id, "ok": True}))
        return []

    monkeypatch.setattr(service, "run_job", fake_run_job)

    folder = cfg.inbox / "good1"
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({"job_id": "good1"}))
    (folder / "master.mp4").write_bytes(b"x")
    for p in folder.iterdir():
        os.utime(p, (0, 0))

    q = ProcessingQueue(cfg.state_dir)
    scan_once(cfg.inbox, q)
    with caplog.at_level(logging.INFO, logger="kiln"):
        assert process_one(cfg, q) == "good1"
    msgs = [r.getMessage() for r in caplog.records if r.name == "kiln"]
    assert any("good1" in m for m in msgs), msgs
