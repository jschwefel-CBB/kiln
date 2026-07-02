"""Transcribe step — faster-whisper to captions + transcript.

Device auto-selected (CUDA when available, else CPU). Model size auto-selected by detected
VRAM ("auto" → large-v3 on >=~10 GB, smaller otherwise), overridable in config. Produces
``captions.srt`` (for upload) and ``transcript.txt`` (consumed by the chapters and metadata
steps, and useful for descriptions/blog posts).
"""

from __future__ import annotations

from kiln.runner import StepResult
from kiln.steps import StepContext


def run(ctx: StepContext) -> StepResult:  # noqa: D401
    raise NotImplementedError("transcribe.run is implemented in Phase 3")
