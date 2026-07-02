"""Pipeline steps.

Every step exposes a uniform callable ``run(ctx) -> StepResult`` so the runner can treat
them interchangeably and so new steps drop in without changing the orchestrator. A step is
self-contained: it reads from ``ctx`` (paths, config, hardware profile), does its work in
the local scratch dir, and returns a :class:`~kiln.runner.StepResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from kiln.config import Config
from kiln.hwprobe import HardwareProfile
from kiln.runner import StepResult


@dataclass
class StepContext:
    """Everything a step needs, passed by the runner."""

    job_id: str
    master: Path        # the input master, already on local scratch
    workdir: Path       # local scratch working directory for this job
    config: Config
    hardware: HardwareProfile


class Step(Protocol):
    """Structural type all step modules satisfy via a module-level ``run``."""

    def __call__(self, ctx: StepContext) -> StepResult: ...


def _build_registry() -> dict[str, Callable[[StepContext], StepResult]]:
    """Map step name -> the real step module's run (Phase 3).

    The no-op module (kiln.steps.noop) stays in the tree for a possible future
    --dry-run, but is no longer wired here.
    """
    from kiln.steps import chapters, metadata, normalize, transcode, transcribe, upscale

    return {
        "transcode": transcode.run,
        "normalize": normalize.run,
        "transcribe": transcribe.run,
        "chapters": chapters.run,
        "metadata": metadata.run,
        "upscale": upscale.run,
    }


STEPS: dict[str, Callable[[StepContext], StepResult]] = _build_registry()
