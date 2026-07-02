"""Transcode step — ffmpeg NVENC to a YouTube-ready MP4.

Generation-aware codec selection from the hardware profile (NVIDIA-only):
  * ``hevc_nvenc`` / ``h264_nvenc`` are the baseline on any modern NVIDIA card.
  * ``av1_nvenc`` is used only on Ada/RTX-40-series or newer (detected via compute
    capability) — the reference A4000 is Ampere and lacks AV1 encode.
  * software ``libx265`` / ``libx264`` is a safety-net fallback only if NVENC is unusable.

Auto-codec by resolution: HEVC for 4K, H.264 High for <=1080p. Output is MP4 with
AAC-LC 48 kHz audio at a generous bitrate (the full-quality master is archived separately).
"""

from __future__ import annotations

import time

from kiln.hwprobe import HardwareProfile
from kiln.runner import StepResult
from kiln.steps import StepContext
from kiln.steps._ffmpeg import FfmpegError, have_ffmpeg, probe_resolution, run_ffmpeg

_4K_MIN_HEIGHT = 2000
_4K_MIN_WIDTH = 3800


def select_codec(width: int, height: int, hw: HardwareProfile) -> tuple[str, list[str]]:
    """Choose (encoder, extra ffmpeg args) from resolution + hardware. AV1 not auto-selected."""
    is_4k = height >= _4K_MIN_HEIGHT or width >= _4K_MIN_WIDTH
    if is_4k:
        if hw.nvenc_hevc:
            return "hevc_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "24", "-tag:v", "hvc1"]
        return "libx265", ["-preset", "medium", "-crf", "22", "-tag:v", "hvc1"]
    if hw.nvenc_h264:
        return "h264_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "21", "-profile:v", "high"]
    return "libx264", ["-preset", "medium", "-crf", "20", "-profile:v", "high"]


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    if not have_ffmpeg():
        return StepResult("transcode", ok=False, seconds=0.0, message="ffmpeg not found")

    width, height = probe_resolution(ctx.master)
    encoder, vargs = select_codec(width, height, ctx.hardware)
    out = ctx.workdir / "upload.mp4"
    software = encoder in {"libx264", "libx265"}
    note = f"{width}x{height} -> {encoder}" + (" (software fallback)" if software else "")

    try:
        run_ffmpeg([
            "-i", str(ctx.master),
            "-c:v", encoder, *vargs,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(out),
        ])
    except FfmpegError as exc:
        return StepResult("transcode", ok=False, seconds=time.monotonic() - start, message=str(exc))

    if not out.is_file() or out.stat().st_size == 0:
        return StepResult("transcode", ok=False, seconds=time.monotonic() - start,
                          message="transcode produced no output")
    return StepResult("transcode", ok=True, seconds=time.monotonic() - start, message=note)
