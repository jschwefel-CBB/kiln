"""Job runner: executes the pipeline for one job.

Reads ``job.json``, runs the selected steps in dependency order (transcribe must
precede chapters and metadata, which consume the transcript), writes outputs to a local
working directory, records per-step status/timings in ``result.json``, then hands the
completed job to the archiver. Steps are fail-isolated: a failed step is recorded and
independent steps still run.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from kiln.config import Config
from kiln.hwprobe import HardwareProfile, probe
from kiln.queue import Job


@dataclass
class StepResult:
    """Outcome of a single pipeline step."""

    name: str
    ok: bool
    seconds: float
    message: str = ""


# Dependency order enforced by the runner. Steps not requested in job.json are skipped.
STEP_ORDER: tuple[str, ...] = (
    "transcode",
    "normalize",
    "transcribe",   # produces the transcript consumed below
    "chapters",
    "metadata",
    "upscale",
)

# Maps a job.json capability key to the step name where they differ.
_JOB_TO_STEPS: dict[str, str] = {"captions": "transcribe"}

# The reverse, for reading the toggle that governs each step.
_STEP_TO_JOB: dict[str, str] = {v: k for k, v in _JOB_TO_STEPS.items()}

_MASTER_SUFFIXES = {".mov", ".mp4", ".mxf", ".mkv"}


def resolve_workdir(config: Config, job_id: str) -> Path:
    workdir = config.scratch_dir / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _wanted(step: str, toggles: dict[str, bool]) -> bool:
    """Is this step requested? Look up its job.json key; default off if absent."""
    key = _STEP_TO_JOB.get(step, step)
    return bool(toggles.get(key, False))


def write_result(workdir: Path, job_id: str, results: list[StepResult]) -> Path:
    # A skipped step (dependency not met) has a message beginning "skipped"; it does not
    # count against the job's success. Everything else is an attempt.
    attempted = [r for r in results if not r.message.startswith("skipped")]
    ok = all(r.ok for r in attempted) if attempted else True
    payload = {
        "job_id": job_id,
        "ok": ok,
        "steps": [
            {"name": r.name, "ok": r.ok, "seconds": round(r.seconds, 3), "message": r.message}
            for r in results
        ],
    }
    out = workdir / "result.json"
    out.write_text(json.dumps(payload, indent=2))
    return out


def run_job(job: Job, config: Config, hardware: HardwareProfile | None = None) -> list[StepResult]:
    """Run all requested steps for ``job`` in dependency order; return per-step results."""
    # Imported here to avoid a circular import (kiln.steps imports StepResult from this module).
    from kiln.steps import STEPS, StepContext

    hardware = hardware or probe()
    job_file = job.folder / "job.json"
    spec = json.loads(job_file.read_text()) if job_file.is_file() else {}
    toggles: dict[str, bool] = dict(config.jobs)
    toggles.update(spec.get("jobs", {}))

    # Copy the master into the scratch workdir (processing is fully local).
    master_src = next(
        (p for p in job.folder.iterdir()
         if p.is_file() and p.suffix.lower() in _MASTER_SUFFIXES and p.name != "upload.mp4"),
        None,
    )
    if master_src is None:
        raise FileNotFoundError(f"no master video found in {job.folder}")
    workdir = resolve_workdir(config, job.job_id)
    master = workdir / master_src.name
    shutil.copyfile(master_src, master)

    ctx = StepContext(job_id=job.job_id, master=master, workdir=workdir,
                      config=config, hardware=hardware)

    results: list[StepResult] = []
    transcribe_ok = False
    for step in STEP_ORDER:
        if not _wanted(step, toggles):
            continue
        # Dependency gate: chapters/metadata need a successful transcript.
        if step in {"chapters", "metadata"} and not transcribe_ok:
            results.append(StepResult(
                step, ok=False, seconds=0.0,
                message="skipped: requires transcript (transcribe not run/failed)",
            ))
            continue
        try:
            result = STEPS[step](ctx)
        except Exception as exc:  # fail-isolated: record and continue
            result = StepResult(step, ok=False, seconds=0.0, message=f"error: {exc}")
        results.append(result)
        if step == "transcribe" and result.ok:
            transcribe_ok = True

    write_result(workdir, job.job_id, results)
    return results
