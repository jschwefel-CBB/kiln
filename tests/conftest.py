"""Shared pytest fixtures for kiln step tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kiln.config import Config
from kiln.hwprobe import HardwareProfile
from kiln.steps import StepContext

_FIXTURE = Path(__file__).parent / "fixtures" / "sample.mp4"


@pytest.fixture
def sample_master() -> Path:
    assert _FIXTURE.is_file(), "run tests/fixtures/make_fixture.sh to generate sample.mp4"
    return _FIXTURE


def _config(tmp_path: Path) -> Config:
    return Config(
        inbox=tmp_path / "inbox",
        scratch_dir=tmp_path / "scratch",
        state_dir=tmp_path / "state",
        masters_archive=None,
        exports_archive=None,
    )


@pytest.fixture
def fake_hardware():
    def _make(**overrides) -> HardwareProfile:
        base = dict(
            gpu_name="NVIDIA RTX A4000", vram_mb=16376, compute_capability="8.6",
            nvenc_h264=True, nvenc_hevc=True, nvenc_av1=False, cuda_available=True,
            ffmpeg_encoders=("h264_nvenc", "hevc_nvenc", "libx264", "libx265"),
        )
        base.update(overrides)
        return HardwareProfile(**base)
    return _make


@pytest.fixture
def step_ctx(tmp_path, sample_master, fake_hardware):
    def _make(master: Path | None = None, hardware: HardwareProfile | None = None) -> StepContext:
        workdir = tmp_path / "work"
        workdir.mkdir(exist_ok=True)
        src = master or sample_master
        dest = workdir / src.name
        shutil.copyfile(src, dest)
        return StepContext(
            job_id="testjob", master=dest, workdir=workdir,
            config=_config(tmp_path), hardware=hardware or fake_hardware(),
        )
    return _make
