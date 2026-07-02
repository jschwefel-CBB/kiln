"""Chapters step — derive YouTube chapter timestamps from the transcript.

Topic-segments ``transcript.txt`` into chapter boundaries and writes ``chapters.txt`` in
YouTube's ``0:00 Title`` timestamp format. Depends on the transcribe step having run.
"""

from __future__ import annotations

from kiln.runner import StepResult
from kiln.steps import StepContext


def run(ctx: StepContext) -> StepResult:  # noqa: D401
    raise NotImplementedError("chapters.run is implemented in Phase 3")
