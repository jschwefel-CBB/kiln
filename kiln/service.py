"""The kiln service loop: producer (inbox watch + submit) + consumer (run + archive).

Strict-sequential: exactly one job is processed at a time. On startup a job orphaned in
``processing/`` by a crash is recovered and re-queued. Completed jobs are archived to the
two destinations (or deferred to the pending-archive queue) and their folder is retired to
``done/`` (or ``failed/`` if processing raised).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path

from kiln.archiver import EXPORT_ARTIFACTS, archive_or_defer, drain_pending
from kiln.config import Config
from kiln.paths import StateLayout
from kiln.queue import ProcessingQueue
from kiln.runner import run_job
from kiln.watcher import make_submit_server, scan_once

# Records go to the "kiln" logger. Running under systemd, stderr is captured into the
# journal, so `journalctl -u kiln` shows this lifecycle. The CLI's `serve` command
# configures a handler; as a library import, kiln stays silent by default.
log = logging.getLogger("kiln")


def _assemble(job_folder: Path, workdir: Path) -> None:
    """Copy produced artifacts from the scratch workdir back next to the master."""
    for name in EXPORT_ARTIFACTS:
        src = workdir / name
        if src.is_file():
            shutil.copyfile(src, job_folder / name)


def process_one(config: Config, queue: ProcessingQueue) -> str | None:
    """Advance the pipeline by exactly one job. Returns the job_id, or None if idle."""
    job = queue.dequeue()
    if job is None:
        return None

    layout = StateLayout(config.state_dir)
    layout.ensure()
    workdir = config.scratch_dir / job.job_id

    log.info("job %s: processing", job.job_id)
    try:
        run_job(job, config)                       # writes outputs into workdir + result.json
        _assemble(job.folder, workdir)             # artifacts now beside the master
        archive_or_defer(job.folder, config)       # split to the two destinations / defer
        # If archive_or_defer deferred, job.folder was moved to pending-archive/ and no
        # longer exists here; if it fully archived, the folder was removed. Either way the
        # processing/ slot is now clear. Record a done marker only when fully consumed.
        if not job.folder.exists():
            (layout.done / job.job_id).mkdir(parents=True, exist_ok=True)
            log.info("job %s: done (archived)", job.job_id)
        else:
            log.info("job %s: processed; archive deferred to pending-archive", job.job_id)
    except Exception as exc:
        # Processing failed: move the job folder to failed/ with a log; never archive.
        log.warning("job %s: FAILED — %s", job.job_id, exc)
        dest = layout.failed / job.job_id
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        if job.folder.exists():
            (job.folder / "job.log").write_text(f"processing failed: {exc}\n")
            os.rename(job.folder, dest)
        else:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "job.log").write_text(f"processing failed: {exc}\n")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)   # free scratch
    return job.job_id


def run(config: Config, *, once: bool = False) -> None:
    """Recover, then run the producer + consumer loops. ``once`` does a single pass."""
    layout = StateLayout(config.state_dir)
    layout.ensure()
    queue = ProcessingQueue(config.state_dir)
    recovered = queue.recover()  # re-queue a crashed job, if any
    if recovered:
        log.info("recovered orphaned job(s) from processing/: %s", recovered)

    if once:
        log.info("kiln serve --once: single pass (inbox=%s)", config.inbox)
        scan_once(config.inbox, queue)
        process_one(config, queue)
        drain_pending(config)
        return

    server = make_submit_server(queue, config.inbox, config.submit_host, config.submit_port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(
        "kiln service started: watching %s, submit endpoint http://%s:%d/submit",
        config.inbox, config.submit_host, config.submit_port,
    )

    last_drain = 0.0
    try:
        while True:
            scan_once(config.inbox, queue)
            processed = process_one(config, queue)
            now = time.monotonic()
            if now - last_drain > 30 or processed is None:
                drain_pending(config)
                last_drain = now
            if processed is None:
                time.sleep(2.0)  # idle backoff
    finally:
        server.shutdown()
        server.server_close()
