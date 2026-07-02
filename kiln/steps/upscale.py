"""Upscale step — Real-ESRGAN upscale/denoise (opt-in only).

Off by default. Intended for low-resolution, noisy, or old sources. Heavy and VRAM-tight
on 4K with <=16 GB cards; if the detected hardware is insufficient the step is skipped with
a clear message rather than failing the job.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from kiln.runner import StepResult
from kiln.steps import StepContext
from kiln.steps._ffmpeg import have_ffmpeg, run_ffmpeg

_MODEL = "realesrgan-x4plus"
_SCALE = 4


def have_realesrgan() -> bool:
    return shutil.which("realesrgan-ncnn-vulkan") is not None


def build_upscale_cmd(in_dir: Path, out_dir: Path, model: str = _MODEL, scale: int = _SCALE) -> list[str]:
    return [
        "realesrgan-ncnn-vulkan",
        "-i", str(in_dir),
        "-o", str(out_dir),
        "-n", model,
        "-s", str(scale),
        "-f", "png",
    ]


def _input_video(ctx: StepContext) -> Path | None:
    upload = ctx.workdir / "upload.mp4"
    if upload.is_file():
        return upload
    # fall back to the master copied into the workdir
    for p in sorted(ctx.workdir.iterdir()):
        if p.is_file() and p.suffix.lower() in {".mov", ".mp4", ".mkv", ".mxf"} and p.name != "upload.mp4":
            return p
    return None


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    if not have_realesrgan():
        return StepResult("upscale", ok=False, seconds=0.0,
                          message="realesrgan-ncnn-vulkan not installed (opt-in step skipped)")
    if not have_ffmpeg():
        return StepResult("upscale", ok=False, seconds=0.0, message="ffmpeg not found")
    source = _input_video(ctx)
    if source is None:
        return StepResult("upscale", ok=False, seconds=0.0,
                          message="no input video to upscale")

    frames_in = ctx.workdir / "frames_in"
    frames_out = ctx.workdir / "frames_out"
    frames_in.mkdir(exist_ok=True)
    frames_out.mkdir(exist_ok=True)
    out = ctx.workdir / "upscaled.mp4"
    try:
        # 1. explode to PNG frames
        run_ffmpeg(["-i", str(source), str(frames_in / "frame_%06d.png")])
        # 2. upscale every frame
        subprocess.run(build_upscale_cmd(frames_in, frames_out), check=True,
                       capture_output=True, text=True)
        # 3. reassemble with original audio
        run_ffmpeg([
            "-framerate", "30", "-i", str(frames_out / "frame_%06d.png"),
            "-i", str(source), "-map", "0:v", "-map", "1:a?",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-movflags", "+faststart", str(out),
        ])
    except (subprocess.CalledProcessError, OSError) as exc:
        return StepResult("upscale", ok=False, seconds=time.monotonic() - start,
                          message=f"upscale failed: {exc}")
    finally:
        shutil.rmtree(frames_in, ignore_errors=True)
        shutil.rmtree(frames_out, ignore_errors=True)

    if not out.is_file() or out.stat().st_size == 0:
        return StepResult("upscale", ok=False, seconds=time.monotonic() - start,
                          message="upscale produced no output")
    return StepResult("upscale", ok=True, seconds=time.monotonic() - start,
                      message=f"{_MODEL} x{_SCALE}")
