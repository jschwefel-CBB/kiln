"""Transcribe step — faster-whisper to captions + transcript.

Device auto-selected (CUDA when available, else CPU). Model size auto-selected by detected
VRAM ("auto" → large-v3 on >=~10 GB, smaller otherwise), overridable in config. Produces
``captions.srt`` (for upload) and ``transcript.txt`` (consumed by the chapters and metadata
steps, and useful for descriptions/blog posts).
"""

from __future__ import annotations

import time
from typing import Iterable

from kiln.hwprobe import HardwareProfile
from kiln.runner import StepResult
from kiln.steps import StepContext


def resolve_model(config_value: str, hw: HardwareProfile) -> str:
    return hw.whisper_tier() if config_value == "auto" else config_value


def resolve_device(hw: HardwareProfile) -> tuple[str, str]:
    return ("cuda", "float16") if hw.cuda_available else ("cpu", "int8")


def _fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments: Iterable) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(f"{i}\n{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}\n{seg.text.strip()}\n")
    return "\n".join(lines)


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]
    except ImportError:
        return StepResult("transcribe", ok=False, seconds=0.0,
                          message="faster-whisper not installed")

    model_name = resolve_model(ctx.config.whisper_model, ctx.hardware)
    device, compute_type = resolve_device(ctx.hardware)
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments, _info = model.transcribe(str(ctx.master))
        segments = list(segments)  # materialize the generator once
    except Exception as exc:  # model download failure, CUDA OOM, etc. — handled, not raised
        return StepResult("transcribe", ok=False, seconds=time.monotonic() - start,
                          message=f"whisper failed: {exc}")

    (ctx.workdir / "captions.srt").write_text(_segments_to_srt(segments))
    (ctx.workdir / "transcript.txt").write_text(
        "\n".join(seg.text.strip() for seg in segments)
    )
    return StepResult("transcribe", ok=True, seconds=time.monotonic() - start,
                      message=f"{model_name} on {device}, {len(segments)} segments")
