"""Shared ffmpeg / ffprobe subprocess helpers for the transcode and normalize steps.

Keeps the two ffmpeg-driven steps DRY and centralizes error handling: a nonzero ffmpeg
exit raises FfmpegError carrying the tail of stderr, which the calling step folds into
its StepResult message.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

_STDERR_TAIL = 2000  # chars of ffmpeg stderr to keep on error


class FfmpegError(RuntimeError):
    """A ffmpeg/ffprobe invocation exited nonzero."""


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def have_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


def ffprobe_json(path: Path) -> dict:
    if not have_ffprobe():
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def probe_resolution(path: Path) -> tuple[int, int]:
    data = ffprobe_json(path)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            try:
                return int(stream["width"]), int(stream["height"])
            except (KeyError, ValueError, TypeError):
                return (0, 0)
    return (0, 0)


def run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    """Run ``ffmpeg -hide_banner -y <args>``; raise FfmpegError on nonzero exit."""
    cmd = ["ffmpeg", "-hide_banner", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise FfmpegError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-_STDERR_TAIL:]}")
    return proc
