"""Job runner: executes the pipeline for one job.

Reads ``job.json``, runs the selected steps in dependency order (transcribe must
precede chapters and metadata, which consume the transcript), writes outputs to a local
working directory, records per-step status/timings in ``result.json``, then hands the
completed job to the archiver. Steps are fail-isolated: a failed step is recorded and
independent steps still run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kiln.config import Config
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


def run_job(job: Job, config: Config) -> list[StepResult]:
    """Run all requested steps for ``job``; return per-step results.

    Not yet implemented — Phases 2/3.
    """
    raise NotImplementedError("runner.run_job is implemented in Phases 2-3")
