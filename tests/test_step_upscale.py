"""Tests for kiln.steps.upscale — command construction + graceful skips (no real upscale)."""

from __future__ import annotations

from pathlib import Path

from kiln.steps import upscale


def test_build_upscale_cmd_shape() -> None:
    cmd = upscale.build_upscale_cmd(Path("/in"), Path("/out"), model="realesrgan-x4plus", scale=4)
    assert cmd[0] == "realesrgan-ncnn-vulkan"
    assert "-i" in cmd and "/in" in cmd
    assert "-o" in cmd and "/out" in cmd
    assert "-n" in cmd and "realesrgan-x4plus" in cmd
    assert "-s" in cmd and "4" in cmd


def test_run_skips_when_binary_absent(step_ctx, monkeypatch) -> None:
    monkeypatch.setattr(upscale, "have_realesrgan", lambda: False)
    ctx = step_ctx()
    (ctx.workdir / "upload.mp4").write_bytes(b"fake")
    result = upscale.run(ctx)
    assert result.ok is False
    assert "realesrgan" in result.message.lower() or "not installed" in result.message.lower()


def test_run_skips_when_no_input(step_ctx, monkeypatch) -> None:
    monkeypatch.setattr(upscale, "have_realesrgan", lambda: True)
    ctx = step_ctx()  # no upload.mp4, but master exists in workdir
    # Remove the copied master too, to force the no-input path.
    for p in ctx.workdir.iterdir():
        p.unlink()
    result = upscale.run(ctx)
    assert result.ok is False
