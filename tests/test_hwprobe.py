"""Tests for kiln.hwprobe — VRAM->model tiering and AV1 gating logic.

probe() itself shells out to nvidia-smi/ffmpeg and is environment-dependent, so these
tests target the pure decision logic, constructing HardwareProfile directly.
"""

from __future__ import annotations

import pytest

from kiln.hwprobe import HardwareProfile, _av1_capable, probe


def _hw(**kw) -> HardwareProfile:
    base = dict(
        gpu_name="NVIDIA RTX A4000", vram_mb=16376, compute_capability="8.6",
        nvenc_h264=True, nvenc_hevc=True, nvenc_av1=False, cuda_available=True,
    )
    base.update(kw)
    return HardwareProfile(**base)


@pytest.mark.parametrize(
    "vram,expected",
    [(16376, "large-v3"), (10000, "large-v3"), (8000, "medium"),
     (4000, "small"), (1500, "base"), (None, "base")],
)
def test_whisper_tier(vram, expected) -> None:
    assert _hw(vram_mb=vram).whisper_tier() == expected


@pytest.mark.parametrize(
    "cc,encoders,expected",
    [
        ("8.6", ("av1_nvenc",), False),   # Ampere: has encoder listed but not capable
        ("8.9", ("av1_nvenc",), True),    # Ada
        ("9.0", ("av1_nvenc",), True),    # newer
        ("8.9", (), False),               # capable card but ffmpeg lacks the encoder
        (None, ("av1_nvenc",), False),
    ],
)
def test_av1_gate(cc, encoders, expected) -> None:
    assert _av1_capable(cc, encoders) is expected


def test_probe_never_raises() -> None:
    # On any host (GPU or not) probe returns a profile without throwing.
    profile = probe()
    assert isinstance(profile.nvenc_av1, bool)
    assert isinstance(profile.ffmpeg_encoders, tuple)
