"""Inbox watcher + localhost submit endpoint.

Watches the local ``$INBOX`` for new job folders and exposes a localhost-only HTTP
submit endpoint for an instant-trigger ping from the Mac-side helper. Both paths just
enqueue onto the :class:`~kiln.queue.ProcessingQueue`.
"""

from __future__ import annotations

from pathlib import Path

from kiln.queue import ProcessingQueue


def watch(inbox: Path, queue: ProcessingQueue) -> None:
    """Block, watching ``inbox`` and enqueuing complete job folders.

    Not yet implemented — Phase 2 (uses ``watchdog``).
    """
    raise NotImplementedError("watcher.watch is implemented in Phase 2 (engine core)")
