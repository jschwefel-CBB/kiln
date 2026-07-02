"""Archiver: the final step that moves finished work to storage.

On job success, moves the master + all outputs to ``$ARCHIVE/<job_id>/`` **if** storage
is reachable and writable. Otherwise the completed job is placed in a persistent local
pending-archive queue; a periodic drainer retries and frees local scratch once a job is
safely archived. This decouples day-to-day processing from storage availability.
"""

from __future__ import annotations

from pathlib import Path

from kiln.config import Config


def archive_or_defer(job_dir: Path, config: Config) -> bool:
    """Move ``job_dir`` to the archive if reachable; else defer to the pending queue.

    Returns True if archived immediately, False if deferred. Not yet implemented — Phase 2.
    """
    raise NotImplementedError("archiver.archive_or_defer is implemented in Phase 2")


def drain_pending(config: Config) -> int:
    """Retry deferred jobs; return the count successfully archived this pass.

    Not yet implemented — Phase 2.
    """
    raise NotImplementedError("archiver.drain_pending is implemented in Phase 2")
