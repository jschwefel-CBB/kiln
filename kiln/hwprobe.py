"""Runtime hardware capability probe (NVIDIA-only).

Detects the GPU model + compute capability (to infer NVENC generation and thus AV1
encode availability), total VRAM (to pick a Whisper model tier), CUDA availability for
the AI backends, and the set of ffmpeg encoders actually present. The rest of kiln
consumes the resulting :class:`HardwareProfile` so no reference card is ever assumed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HardwareProfile:
    """What kiln detected about the host at runtime."""

    gpu_name: str | None            # e.g. "NVIDIA RTX A4000"; None if no NVIDIA GPU
    vram_mb: int | None             # total VRAM in MiB
    compute_capability: str | None  # e.g. "8.6" (Ampere)
    nvenc_h264: bool
    nvenc_hevc: bool
    nvenc_av1: bool                 # True only on Ada/RTX-40-series or newer
    cuda_available: bool
    ffmpeg_encoders: tuple[str, ...] = ()

    def whisper_tier(self) -> str:
        """Return the largest Whisper model that fits detected VRAM ("auto" logic)."""
        raise NotImplementedError("whisper_tier is implemented in Phase 2")


def probe() -> HardwareProfile:
    """Probe the host and return a :class:`HardwareProfile`.

    Not yet implemented — Phase 2. Will shell out to ``nvidia-smi`` and
    ``ffmpeg -encoders`` and parse compute capability to gate AV1.
    """
    raise NotImplementedError("hwprobe.probe is implemented in Phase 2 (engine core)")
