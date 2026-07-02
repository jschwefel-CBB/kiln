"""Metadata step — local-LLM drafts of description, titles, and tags.

Feeds the transcript to a local Ollama model (default 8B; GPU if available, else CPU) and
writes ``metadata.md`` containing a draft description, several title options, and suggested
tags. Depends on the transcribe step. Kept local so no transcript leaves the machine and
there is no per-use API cost.
"""

from __future__ import annotations

from kiln.runner import StepResult
from kiln.steps import StepContext


def run(ctx: StepContext) -> StepResult:  # noqa: D401
    raise NotImplementedError("metadata.run is implemented in Phase 3")
