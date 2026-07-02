"""Tests for kiln.steps.noop, the STEPS registry, and kiln.runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kiln.config import Config
from kiln.hwprobe import HardwareProfile
from kiln.queue import Job
from kiln.runner import STEP_ORDER, StepResult, run_job, write_result
from kiln.steps import STEPS, StepContext, noop


def _ctx(tmp_path: Path) -> StepContext:
    workdir = tmp_path / "work"
    workdir.mkdir()
    master = workdir / "master.mov"
    master.write_bytes(b"fake master bytes")
    cfg = Config(
        inbox=tmp_path / "inbox",
        scratch_dir=tmp_path / "scratch",
        state_dir=tmp_path / "state",
        masters_archive=None,
        exports_archive=None,
    )
    hw = HardwareProfile(
        gpu_name=None, vram_mb=None, compute_capability=None,
        nvenc_h264=False, nvenc_hevc=False, nvenc_av1=False, cuda_available=False,
    )
    return StepContext(job_id="job1", master=master, workdir=workdir, config=cfg, hardware=hw)


def test_noop_transcode_produces_upload(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    result = noop.transcode(ctx)
    assert result.ok is True
    assert result.name == "transcode"
    assert (ctx.workdir / "upload.mp4").exists()
    # No-op transcode copies the master bytes so the artifact is non-empty.
    assert (ctx.workdir / "upload.mp4").read_bytes() == b"fake master bytes"


def test_noop_transcribe_produces_srt_and_transcript(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    result = noop.transcribe(ctx)
    assert result.ok is True
    assert (ctx.workdir / "captions.srt").exists()
    assert (ctx.workdir / "transcript.txt").exists()


def test_steps_registry_covers_every_ordered_step() -> None:
    for step in STEP_ORDER:
        assert step in STEPS, f"{step} missing from STEPS registry"
        assert callable(STEPS[step])


# --- Task 5b (runner) tests appended below ---


def _job(tmp_path: Path, jobs: dict) -> Job:
    folder = tmp_path / "state" / "processing" / "2026-07-02_v"
    folder.mkdir(parents=True)
    (folder / "master.mov").write_bytes(b"fake master bytes")
    (folder / "job.json").write_text(json.dumps({"job_id": "2026-07-02_v", "jobs": jobs}))
    return Job.from_folder(folder)


def _config(tmp_path: Path) -> Config:
    return Config(
        inbox=tmp_path / "inbox",
        scratch_dir=tmp_path / "scratch",
        state_dir=tmp_path / "state",
        masters_archive=None,
        exports_archive=None,
    )


def test_run_job_runs_requested_steps_in_order(tmp_path: Path) -> None:
    job = _job(tmp_path, {"transcode": True, "captions": True, "chapters": True,
                          "metadata": True, "normalize": True, "upscale": False})
    results = run_job(job, _config(tmp_path))
    names = [r.name for r in results if r.ok]
    # transcode before transcribe before chapters/metadata; upscale absent.
    assert names.index("transcode") < names.index("transcribe")
    assert names.index("transcribe") < names.index("chapters")
    assert "upscale" not in [r.name for r in results]
    workdir = tmp_path / "scratch" / "2026-07-02_v"
    assert (workdir / "upload.mp4").exists()
    assert (workdir / "chapters.txt").exists()


def test_chapters_and_metadata_skipped_when_transcribe_not_requested(tmp_path: Path) -> None:
    job = _job(tmp_path, {"transcode": True, "captions": False,
                          "chapters": True, "metadata": True})
    results = run_job(job, _config(tmp_path))
    by_name = {r.name: r for r in results}
    # chapters/metadata recorded but skipped (ok False, message mentions transcript).
    assert by_name["chapters"].ok is False
    assert "transcript" in by_name["chapters"].message.lower()
    assert not (tmp_path / "scratch" / "2026-07-02_v" / "chapters.txt").exists()


def test_write_result_shape(tmp_path: Path) -> None:
    workdir = tmp_path / "wd"
    workdir.mkdir()

    results = [StepResult("transcode", True, 0.1), StepResult("transcribe", True, 0.2)]
    path = write_result(workdir, "job1", results)
    data = json.loads(path.read_text())
    assert data["job_id"] == "job1"
    assert data["ok"] is True
    assert [s["name"] for s in data["steps"]] == ["transcode", "transcribe"]


def test_failed_step_is_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kiln import steps

    def boom(ctx):
        raise RuntimeError("kaboom")

    # Make transcode blow up; transcribe (independent) must still run and succeed.
    monkeypatch.setitem(steps.STEPS, "transcode", boom)
    job = _job(tmp_path, {"transcode": True, "captions": True})
    results = run_job(job, _config(tmp_path))
    by_name = {r.name: r for r in results}
    assert by_name["transcode"].ok is False
    assert "kaboom" in by_name["transcode"].message
    assert by_name["transcribe"].ok is True  # independent step still ran
