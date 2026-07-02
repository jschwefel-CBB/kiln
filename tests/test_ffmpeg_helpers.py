"""Tests for kiln.steps._ffmpeg — ffprobe/ffmpeg subprocess helpers."""

from __future__ import annotations

import pytest

from kiln.steps import _ffmpeg


@pytest.mark.skipif(not _ffmpeg.have_ffprobe(), reason="ffprobe not installed")
def test_probe_resolution_reads_sample(sample_master) -> None:
    w, h = _ffmpeg.probe_resolution(sample_master)
    assert (w, h) == (320, 240)


@pytest.mark.skipif(not _ffmpeg.have_ffprobe(), reason="ffprobe not installed")
def test_ffprobe_json_has_streams(sample_master) -> None:
    data = _ffmpeg.ffprobe_json(sample_master)
    assert "streams" in data and len(data["streams"]) >= 1


def test_probe_resolution_missing_file_returns_zero(tmp_path) -> None:
    assert _ffmpeg.probe_resolution(tmp_path / "nope.mp4") == (0, 0)


@pytest.mark.skipif(not _ffmpeg.have_ffmpeg(), reason="ffmpeg not installed")
def test_run_ffmpeg_raises_on_bad_args(tmp_path) -> None:
    from kiln.steps._ffmpeg import FfmpegError

    with pytest.raises(FfmpegError):
        _ffmpeg.run_ffmpeg(["-i", str(tmp_path / "does-not-exist.mp4"),
                            str(tmp_path / "out.mp4")])
