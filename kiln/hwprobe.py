"""Runtime hardware capability probe (NVIDIA-only).

Detects the GPU model + compute capability (to infer NVENC generation and thus AV1
encode availability), total VRAM (to pick a Whisper model tier), CUDA availability for
the AI backends, and the set of ffmpeg encoders actually present. The rest of kiln
consumes the resulting :class:`HardwareProfile` so no reference card is ever assumed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
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
        vram = self.vram_mb or 0
        if vram >= 10000:
            return "large-v3"
        if vram >= 5000:
            return "medium"
        if vram >= 2000:
            return "small"
        return "base"


def _run(cmd: list[str]) -> str:
    """Run a command, returning stdout ('' on any failure — probe must never raise)."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _ffmpeg_encoders() -> tuple[str, ...]:
    if shutil.which("ffmpeg") is None:
        return ()
    text = _run(["ffmpeg", "-hide_banner", "-encoders"])
    found = []
    for token in ("h264_nvenc", "hevc_nvenc", "av1_nvenc", "libx264", "libx265"):
        if token in text:
            found.append(token)
    return tuple(found)


def _av1_capable(compute_capability: str | None, encoders: tuple[str, ...]) -> bool:
    """AV1 NVENC exists on Ada/RTX-40+ (compute capability >= 8.9) AND ffmpeg must have it."""
    if "av1_nvenc" not in encoders or not compute_capability:
        return False
    try:
        major, minor = (int(x) for x in compute_capability.split("."))
    except ValueError:
        return False
    return (major, minor) >= (8, 9)


def probe() -> HardwareProfile:
    """Probe the host and return a :class:`HardwareProfile`. Never raises."""
    encoders = _ffmpeg_encoders()

    gpu_name: str | None = None
    vram_mb: int | None = None
    compute_capability: str | None = None
    cuda_available = False

    if shutil.which("nvidia-smi") is not None:
        q = _run([
            "nvidia-smi",
            "--query-gpu=name,memory.total,compute_cap",
            "--format=csv,noheader,nounits",
        ])
        line = q.strip().splitlines()[0] if q.strip() else ""
        if line:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                gpu_name = parts[0] or None
                m = re.search(r"\d+", parts[1])
                vram_mb = int(m.group()) if m else None
                cuda_available = True
            if len(parts) >= 3 and re.match(r"^\d+\.\d+$", parts[2]):
                compute_capability = parts[2]

    return HardwareProfile(
        gpu_name=gpu_name,
        vram_mb=vram_mb,
        compute_capability=compute_capability,
        nvenc_h264="h264_nvenc" in encoders,
        nvenc_hevc="hevc_nvenc" in encoders,
        nvenc_av1=_av1_capable(compute_capability, encoders),
        cuda_available=cuda_available,
        ffmpeg_encoders=encoders,
    )
