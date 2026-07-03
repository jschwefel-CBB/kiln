"""Tests for kiln.retention — master pruning with the verified-complete-exports fail-safe.

This deletes ProRes masters, so every guard is tested explicitly: age threshold, the
exports-complete check (upload.mp4 + result.json with ok:true), the exports pool being
offline, the per-job .keep_master pin, and the prune_masters opt-in gate.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from kiln.archiver import KEEP_MASTER_MARKER
from kiln.config import Config
from kiln.retention import prune_masters


def _config(tmp_path: Path, *, masters=..., exports=..., prune=True, days=90) -> Config:
    return Config(
        inbox=tmp_path / "inbox",
        scratch_dir=tmp_path / "scratch",
        state_dir=tmp_path / "state",
        masters_archive=(tmp_path / "masters") if masters is ... else masters,
        exports_archive=(tmp_path / "exports") if exports is ... else exports,
        prune_masters=prune,
        retention_days=days,
    )


def _archived_master(tmp_path: Path, job_id: str, *, age_days: float, pinned: bool = False) -> Path:
    d = tmp_path / "masters" / job_id
    d.mkdir(parents=True)
    m = d / "master.mov"
    m.write_bytes(b"MASTER")
    if pinned:
        (d / KEEP_MASTER_MARKER).write_text("")
    when = time.time() - age_days * 86400
    os.utime(m, (when, when))
    return d


def _archived_exports(tmp_path: Path, job_id: str, *, upload=True, result=True, ok=True) -> Path:
    d = tmp_path / "exports" / job_id
    d.mkdir(parents=True)
    if upload:
        (d / "upload.mp4").write_bytes(b"UPLOAD")
    if result:
        (d / "result.json").write_text(json.dumps({"job_id": job_id, "ok": ok}))
    return d


def test_prunes_old_master_with_complete_exports(tmp_path: Path) -> None:
    _archived_master(tmp_path, "old", age_days=120)
    _archived_exports(tmp_path, "old")
    report = prune_masters(_config(tmp_path, days=90), dry_run=False)
    assert "old" in report.pruned
    assert not (tmp_path / "masters" / "old").exists()   # master dir deleted
    assert (tmp_path / "exports" / "old" / "upload.mp4").exists()  # exports untouched


def test_keeps_master_within_window(tmp_path: Path) -> None:
    _archived_master(tmp_path, "recent", age_days=10)
    _archived_exports(tmp_path, "recent")
    report = prune_masters(_config(tmp_path, days=90), dry_run=False)
    assert "recent" not in report.pruned
    assert (tmp_path / "masters" / "recent" / "master.mov").exists()
    assert "recent" in report.kept


def test_never_prunes_when_exports_pool_offline(tmp_path: Path) -> None:
    _archived_master(tmp_path, "old", age_days=200)
    # exports_archive points nowhere (unset) -> pool offline -> must NOT prune.
    report = prune_masters(_config(tmp_path, exports=None, days=90), dry_run=False)
    assert "old" not in report.pruned
    assert (tmp_path / "masters" / "old" / "master.mov").exists()
    assert report.skipped_exports_unreachable


def test_never_prunes_when_upload_missing(tmp_path: Path) -> None:
    _archived_master(tmp_path, "old", age_days=200)
    _archived_exports(tmp_path, "old", upload=False)   # no upload.mp4 in exports
    report = prune_masters(_config(tmp_path, days=90), dry_run=False)
    assert "old" not in report.pruned
    assert (tmp_path / "masters" / "old" / "master.mov").exists()
    assert "old" in report.kept


def test_never_prunes_when_result_missing(tmp_path: Path) -> None:
    _archived_master(tmp_path, "old", age_days=200)
    _archived_exports(tmp_path, "old", result=False)
    report = prune_masters(_config(tmp_path, days=90), dry_run=False)
    assert "old" not in report.pruned
    assert (tmp_path / "masters" / "old" / "master.mov").exists()


def test_never_prunes_when_run_not_ok(tmp_path: Path) -> None:
    _archived_master(tmp_path, "old", age_days=200)
    _archived_exports(tmp_path, "old", ok=False)       # result.json ok:false
    report = prune_masters(_config(tmp_path, days=90), dry_run=False)
    assert "old" not in report.pruned
    assert (tmp_path / "masters" / "old" / "master.mov").exists()


def test_never_prunes_pinned_master(tmp_path: Path) -> None:
    # Old AND complete exports AND pinned -> the pin wins; never deleted.
    _archived_master(tmp_path, "evergreen", age_days=999, pinned=True)
    _archived_exports(tmp_path, "evergreen")
    report = prune_masters(_config(tmp_path, days=90), dry_run=False)
    assert "evergreen" not in report.pruned
    assert (tmp_path / "masters" / "evergreen" / "master.mov").exists()
    assert "evergreen" in report.pinned


def test_dry_run_deletes_nothing(tmp_path: Path) -> None:
    _archived_master(tmp_path, "old", age_days=200)
    _archived_exports(tmp_path, "old")
    report = prune_masters(_config(tmp_path, days=90), dry_run=True)
    # Reported as would-prune, but the master is still on disk.
    assert "old" in report.would_prune
    assert not report.pruned
    assert (tmp_path / "masters" / "old" / "master.mov").exists()


def test_prune_masters_false_forces_dry_run(tmp_path: Path) -> None:
    # Even with dry_run=False, prune_masters=false must delete nothing (opt-in gate).
    _archived_master(tmp_path, "old", age_days=200)
    _archived_exports(tmp_path, "old")
    report = prune_masters(_config(tmp_path, prune=False, days=90), dry_run=False)
    assert "old" in report.would_prune
    assert not report.pruned
    assert (tmp_path / "masters" / "old" / "master.mov").exists()


def test_no_masters_archive_is_noop(tmp_path: Path) -> None:
    report = prune_masters(_config(tmp_path, masters=None), dry_run=False)
    assert not report.pruned and not report.would_prune and not report.kept
