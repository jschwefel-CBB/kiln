"""Normalize step — loudness-normalize audio to the configured target (default -14 LUFS).

Uses ffmpeg ``loudnorm``. YouTube's reference level is -14 LUFS; normalizing here means
uploads sit at a consistent perceived loudness rather than too quiet or too loud.
"""

from __future__ import annotations

from kiln.runner import StepResult
from kiln.steps import StepContext


def run(ctx: StepContext) -> StepResult:  # noqa: D401
    raise NotImplementedError("normalize.run is implemented in Phase 3")
