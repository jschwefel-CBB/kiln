"""Persistent, strict-sequential processing queue.

One video is fully processed before the next starts, so GPU steps never contend for
VRAM. Queue state is persisted to ``state_dir`` and survives a service restart.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from kiln.paths import StateLayout


@dataclass
class Job:
    """A queued unit of work: a job folder (master + job.json) on local disk."""

    job_id: str
    folder: Path

    @classmethod
    def from_folder(cls, folder: Path) -> "Job":
        folder = Path(folder)
        return cls(job_id=folder.name, folder=folder)


class ProcessingQueue:
    """Disk-backed FIFO with exactly-once, strict-sequential semantics.

    The queue is the filesystem: enqueue/dequeue are atomic ``os.rename`` moves of the
    job folder between ``state_dir`` subdirectories, so state survives a crash with no
    database and no lock file. Requires ``state_dir`` to share a filesystem with the
    source folders (same-filesystem rename); enforced by ``kiln doctor``.
    """

    def __init__(self, state_dir: Path) -> None:
        self._layout = StateLayout(state_dir)
        self._layout.ensure()

    def enqueue(self, folder: Path) -> Job:
        folder = Path(folder)
        dest = self._layout.queued / folder.name
        if folder.resolve() == dest.resolve():
            return Job.from_folder(dest)  # already queued; adopt in place
        os.rename(folder, dest)
        return Job.from_folder(dest)

    def _oldest_queued(self) -> Path | None:
        entries = [p for p in self._layout.queued.iterdir() if p.is_dir()]
        if not entries:
            return None
        return min(entries, key=lambda p: p.stat().st_mtime)

    def current(self) -> Job | None:
        entries = [p for p in self._layout.processing.iterdir() if p.is_dir()]
        return Job.from_folder(entries[0]) if entries else None

    def dequeue(self) -> Job | None:
        if self.current() is not None:
            raise RuntimeError("cannot dequeue: a job is already in processing/")
        oldest = self._oldest_queued()
        if oldest is None:
            return None
        dest = self._layout.processing / oldest.name
        os.rename(oldest, dest)
        return Job.from_folder(dest)

    def recover(self) -> Job | None:
        """Re-queue a job orphaned in processing/ by a crash. Returns it, or None."""
        cur = self.current()
        if cur is None:
            return None
        dest = self._layout.queued / cur.folder.name
        os.rename(cur.folder, dest)
        # Make it the oldest so it re-runs first.
        os.utime(dest, (0, 0))
        return Job.from_folder(dest)
