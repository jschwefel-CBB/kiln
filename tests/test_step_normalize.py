"""Tests for kiln.steps.normalize — loudnorm filter + a real normalize when ffmpeg is present."""

from __future__ import annotations

import shutil

import pytest

from kiln.steps import _ffmpeg, normalize


def test_loudnorm_filter_uses_target() -> None:
    assert normalize._loudnorm_filter(-14) == "loudnorm=I=-14:TP=-1.5:LRA=11"
    assert normalize._loudnorm_filter(-16) == "loudnorm=I=-16:TP=-1.5:LRA=11"


def test_normalize_without_upload_fails(step_ctx) -> None:
    ctx = step_ctx()  # no upload.mp4 seeded
    result = normalize.run(ctx)
    assert result.ok is False
    assert "upload.mp4" in result.message


@pytest.mark.skipif(not _ffmpeg.have_ffmpeg(), reason="ffmpeg not installed")
def test_real_normalize_rewrites_upload(step_ctx, sample_master) -> None:
    ctx = step_ctx()
    # Seed an upload.mp4 (the normalize step operates on it) by copying the sample.
    shutil.copyfile(sample_master, ctx.workdir / "upload.mp4")
    result = normalize.run(ctx)
    assert result.ok is True, result.message
    upload = ctx.workdir / "upload.mp4"
    assert upload.is_file() and upload.stat().st_size > 0
    # still a valid, readable video afterwards
    assert _ffmpeg.probe_resolution(upload) == (320, 240)
