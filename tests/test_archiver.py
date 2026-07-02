"""Tests for kiln.archiver — two-destination archive + pending-archive drain."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from kiln.archiver import archive_or_defer, drain_pending
from kiln.config import Config


def _config(tmp_path: Path, masters, exports) -> Config:
    return Config(
        inbox=tmp_path / "inbox",
        scratch_dir=tmp_path / "scratch",
        state_dir=tmp_path / "state",
        masters_archive=masters,
        exports_archive=exports,
    )


def _assembled_job(tmp_path: Path, job_id: str = "2026-07-02_v") -> Path:
    """A processed job folder: master + export artifacts side by side."""
    folder = tmp_path / "state" / "processing" / job_id
    folder.mkdir(parents=True)
    (folder / "master.mov").write_bytes(b"MASTER")
    (folder / "upload.mp4").write_bytes(b"UPLOAD")
    (folder / "captions.srt").write_text("caps")
    (folder / "transcript.txt").write_text("script")
    (folder / "chapters.txt").write_text("00:00 Intro")
    (folder / "metadata.md").write_text("# meta")
    (folder / "result.json").write_text(json.dumps({"job_id": job_id, "ok": True}))
    return folder


def test_archive_both_destinations(tmp_path: Path) -> None:
    masters = tmp_path / "masters"
    exports = tmp_path / "exports"
    cfg = _config(tmp_path, masters, exports)
    job = _assembled_job(tmp_path)

    fully = archive_or_defer(job, cfg)
    assert fully is True
    # Master went to masters_archive/<job_id>/.
    assert (masters / "2026-07-02_v" / "master.mov").read_bytes() == b"MASTER"
    # Export package went to exports_archive/<job_id>/.
    assert (exports / "2026-07-02_v" / "upload.mp4").read_bytes() == b"UPLOAD"
    assert (exports / "2026-07-02_v" / "captions.srt").exists()
    assert (exports / "2026-07-02_v" / "result.json").exists()
    # The master did NOT leak into exports, nor artifacts into masters.
    assert not (exports / "2026-07-02_v" / "master.mov").exists()
    assert not (masters / "2026-07-02_v" / "upload.mp4").exists()
    # Source job folder consumed.
    assert not job.exists()


def test_defer_when_both_unset(tmp_path: Path) -> None:
    cfg = _config(tmp_path, None, None)
    job = _assembled_job(tmp_path)
    fully = archive_or_defer(job, cfg)
    assert fully is False
    pending = tmp_path / "state" / "pending-archive" / "2026-07-02_v"
    assert pending.is_dir()
    assert (pending / "master.mov").exists()
    assert (pending / "upload.mp4").exists()
    assert (pending / "pending.json").exists()


def test_partial_archive_masters_only(tmp_path: Path) -> None:
    masters = tmp_path / "masters"
    cfg = _config(tmp_path, masters, None)  # exports unset
    job = _assembled_job(tmp_path)
    fully = archive_or_defer(job, cfg)
    assert fully is False
    # Master archived immediately...
    assert (masters / "2026-07-02_v" / "master.mov").exists()
    # ...exports held in pending (master no longer there).
    pending = tmp_path / "state" / "pending-archive" / "2026-07-02_v"
    assert (pending / "upload.mp4").exists()
    assert not (pending / "master.mov").exists()


def test_drain_completes_when_destinations_appear(tmp_path: Path) -> None:
    # First defer with nothing configured.
    cfg_unset = _config(tmp_path, None, None)
    archive_or_defer(_assembled_job(tmp_path), cfg_unset)
    assert (tmp_path / "state" / "pending-archive" / "2026-07-02_v").is_dir()

    # Now point both destinations and drain.
    masters = tmp_path / "masters"
    exports = tmp_path / "exports"
    cfg_set = _config(tmp_path, masters, exports)
    count = drain_pending(cfg_set)
    assert count == 1
    assert (masters / "2026-07-02_v" / "master.mov").exists()
    assert (exports / "2026-07-02_v" / "upload.mp4").exists()
    assert not (tmp_path / "state" / "pending-archive" / "2026-07-02_v").exists()


def test_archive_across_filesystem_with_squashed_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archive succeeds cross-filesystem even when the destination refuses chmod/chown.

    Reproduces root-squashed ``sec=sys`` NFS: ``os.rename`` fails (different
    filesystem) and any attempt to replicate source metadata (``copystat``) is
    refused with ``PermissionError``. The archiver must copy the bytes and unlink
    the source without ever touching destination metadata. If ``_place`` regressed
    to ``shutil.move``/``copy2``, the ``copystat`` below would propagate and fail.
    """
    real_rename = __import__("os").rename

    def cross_fs_rename(src: str, dst: str) -> None:
        # Only the archiver's file placement crosses filesystems; state-dir renames
        # (moving job folders within state/) stay on one fs and must still work.
        if "masters" in str(dst) or "exports" in str(dst):
            raise OSError("[Errno 18] Invalid cross-device link")
        real_rename(src, dst)

    def refuse_copystat(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("[Errno 1] Operation not permitted")

    monkeypatch.setattr("kiln.archiver.os.rename", cross_fs_rename)
    monkeypatch.setattr(shutil, "copystat", refuse_copystat)

    masters = tmp_path / "masters"
    exports = tmp_path / "exports"
    cfg = _config(tmp_path, masters, exports)
    job = _assembled_job(tmp_path)

    fully = archive_or_defer(job, cfg)
    assert fully is True
    assert (masters / "2026-07-02_v" / "master.mov").read_bytes() == b"MASTER"
    assert (exports / "2026-07-02_v" / "upload.mp4").read_bytes() == b"UPLOAD"
    assert (exports / "2026-07-02_v" / "captions.srt").exists()
    assert not job.exists()
