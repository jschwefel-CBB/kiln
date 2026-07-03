"""Tests for kiln.watcher — stable-folder detection, scan, and submit endpoint."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

from kiln.queue import ProcessingQueue
from kiln.watcher import is_stable, make_submit_server, scan_once


def _drop(inbox: Path, name: str, *, complete: bool = True) -> Path:
    folder = inbox / name
    folder.mkdir(parents=True)
    if complete:
        (folder / "job.json").write_text("{}")
        (folder / "master.mov").write_bytes(b"data")
    return folder


def test_is_stable_requires_job_and_master(tmp_path: Path) -> None:
    folder = _drop(tmp_path / "inbox", "incomplete", complete=False)
    (folder / "job.json").write_text("{}")  # master missing
    # Age the file so the age-gate isn't the reason it's unstable.
    os.utime(folder / "job.json", (1000, 1000))
    assert is_stable(folder, min_age_seconds=0.0) is False


def test_is_stable_true_for_aged_complete_folder(tmp_path: Path) -> None:
    folder = _drop(tmp_path / "inbox", "ready")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))
    assert is_stable(folder, min_age_seconds=2.0) is True


def test_is_stable_false_for_fresh_write(tmp_path: Path) -> None:
    folder = _drop(tmp_path / "inbox", "fresh")
    # Files just written; newer than the age gate.
    assert is_stable(folder, min_age_seconds=5.0) is False


def test_scan_once_enqueues_stable_folder(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    folder = _drop(inbox, "job-a")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))
    q = ProcessingQueue(tmp_path / "state")
    enqueued = scan_once(inbox, q, min_age_seconds=2.0)
    assert enqueued == ["job-a"]
    assert (tmp_path / "state" / "queued" / "job-a").is_dir()
    assert not folder.exists()  # moved out of inbox


def test_scan_once_ignores_dot_prefixed_folders(tmp_path: Path) -> None:
    """A dot-prefixed folder is a staging/hidden area (the Mac helper stages into
    <inbox>/.staging/<job_id>/ then atomically renames). Even when it looks complete and
    stable, the watcher must never enqueue it — otherwise a mid-copy staging dir that goes
    quiet for the stability window would be picked up incomplete."""
    inbox = tmp_path / "inbox"
    staging = _drop(inbox, ".staging")          # dot-prefixed, complete + stable
    old = time.time() - 10
    for p in staging.iterdir():
        os.utime(p, (old, old))
    q = ProcessingQueue(tmp_path / "state")
    enqueued = scan_once(inbox, q, min_age_seconds=2.0)
    assert enqueued == []                        # not enqueued
    assert staging.exists()                       # left untouched in the inbox


def test_submit_endpoint_triggers_scan(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    folder = _drop(inbox, "job-b")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))
    q = ProcessingQueue(tmp_path / "state")
    server = make_submit_server(q, inbox, "127.0.0.1", 0)  # port 0 = OS-assigned
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/submit", method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read())
        assert payload["enqueued"] == ["job-b"]
    finally:
        server.shutdown()
        server.server_close()
    assert (tmp_path / "state" / "queued" / "job-b").is_dir()
