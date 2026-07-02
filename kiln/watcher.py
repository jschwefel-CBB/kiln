"""Inbox watcher + localhost submit endpoint.

Watches the local ``$INBOX`` for new job folders and exposes a localhost-only HTTP
submit endpoint for an instant-trigger ping from the Mac-side helper. Both paths just
enqueue onto the :class:`~kiln.queue.ProcessingQueue`.
"""

from __future__ import annotations

import http.server
import json
import time
from pathlib import Path

from kiln.queue import ProcessingQueue

_MASTER_SUFFIXES = {".mov", ".mp4", ".mxf", ".mkv"}


def is_stable(folder: Path, min_age_seconds: float = 2.0) -> bool:
    """True iff the folder is a complete job and nothing was written very recently."""
    folder = Path(folder)
    if not (folder / "job.json").is_file():
        return False
    has_master = any(
        p.is_file() and p.suffix.lower() in _MASTER_SUFFIXES and p.name != "upload.mp4"
        for p in folder.iterdir()
    )
    if not has_master:
        return False
    cutoff = time.time() - min_age_seconds
    newest = max((p.stat().st_mtime for p in folder.iterdir()), default=0.0)
    return newest <= cutoff


def scan_once(inbox: Path, queue: ProcessingQueue, min_age_seconds: float = 2.0) -> list[str]:
    """Enqueue every stable job folder currently in ``inbox``; return enqueued job_ids."""
    inbox = Path(inbox)
    enqueued: list[str] = []
    if not inbox.is_dir():
        return enqueued
    for folder in sorted(p for p in inbox.iterdir() if p.is_dir()):
        if is_stable(folder, min_age_seconds):
            job = queue.enqueue(folder)
            enqueued.append(job.job_id)
    return enqueued


def make_submit_server(
    queue: ProcessingQueue, inbox: Path, host: str, port: int
) -> http.server.ThreadingHTTPServer:
    """Build a localhost-only HTTP server whose POST /submit triggers an inbox scan."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server naming)
            if self.path.rstrip("/") == "/submit":
                enqueued = scan_once(inbox, queue)
                body = json.dumps({"enqueued": enqueued}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: object) -> None:  # silence default stderr logging
            return

    return http.server.ThreadingHTTPServer((host, port), _Handler)


def watch(
    inbox: Path,
    queue: ProcessingQueue,
    min_age_seconds: float = 2.0,
    poll_seconds: float = 2.0,
) -> None:
    """Block, periodically scanning ``inbox`` and enqueuing complete job folders."""
    while True:
        scan_once(inbox, queue, min_age_seconds)
        time.sleep(poll_seconds)
