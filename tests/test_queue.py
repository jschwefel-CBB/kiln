"""Tests for kiln.paths (state layout) and kiln.queue (directory-of-folders FIFO)."""

from __future__ import annotations

from pathlib import Path

from kiln.paths import StateLayout


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
