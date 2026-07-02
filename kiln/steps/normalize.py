"""Normalize step — loudness-normalize audio to the configured target (default -14 LUFS).

Uses ffmpeg ``loudnorm``. YouTube's reference level is -14 LUFS; normalizing here means
uploads sit at a consistent perceived loudness rather than too quiet or too loud.
"""

from __future__ import annotations

import os
import time

from kiln.runner import StepResult
from kiln.steps import StepContext
from kiln.steps._ffmpeg import FfmpegError, have_ffmpeg, run_ffmpeg


def _loudnorm_filter(target_lufs: int) -> str:
    # TP (true-peak) -1.5 dBTP and LRA 11 are standard, conservative loudnorm defaults.
    return f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    upload = ctx.workdir / "upload.mp4"
    if not upload.is_file():
        return StepResult("normalize", ok=False, seconds=0.0,
                          message="no upload.mp4 to normalize (transcode must run first)")
    if not have_ffmpeg():
        return StepResult("normalize", ok=False, seconds=0.0, message="ffmpeg not found")

    tmp = ctx.workdir / "upload.norm.mp4"
    try:
        run_ffmpeg([
            "-i", str(upload),
            "-af", _loudnorm_filter(ctx.config.target_lufs),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(tmp),
        ])
    except FfmpegError as exc:
        tmp.unlink(missing_ok=True)
        return StepResult("normalize", ok=False, seconds=time.monotonic() - start, message=str(exc))

    if not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return StepResult("normalize", ok=False, seconds=time.monotonic() - start,
                          message="normalize produced no output")
    os.replace(tmp, upload)  # atomic swap in place
    return StepResult("normalize", ok=True, seconds=time.monotonic() - start,
                      message=f"loudnorm I={ctx.config.target_lufs}")
