"""Archiver: the final step that moves finished work to storage.

On job success, moves outputs to their two write-once destinations:
  * the ProRes master   -> ``masters_archive/<job_id>/``
  * the export package   -> ``exports_archive/<job_id>/``
each **if** that destination is reachable and writable. Whatever cannot be moved
(destination unset or offline) is placed in a persistent local pending-archive
queue; a periodic drainer retries and frees local scratch once a job's pieces are
safely archived. This decouples day-to-day processing from storage availability,
and lets masters and exports live on separate storage tiers.
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
