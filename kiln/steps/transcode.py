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

from kiln.runner import StepResult
from kiln.steps import StepContext


def run(ctx: StepContext) -> StepResult:  # noqa: D401
    raise NotImplementedError("transcode.run is implemented in Phase 3")
