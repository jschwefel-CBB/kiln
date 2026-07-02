"""Tests for kiln.steps.transcode — codec selection logic + a real encode when ffmpeg is present."""

from __future__ import annotations

import pytest

from kiln.steps import _ffmpeg, transcode


def test_4k_prefers_hevc_nvenc_when_available(fake_hardware) -> None:
    enc, _ = transcode.select_codec(3840, 2160, fake_hardware(nvenc_hevc=True))
    assert enc == "hevc_nvenc"


def test_1080p_prefers_h264_nvenc_when_available(fake_hardware) -> None:
    enc, _ = transcode.select_codec(1920, 1080, fake_hardware(nvenc_h264=True))
    assert enc == "h264_nvenc"


def test_falls_back_to_software_when_no_nvenc(fake_hardware) -> None:
    hw = fake_hardware(nvenc_h264=False, nvenc_hevc=False)
    enc4k, _ = transcode.select_codec(3840, 2160, hw)
    enc1080, _ = transcode.select_codec(1920, 1080, hw)
    assert enc4k == "libx265"
    assert enc1080 == "libx264"


def test_av1_never_auto_selected(fake_hardware) -> None:
    # Even on a card that supports AV1, auto mode does not pick it in Phase 3.
    hw = fake_hardware(nvenc_av1=True, compute_capability="8.9")
    enc, _ = transcode.select_codec(3840, 2160, hw)
    assert enc != "av1_nvenc"


@pytest.mark.skipif(not _ffmpeg.have_ffmpeg(), reason="ffmpeg not installed")
def test_real_transcode_produces_playable_upload(step_ctx, fake_hardware) -> None:
    # Force the software encoder so this runs on any ffmpeg box (no NVENC/GPU needed).
    ctx = step_ctx(hardware=fake_hardware(nvenc_h264=False, nvenc_hevc=False, cuda_available=False))
    result = transcode.run(ctx)
    assert result.ok is True, result.message
    upload = ctx.workdir / "upload.mp4"
    assert upload.is_file() and upload.stat().st_size > 0
    # Output is a valid video ffprobe can read.
    assert _ffmpeg.probe_resolution(upload) == (320, 240)
