"""Metadata step — local-LLM drafts of description, titles, and tags.

Feeds the transcript to a local Ollama model (default 8B; GPU if available, else CPU) and
writes ``metadata.md`` containing a draft description, several title options, and suggested
tags. Depends on the transcribe step. Kept local so no transcript leaves the machine and
there is no per-use API cost.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from kiln.runner import StepResult
from kiln.steps import StepContext

_MAX_TRANSCRIPT_CHARS = 12000  # keep the prompt within a sane context window


def build_prompt(transcript: str) -> str:
    clipped = transcript[:_MAX_TRANSCRIPT_CHARS]
    return (
        "You are helping a YouTube creator. From the transcript below, write a Markdown "
        "document with three sections:\n"
        "## Description — a compelling 2-3 paragraph video description.\n"
        "## Titles — five alternative title options as a bullet list.\n"
        "## Tags — a comma-separated list of 10-15 relevant tags.\n\n"
        "Transcript:\n"
        f"{clipped}\n"
    )


def _ollama_generate(model: str, prompt: str, host: str = "127.0.0.1",
                     port: int = 11434, timeout: float = 120) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"http://{host}:{port}/api/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    return payload.get("response", "")


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    transcript_file = ctx.workdir / "transcript.txt"
    if not transcript_file.is_file():
        return StepResult("metadata", ok=False, seconds=0.0,
                          message="no transcript.txt (transcribe must run first)")
    prompt = build_prompt(transcript_file.read_text())
    try:
        text = _ollama_generate(ctx.config.llm_model, prompt)
    except (urllib.error.URLError, OSError, ConnectionError, json.JSONDecodeError) as exc:
        return StepResult("metadata", ok=False, seconds=time.monotonic() - start,
                          message=f"ollama call failed (is it running, model pulled?): {exc}")
    if not text.strip():
        return StepResult("metadata", ok=False, seconds=time.monotonic() - start,
                          message="ollama returned empty response")
    (ctx.workdir / "metadata.md").write_text(text)
    return StepResult("metadata", ok=True, seconds=time.monotonic() - start,
                      message=f"{ctx.config.llm_model}")
