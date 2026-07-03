"""Master retention: prune archived ProRes masters after a rolling window.

Masters are large (~220 GB/hr at 4K); their re-derivable export packages are kept forever
on a separate pool. This sweep deletes a master's archive dir once it is older than
``retention_days`` — but only behind a strict fail-safe, because deletion is irreversible:

  * the exports pool must be reachable, and that job's exports must be **complete**
    (``upload.mp4`` and ``result.json`` present, with ``result.json`` recording ``ok: true``);
  * a master pinned with a ``.keep_master`` marker (from ``job.json`` ``keep_master: true``)
    is never deleted;
  * nothing is deleted unless ``prune_masters`` is enabled *and* this is not a dry run.

``prune_masters(config, dry_run=...)`` returns a :class:`PruneReport` describing what was (or
would be) done. It never raises for a normal "can't prune this one" case — those are recorded
on the report so the caller/log can show them.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from kiln.archiver import KEEP_MASTER_MARKER
from kiln.config import Config


@dataclass
class PruneReport:
    """Outcome of a prune sweep. Lists hold job_ids."""

    pruned: list[str] = field(default_factory=list)          # actually deleted
    would_prune: list[str] = field(default_factory=list)     # eligible, but dry-run / gated
    kept: list[str] = field(default_factory=list)            # within window OR exports incomplete
    pinned: list[str] = field(default_factory=list)          # .keep_master present
    skipped_exports_unreachable: bool = False                # exports pool offline → whole sweep held


def _master_age_days(master_dir: Path) -> float | None:
    """Age of the master file in days, by its mtime. None if no master file present."""
    master = next(
        (p for p in master_dir.iterdir()
         if p.is_file() and p.name != KEEP_MASTER_MARKER),
        None,
    )
    if master is None:
        return None
    return (time.time() - master.stat().st_mtime) / 86400.0


def _exports_complete(exports_dir: Path) -> bool:
    """True iff this job's exports prove a successful, fully-archived run.

    Job-type-agnostic anchor: upload.mp4 and result.json must both exist and result.json must
    record ok: true. Optional artifacts (captions/chapters/metadata) are produced only when
    requested, so they are deliberately not required.
    """
    upload = exports_dir / "upload.mp4"
    result = exports_dir / "result.json"
    if not (upload.is_file() and result.is_file()):
        return False
    try:
        return bool(json.loads(result.read_text()).get("ok", False))
    except (ValueError, OSError):
        return False


def _reachable(path: Path | None) -> bool:
    return path is not None and path.is_dir()


def prune_masters(config: Config, *, dry_run: bool) -> PruneReport:
    """Sweep the masters archive; delete masters past the window with complete exports.

    Deletion happens only when ``config.prune_masters`` is True AND ``dry_run`` is False;
    otherwise eligible masters are reported under ``would_prune`` and left on disk.
    """
    report = PruneReport()
    masters_root = config.masters_archive
    if masters_root is None or not masters_root.is_dir():
        return report  # nothing to prune / not configured

    # Fail-safe: if the exports pool is unreachable we cannot verify anything, so we hold the
    # ENTIRE sweep rather than risk deleting a master whose exports we can't confirm.
    if not _reachable(config.exports_archive):
        report.skipped_exports_unreachable = True
        return report
    exports_root = config.exports_archive
    assert exports_root is not None  # narrowed by _reachable

    # Actual deletion is gated on the opt-in AND a real (non-dry) run.
    will_delete = config.prune_masters and not dry_run

    for master_dir in sorted(p for p in masters_root.iterdir() if p.is_dir()):
        job_id = master_dir.name

        if (master_dir / KEEP_MASTER_MARKER).exists():
            report.pinned.append(job_id)
            continue

        age = _master_age_days(master_dir)
        if age is None or age <= config.retention_days:
            report.kept.append(job_id)
            continue

        if not _exports_complete(exports_root / job_id):
            report.kept.append(job_id)  # old, but exports not verified → never prune
            continue

        # Eligible for pruning.
        if will_delete:
            shutil.rmtree(master_dir, ignore_errors=False)
            report.pruned.append(job_id)
        else:
            report.would_prune.append(job_id)

    return report


def format_report(report: PruneReport, config: Config, *, dry_run: bool) -> str:
    """Human-readable one-screen summary for the CLI / timer log."""
    lines = ["kiln prune", "----------"]
    if report.skipped_exports_unreachable:
        lines.append(
            f"HELD: exports pool unreachable ({config.exports_archive}) — nothing pruned."
        )
        return "\n".join(lines)

    mode = "dry-run" if dry_run else ("live" if config.prune_masters else "disabled (prune_masters=false)")
    lines.append(f"mode: {mode};  retention_days={config.retention_days}")
    if report.pruned:
        lines.append(f"pruned ({len(report.pruned)}): {', '.join(report.pruned)}")
    if report.would_prune:
        verb = "would prune" if (dry_run or not config.prune_masters) else "eligible"
        lines.append(f"{verb} ({len(report.would_prune)}): {', '.join(report.would_prune)}")
    lines.append(f"kept within-window/unverified: {len(report.kept)};  pinned: {len(report.pinned)}")
    return "\n".join(lines)
