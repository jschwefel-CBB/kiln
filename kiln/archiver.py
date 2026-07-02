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

import json
import os
import shutil
from pathlib import Path

from kiln.config import Config
from kiln.paths import StateLayout

EXPORT_ARTIFACTS = (
    "upload.mp4", "captions.srt", "transcript.txt",
    "chapters.txt", "metadata.md", "result.json", "job.log",
)
_MASTER_SUFFIXES = {".mov", ".mp4", ".mxf", ".mkv"}


def _reachable(path: Path | None) -> bool:
    """A destination is usable iff set, creatable, and writable."""
    if path is None:
        return False
    try:
        path.mkdir(parents=True, exist_ok=True)
        return os.access(path, os.W_OK)
    except OSError:
        return False


def _master_in(folder: Path) -> Path | None:
    # upload.mp4 is an artifact, not the master — exclude it explicitly.
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in _MASTER_SUFFIXES and p.name != "upload.mp4":
            return p
    return None


def _move_file(src: Path, dst: Path) -> None:
    """Move one file src -> dst, working across filesystems and over squashed NFS.

    ``shutil.move`` falls back to ``copy2`` for a cross-filesystem move, and
    ``copy2`` calls ``copystat`` to replicate the source's mode/mtime/flags onto
    the destination. Over root-squashed ``sec=sys`` NFS (the normal secure export
    config) that metadata operation is refused with ``Operation not permitted``
    even though the bytes copy fine. The archive is write-once, so preserving the
    source's exact mode/mtime on it has no value — copy bytes only, then unlink.
    """
    try:
        os.rename(src, dst)  # fast path: same filesystem
    except OSError:
        shutil.copyfile(src, dst)  # bytes only — no copystat, NFS-safe
        os.unlink(src)


def _place(files: list[Path], dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        _move_file(f, dest_dir / f.name)


def archive_or_defer(job_dir: Path, config: Config) -> bool:
    """Split ``job_dir`` to its two destinations; defer whatever can't be placed.

    Returns True iff both halves archived immediately (nothing deferred).
    """
    job_dir = Path(job_dir)
    job_id = job_dir.name
    layout = StateLayout(config.state_dir)
    layout.ensure()

    master = _master_in(job_dir)
    artifacts = [job_dir / name for name in EXPORT_ARTIFACTS if (job_dir / name).is_file()]

    masters_ok = _reachable(config.masters_archive)
    exports_ok = _reachable(config.exports_archive)

    deferred: list[str] = []

    if master is not None:
        if masters_ok:
            assert config.masters_archive is not None  # narrowed by masters_ok
            _place([master], config.masters_archive / job_id)
        else:
            deferred.append("master")

    if artifacts:
        if exports_ok:
            assert config.exports_archive is not None  # narrowed by exports_ok
            _place(artifacts, config.exports_archive / job_id)
        else:
            deferred.append("exports")

    if not deferred:
        # Everything placed; drop the now-empty job folder.
        shutil.rmtree(job_dir, ignore_errors=True)
        return True

    # Something couldn't be placed — hold the whole (remaining) job in pending-archive.
    manifest = {"job_id": job_id, "deferred": deferred}
    (job_dir / "pending.json").write_text(json.dumps(manifest, indent=2))
    dest = layout.pending / job_id
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    os.rename(job_dir, dest)
    return False


def drain_pending(config: Config) -> int:
    """Retry deferred jobs; return the count fully archived this pass."""
    layout = StateLayout(config.state_dir)
    layout.ensure()
    drained = 0
    for job_dir in sorted(p for p in layout.pending.iterdir() if p.is_dir()):
        # Remove any stale manifest so a fully-archived job isn't misjudged.
        (job_dir / "pending.json").unlink(missing_ok=True)
        if archive_or_defer(job_dir, config):
            drained += 1
    return drained
