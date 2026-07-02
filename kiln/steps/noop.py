"""No-op passthrough steps for Phase 2.

These stand in for the real GPU/AI steps (Phase 3) so the whole pipeline —
queue -> runner -> result.json -> archiver -> pending-archive -> drain — is testable with
zero ffmpeg/CUDA/model dependency. Each produces the same output *filenames* the real
steps will, so the runner, result.json, and the archiver's export-package logic are all
exercised for real. Phase 3 repoints the STEPS registry from here to the real modules.
"""

from __future__ import annotations

import shutil
import time
from typing import Callable

from kiln.runner import StepResult
from kiln.steps import StepContext

_PLACEHOLDER = "kiln no-op placeholder (Phase 2)\n"


def _timed(name: str, work: Callable[[], object]) -> StepResult:
    """Run ``work`` (return value discarded) and wrap it in a successful StepResult."""
    start = time.monotonic()
    work()
    return StepResult(name=name, ok=True, seconds=time.monotonic() - start)


def transcode(ctx: StepContext) -> StepResult:
    """Stand-in transcode: copy the master to upload.mp4 (non-empty, observable)."""
    return _timed("transcode", lambda: shutil.copyfile(ctx.master, ctx.workdir / "upload.mp4"))


def normalize(ctx: StepContext) -> StepResult:
    """Stand-in loudness normalize: no-op (audio handled with the real transcode later)."""
    return _timed("normalize", lambda: None)


def transcribe(ctx: StepContext) -> StepResult:
    def _work() -> None:
        (ctx.workdir / "captions.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n" + _PLACEHOLDER
        )
        (ctx.workdir / "transcript.txt").write_text(_PLACEHOLDER)

    return _timed("transcribe", _work)


def chapters(ctx: StepContext) -> StepResult:
    return _timed(
        "chapters",
        lambda: (ctx.workdir / "chapters.txt").write_text("00:00 Intro\n"),
    )


def metadata(ctx: StepContext) -> StepResult:
    return _timed(
        "metadata",
        lambda: (ctx.workdir / "metadata.md").write_text("# Draft metadata\n" + _PLACEHOLDER),
    )


def upscale(ctx: StepContext) -> StepResult:
    """Opt-in upscale: no-op stand-in (real Real-ESRGAN in Phase 3)."""
    return _timed("upscale", lambda: None)


def run(ctx: StepContext) -> StepResult:
    """Default entry (unused by the runner, which calls the named functions via STEPS)."""
    return transcode(ctx)
