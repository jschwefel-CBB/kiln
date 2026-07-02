"""Upscale step — Real-ESRGAN upscale/denoise (opt-in only).

Off by default. Intended for low-resolution, noisy, or old sources. Heavy and VRAM-tight
on 4K with <=16 GB cards; if the detected hardware is insufficient the step is skipped with
a clear message rather than failing the job.
"""

from __future__ import annotations

from kiln.runner import StepResult
from kiln.steps import StepContext


def run(ctx: StepContext) -> StepResult:  # noqa: D401
    raise NotImplementedError("upscale.run is implemented in Phase 3")
