"""State-directory layout — the single source of truth for the queue's on-disk scheme.

kiln's queue is the filesystem: a job is a folder that physically moves between these
subdirectories of ``state_dir``. Every component (queue, archiver, CLI, service) agrees
on the scheme through this module so the names live in exactly one place.

    state_dir/
      queued/            jobs waiting to be processed (FIFO by mtime)
      processing/        the single job currently running (at most one)
      done/              successfully processed and fully archived
      failed/            processing failed; kept with its job.log, never archived
      pending-archive/   processed OK but a destination was unreachable; drainer retries
"""

from __future__ import annotations

from pathlib import Path

QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
FAILED = "failed"
PENDING = "pending-archive"

_SUBDIRS = (QUEUED, PROCESSING, DONE, FAILED, PENDING)


class StateLayout:
    """Resolves and creates the ``state_dir`` subdirectories."""

    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def queued(self) -> Path:
        return self._root / QUEUED

    @property
    def processing(self) -> Path:
        return self._root / PROCESSING

    @property
    def done(self) -> Path:
        return self._root / DONE

    @property
    def failed(self) -> Path:
        return self._root / FAILED

    @property
    def pending(self) -> Path:
        return self._root / PENDING

    def ensure(self) -> None:
        """Create every state subdirectory (idempotent)."""
        for sub in _SUBDIRS:
            (self._root / sub).mkdir(parents=True, exist_ok=True)
