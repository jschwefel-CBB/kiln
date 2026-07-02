"""Tests for kiln.paths (state layout) and kiln.queue (directory-of-folders FIFO)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from kiln.paths import StateLayout
from kiln.queue import ProcessingQueue


def test_state_layout_creates_all_subdirs(tmp_path: Path) -> None:
    layout = StateLayout(tmp_path / "state")
    layout.ensure()
    for sub in ("queued", "processing", "done", "failed", "pending-archive"):
        assert (tmp_path / "state" / sub).is_dir()


def test_state_layout_properties(tmp_path: Path) -> None:
    layout = StateLayout(tmp_path / "state")
    assert layout.queued == tmp_path / "state" / "queued"
    assert layout.processing == tmp_path / "state" / "processing"
    assert layout.done == tmp_path / "state" / "done"
    assert layout.failed == tmp_path / "state" / "failed"
    assert layout.pending == tmp_path / "state" / "pending-archive"


def _make_job_folder(parent: Path, name: str) -> Path:
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / "job.json").write_text("{}")
    (folder / "master.mov").write_bytes(b"fake master")
    return folder


def test_enqueue_moves_folder_into_queued(tmp_path: Path) -> None:
    q = ProcessingQueue(tmp_path / "state")
    src = _make_job_folder(tmp_path / "inbox", "2026-07-02_a")
    job = q.enqueue(src)
    assert not src.exists()  # moved, not copied
    assert job.folder == tmp_path / "state" / "queued" / "2026-07-02_a"
    assert job.folder.is_dir()
    assert (job.folder / "master.mov").read_bytes() == b"fake master"
    assert job.job_id == "2026-07-02_a"


def test_dequeue_is_fifo_by_mtime(tmp_path: Path) -> None:
    q = ProcessingQueue(tmp_path / "state")
    first = q.enqueue(_make_job_folder(tmp_path / "inbox", "first"))
    time.sleep(0.01)
    second = q.enqueue(_make_job_folder(tmp_path / "inbox", "second"))
    # Force distinct, ordered mtimes regardless of filesystem granularity.
    os.utime(first.folder, (1000, 1000))
    os.utime(second.folder, (2000, 2000))

    got = q.dequeue()
    assert got is not None and got.job_id == "first"
    assert got.folder == tmp_path / "state" / "processing" / "first"


def test_dequeue_empty_returns_none(tmp_path: Path) -> None:
    q = ProcessingQueue(tmp_path / "state")
    assert q.dequeue() is None


def test_strict_sequential_blocks_second_dequeue(tmp_path: Path) -> None:
    q = ProcessingQueue(tmp_path / "state")
    q.enqueue(_make_job_folder(tmp_path / "inbox", "a"))
    q.enqueue(_make_job_folder(tmp_path / "inbox", "b"))
    q.dequeue()  # moves 'a' (or 'b') into processing/
    with pytest.raises(RuntimeError, match="processing"):
        q.dequeue()


def test_recover_requeues_orphan(tmp_path: Path) -> None:
    q = ProcessingQueue(tmp_path / "state")
    q.enqueue(_make_job_folder(tmp_path / "inbox", "crashed"))
    q.dequeue()  # now in processing/
    assert q.current() is not None
    recovered = q.recover()
    assert recovered is not None and recovered.job_id == "crashed"
    assert recovered.folder == tmp_path / "state" / "queued" / "crashed"
    assert q.current() is None
