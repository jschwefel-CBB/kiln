"""Chapters step — derive YouTube chapter timestamps from the transcript.

Topic-segments ``transcript.txt`` into chapter boundaries and writes ``chapters.txt`` in
YouTube's ``0:00 Title`` timestamp format. Depends on the transcribe step having run.
"""

from __future__ import annotations

import time

from kiln.runner import StepResult
from kiln.steps import StepContext

_DEFAULT_MAX = 8
_SECONDS_PER_LINE = 4  # rough proportional clock; plain text has no word timestamps


def _fmt_ts(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def build_chapters(transcript: str, max_chapters: int = _DEFAULT_MAX) -> list[str]:
    """Deterministic proportional segmentation of a plain-text transcript into chapters.

    Plain-text transcript has no word timestamps, so timing is proportional to line
    position. Good enough for a first pass; a timestamp-aware version can replace this
    behind the same signature later.
    """
    lines = [ln.strip() for ln in transcript.splitlines() if ln.strip()]
    if not lines:
        return ["0:00 Intro"]

    n = max(1, min(max_chapters, len(lines)))
    total_seconds = len(lines) * _SECONDS_PER_LINE
    chapters: list[str] = []
    for i in range(n):
        idx = (len(lines) * i) // n
        ts = 0 if i == 0 else (total_seconds * i) // n
        title = " ".join(lines[idx].split()[:6]) or f"Part {i + 1}"
        chapters.append(f"{_fmt_ts(ts)} {title}")
    return chapters


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    transcript_file = ctx.workdir / "transcript.txt"
    if not transcript_file.is_file():
        return StepResult("chapters", ok=False, seconds=0.0,
                          message="no transcript.txt (transcribe must run first)")
    chapters = build_chapters(transcript_file.read_text())
    (ctx.workdir / "chapters.txt").write_text("\n".join(chapters) + "\n")
    return StepResult("chapters", ok=True, seconds=time.monotonic() - start,
                      message=f"{len(chapters)} chapters")
