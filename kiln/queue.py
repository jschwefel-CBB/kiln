"""Persistent, strict-sequential processing queue.

One video is fully processed before the next starts, so GPU steps never contend for
VRAM. Queue state is persisted to ``state_dir`` and survives a service restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Job:
    """A queued unit of work: a job folder (master + job.json) on local disk."""

    job_id: str
    folder: Path


class ProcessingQueue:
    """Disk-backed FIFO with exactly-once, strict-sequential semantics."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir

    def enqueue(self, job: Job) -> None:
        raise NotImplementedError("ProcessingQueue is implemented in Phase 2")

    def dequeue(self) -> Job | None:
        raise NotImplementedError("ProcessingQueue is implemented in Phase 2")
