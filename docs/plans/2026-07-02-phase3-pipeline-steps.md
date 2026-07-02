# Kiln Phase 3 — Real Pipeline Steps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace kiln's six no-op passthrough steps with real implementations — ffmpeg-NVENC transcode, ffmpeg loudnorm, faster-whisper transcribe, transcript→chapters, Ollama metadata, and Real-ESRGAN (ncnn-vulkan) upscale — each satisfying the existing `run(ctx) -> StepResult` seam, TDD'd against a small committed synthetic clip, with the `STEPS` registry repointed from `noop` to the real modules.

**Architecture:** Each step module already exists as a documented stub in `kiln/steps/` and already declares `run(ctx: StepContext) -> StepResult`. Phase 3 fills in the bodies and does **not** touch the runner, queue, watcher, service, or archiver — those consume the steps through the unchanged seam. External tools are invoked as subprocesses (ffmpeg, ollama HTTP, realesrgan-ncnn-vulkan) exactly like Phase 2's design intended; faster-whisper is a Python library (CTranslate2 backend, already installed). Steps read the hardware profile (`ctx.hardware`) to make generation-aware choices (AV1 gating, Whisper tier, CUDA vs CPU) so nothing is hardcoded to the A4000. The final task flips `STEPS` in `kiln/steps/__init__.py` from the `noop` functions to the real modules' `run`, at which point the Phase 2 integration test runs the *real* pipeline end-to-end.

**Tech Stack:** Python 3.11+, `ffmpeg` (NVENC + loudnorm, subprocess), `faster-whisper` 1.x (installed), `ollama` local HTTP API (`llama3.1:8b`, subprocess or `urllib`), `realesrgan-ncnn-vulkan` (prebuilt binary, subprocess), `pytest`. No PyTorch/CUDA Python stack is added (upscale uses the Vulkan binary, transcribe uses CTranslate2).

## Global Constraints

- **NVIDIA-only, generation-aware, no hardcoded reference card.** Every step that touches the GPU consults `ctx.hardware` (a `HardwareProfile` from `kiln.hwprobe`). Transcode selects the encoder from `hardware.nvenc_av1` / `nvenc_hevc` / `nvenc_h264`; **`av1_nvenc` is used only when `hardware.nvenc_av1` is True** (compute ≥ 8.9 AND ffmpeg has the encoder) — the A4000 lists `av1_nvenc` in ffmpeg but is Ampere and will fail at encode, which is exactly why the gate reads the profile, not ffmpeg's encoder list. Whisper model tier comes from `hardware.whisper_tier()` when config is `"auto"`.
- **Software fallback is a safety net, not a default.** If NVENC is unusable (`nvenc_hevc` and `nvenc_h264` both False), transcode falls back to `libx264`/`libx265` and records that it did so in the `StepResult.message`. Never silently.
- **Steps must not raise for "expected" conditions.** A step returns `StepResult(ok=False, ...)` for a handled failure (tool missing, hardware insufficient for upscale, model unavailable). The runner's `try/except` catches truly unexpected exceptions, but steps should fail *gracefully and describptively* for the conditions named here. `upscale` on insufficient hardware returns `ok=False` with a clear skip message — it does not raise.
- **No unguarded debug output.** No stray `print()`. Subprocess stdout/stderr is captured and, on failure, folded into `StepResult.message` (truncated) — never dumped to the process's stdout.
- **Trade-secret / privacy guard.** The transcript and metadata never leave the machine — Ollama is local, no external API. (This is also a kiln design tenet: offline-first, no server dependency.)
- **Fixture is synthetic and committed (<5 MB).** All step tests run against a tiny ffmpeg-generated clip produced by a committed generator script. Text-dependent steps (transcribe/chapters/metadata) assert **structure and format** (valid `.srt`, non-empty `transcript.txt`, YouTube-timestamp lines, well-formed `metadata.md`), never exact recognized words.
- **Tests never depend on GPU, network, or heavyweight models.** GPU/ffmpeg-NVENC, real Whisper decode, the Ollama server, and the ncnn-vulkan binary are all **mocked or guarded** in the automated tests. A test may *optionally* exercise a real tool only when it is present, and must skip cleanly when absent (`pytest.mark.skipif`). CI (no GPU) must stay green.
- **Conventional commits, personal repo, GPG-signed.** Same as Phase 2. No CBB SDLC ceremony.

---

## File Structure

Every step module already exists; Phase 3 fills bodies and adds tests + two small shared helpers + a fixture generator.

| File | Responsibility | Phase 3 action |
|---|---|---|
| `tests/fixtures/make_fixture.sh` | **(new)** ffmpeg command that generates the tiny synthetic test clip (testsrc + sine). Committed; regenerable. | Create. |
| `tests/fixtures/sample.mp4` | **(new, committed)** the generated clip (<500 KB). The one committed binary Phase 3 adds. | Generate + commit. |
| `tests/conftest.py` | **(new)** shared pytest fixtures: `sample_master` (path to the clip), `step_ctx` factory (builds a `StepContext` on a tmp workdir with the clip copied in), and a `fake_hardware` factory. | Create. |
| `kiln/steps/_ffmpeg.py` | **(new)** thin shared helpers for running ffmpeg/ffprobe as subprocesses: `probe_resolution(path) -> tuple[int,int]`, `run_ffmpeg(args) -> subprocess.CompletedProcess`, and `ffprobe_json(path) -> dict`. Keeps transcode/normalize DRY. | Create. |
| `kiln/steps/transcode.py` | ffmpeg NVENC → `upload.mp4`; codec chosen from resolution + `ctx.hardware`. | Implement `run`. |
| `kiln/steps/normalize.py` | ffmpeg `loudnorm` to target LUFS; applied to `upload.mp4` (or its own audio pass). | Implement `run`. |
| `kiln/steps/transcribe.py` | faster-whisper → `captions.srt` + `transcript.txt`; model tier from config/hardware. | Implement `run`. |
| `kiln/steps/chapters.py` | transcript → `chapters.txt` (YouTube `M:SS Title` lines). Pure-Python segmentation. | Implement `run`. |
| `kiln/steps/metadata.py` | transcript → Ollama (`llama3.1:8b`) → `metadata.md` (description, titles, tags). | Implement `run`. |
| `kiln/steps/upscale.py` | realesrgan-ncnn-vulkan per-frame upscale (opt-in); graceful skip if binary/hardware absent. | Implement `run`. |
| `kiln/steps/__init__.py` | The `STEPS` registry. | Repoint from `noop` to real modules (final task). |
| `install.sh` | Add the realesrgan-ncnn-vulkan binary download + faster-whisper/ollama notes. | Extend (final task). |
| `config.example.toml` | Document that `upscale` needs the ncnn-vulkan binary + that metadata needs the ollama model pulled. | Extend (final task). |
| `tests/test_step_transcode.py` … `tests/test_step_upscale.py` | One test module per step. | Create (per task). |

**Ordering:** the runner enforces `transcode → normalize → transcribe → chapters → metadata → upscale`. Chapters and metadata **consume `transcript.txt`**; their tests write a known transcript into the workdir rather than depending on a real transcribe run. Normalize operates on `upload.mp4`, so its test seeds an `upload.mp4` (a copy of the sample) rather than depending on a real transcode.

---

## Task 1: Test fixture + shared conftest

**Files:**
- Create: `tests/fixtures/make_fixture.sh`, `tests/fixtures/sample.mp4`, `tests/conftest.py`
- Test: (this task's "test" is that the fixtures import/collect and the clip exists and is small)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `tests/fixtures/sample.mp4` — a committed ~3 s, 320×240, H.264+AAC clip, <500 KB.
  - `conftest.py` fixtures (available to all test modules):
    - `sample_master() -> Path` — returns the path to `tests/fixtures/sample.mp4`.
    - `fake_hardware(**overrides) -> HardwareProfile` — builds a `HardwareProfile`; defaults model to an Ampere-like A4000 (`nvenc_av1=False`), overridable per test.
    - `step_ctx(tmp_path, master=None, hardware=None) -> StepContext` — copies the master into a fresh `tmp_path` workdir and returns a ready `StepContext`. This is the workhorse used by every step test.

- [ ] **Step 1: Write the fixture generator script**

Create `tests/fixtures/make_fixture.sh`:

```bash
#!/usr/bin/env bash
# Generates the tiny synthetic test clip committed as sample.mp4.
# Regenerate with: bash tests/fixtures/make_fixture.sh
# ~3s, 320x240, H.264 + AAC sine tone — a few hundred KB, no copyright, no real speech.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ffmpeg -y \
  -f lavfi -i "testsrc=size=320x240:rate=30:duration=3" \
  -f lavfi -i "sine=frequency=440:duration=3" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
  "$here/sample.mp4"
echo "wrote $here/sample.mp4 ($(du -h "$here/sample.mp4" | cut -f1))"
```

- [ ] **Step 2: Generate the clip and verify it is small**

Run:
```bash
bash tests/fixtures/make_fixture.sh
test "$(stat -c%s tests/fixtures/sample.mp4)" -lt 5000000 && echo "OK <5MB"
```
Expected: prints the file size and `OK <5MB` (should be well under 500 KB).

- [ ] **Step 3: Write `tests/conftest.py`**

```python
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
```

- [ ] **Step 4: Verify the fixtures collect**

Run: `python -m pytest tests/ -q --collect-only 2>&1 | tail -3`
Expected: collection succeeds (no import errors from `conftest.py`); existing tests still listed.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/make_fixture.sh tests/fixtures/sample.mp4 tests/conftest.py
git commit -m "test(fixtures): add synthetic sample clip and shared step-test fixtures"
```

---

## Task 2: ffmpeg helpers (`kiln/steps/_ffmpeg.py`)

**Files:**
- Create: `kiln/steps/_ffmpeg.py`
- Test: `tests/test_ffmpeg_helpers.py` (create)

**Interfaces:**
- Consumes: nothing (shells out to ffmpeg/ffprobe).
- Produces:
  - `FfmpegError(RuntimeError)` — raised for a nonzero ffmpeg exit (carries truncated stderr).
  - `have_ffmpeg() -> bool` / `have_ffprobe() -> bool` — `shutil.which` checks.
  - `ffprobe_json(path: Path) -> dict` — runs `ffprobe -show_streams -show_format -of json`, returns parsed dict (`{}` on failure).
  - `probe_resolution(path: Path) -> tuple[int, int]` — returns `(width, height)` of the first video stream, or `(0, 0)` if unknown.
  - `run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess` — runs `ffmpeg -hide_banner -y <args>`, capturing output; raises `FfmpegError` on nonzero exit.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ffmpeg_helpers.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_ffmpeg_helpers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kiln.steps._ffmpeg'`.

- [ ] **Step 3: Create `kiln/steps/_ffmpeg.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ffmpeg_helpers.py -v`
Expected: PASS (on deb005 ffprobe is present so the resolution tests run; in a CI without ffprobe they skip).

- [ ] **Step 5: Commit**

```bash
git add kiln/steps/_ffmpeg.py tests/test_ffmpeg_helpers.py
git commit -m "feat(steps): add shared ffmpeg/ffprobe subprocess helpers"
```

---

## Task 3: Transcode step (`kiln/steps/transcode.py`)

**Files:**
- Modify: `kiln/steps/transcode.py`
- Test: `tests/test_step_transcode.py` (create)

**Interfaces:**
- Consumes: `StepContext`, `StepResult`, `_ffmpeg` helpers, `ctx.hardware`.
- Produces:
  - `run(ctx) -> StepResult` producing `ctx.workdir / "upload.mp4"`.
  - `select_codec(width: int, height: int, hw: HardwareProfile) -> tuple[str, list[str]]` — returns `(encoder_name, extra_ffmpeg_args)`. Rules (pure, unit-tested): resolution ≥ 4K (height ≥ 2000 or width ≥ 3800) → prefer HEVC; else H.264. On each tier pick the NVENC encoder if the matching `hardware.nvenc_*` is True, else fall back to the software encoder (`libx265`/`libx264`). **AV1 is never auto-selected** in Phase 3 (only if a future `codec="av1"` override + `hardware.nvenc_av1` — out of scope here; document it). Returns the chosen encoder and its quality args.

- [ ] **Step 1: Write the failing test for codec selection (pure logic, no ffmpeg)**

Create `tests/test_step_transcode.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_step_transcode.py -q`
Expected: FAIL — `AttributeError: module 'kiln.steps.transcode' has no attribute 'select_codec'`.

- [ ] **Step 3: Implement `transcode.py`**

Replace the stub body (keep the module docstring):

```python
from __future__ import annotations

import shutil
import time

from kiln.hwprobe import HardwareProfile
from kiln.runner import StepResult
from kiln.steps import StepContext
from kiln.steps._ffmpeg import FfmpegError, have_ffmpeg, probe_resolution, run_ffmpeg

_4K_MIN_HEIGHT = 2000
_4K_MIN_WIDTH = 3800


def select_codec(width: int, height: int, hw: HardwareProfile) -> tuple[str, list[str]]:
    """Choose (encoder, extra ffmpeg args) from resolution + hardware. AV1 not auto-selected."""
    is_4k = height >= _4K_MIN_HEIGHT or width >= _4K_MIN_WIDTH
    if is_4k:
        if hw.nvenc_hevc:
            return "hevc_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "24", "-tag:v", "hvc1"]
        return "libx265", ["-preset", "medium", "-crf", "22", "-tag:v", "hvc1"]
    if hw.nvenc_h264:
        return "h264_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "21", "-profile:v", "high"]
    return "libx264", ["-preset", "medium", "-crf", "20", "-profile:v", "high"]


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    if not have_ffmpeg():
        return StepResult("transcode", ok=False, seconds=0.0, message="ffmpeg not found")

    width, height = probe_resolution(ctx.master)
    encoder, vargs = select_codec(width, height, ctx.hardware)
    out = ctx.workdir / "upload.mp4"
    software = encoder in {"libx264", "libx265"}
    note = f"{width}x{height} -> {encoder}" + (" (software fallback)" if software else "")

    try:
        run_ffmpeg([
            "-i", str(ctx.master),
            "-c:v", encoder, *vargs,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(out),
        ])
    except FfmpegError as exc:
        return StepResult("transcode", ok=False, seconds=time.monotonic() - start, message=str(exc))

    if not out.is_file() or out.stat().st_size == 0:
        return StepResult("transcode", ok=False, seconds=time.monotonic() - start,
                          message="transcode produced no output")
    return StepResult("transcode", ok=True, seconds=time.monotonic() - start, message=note)
```

- [ ] **Step 4: Run codec-logic tests to verify they pass**

Run: `python -m pytest tests/test_step_transcode.py -q`
Expected: the four selection tests PASS.

- [ ] **Step 5: Add a real-encode test (guarded on ffmpeg; uses software encoder so it runs in CI-with-ffmpeg without a GPU)**

Append to `tests/test_step_transcode.py`:

```python
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
```

- [ ] **Step 6: Run the full transcode suite**

Run: `python -m pytest tests/test_step_transcode.py -v`
Expected: all PASS (the real-encode test runs on deb005; skips only where ffmpeg is absent).

- [ ] **Step 7: Commit**

```bash
git add kiln/steps/transcode.py tests/test_step_transcode.py
git commit -m "feat(steps): implement ffmpeg-NVENC transcode with resolution-aware codec selection"
```

---

## Task 4: Normalize step (`kiln/steps/normalize.py`)

**Files:**
- Modify: `kiln/steps/normalize.py`
- Test: `tests/test_step_normalize.py` (create)

**Interfaces:**
- Consumes: `StepContext`, `StepResult`, `_ffmpeg`, `ctx.config.target_lufs`.
- Produces:
  - `run(ctx) -> StepResult` — loudness-normalizes the audio of `ctx.workdir / "upload.mp4"` to `ctx.config.target_lufs` (default −14) using ffmpeg `loudnorm`, writing back to `upload.mp4` (via a temp file swap). If `upload.mp4` is absent (transcode didn't run/failed), returns `ok=False` with a message — normalize depends on the transcode output.
  - `_loudnorm_filter(target_lufs: int) -> str` — returns the `loudnorm=I=<t>:TP=-1.5:LRA=11` filter string (pure, unit-tested).

- [ ] **Step 1: Write the failing test**

Create `tests/test_step_normalize.py`:

```python
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
    before = (ctx.workdir / "upload.mp4").stat().st_size
    result = normalize.run(ctx)
    assert result.ok is True, result.message
    upload = ctx.workdir / "upload.mp4"
    assert upload.is_file() and upload.stat().st_size > 0
    # still a valid, readable video afterwards
    assert _ffmpeg.probe_resolution(upload) == (320, 240)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_step_normalize.py -q`
Expected: FAIL — `AttributeError: ... has no attribute '_loudnorm_filter'`.

- [ ] **Step 3: Implement `normalize.py`**

```python
from __future__ import annotations

import os
import time

from kiln.runner import StepResult
from kiln.steps import StepContext
from kiln.steps._ffmpeg import FfmpegError, have_ffmpeg, run_ffmpeg


def _loudnorm_filter(target_lufs: int) -> str:
    # TP (true-peak) -1.5 dBTP and LRA 11 are standard, conservative loudnorm defaults.
    return f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    upload = ctx.workdir / "upload.mp4"
    if not upload.is_file():
        return StepResult("normalize", ok=False, seconds=0.0,
                          message="no upload.mp4 to normalize (transcode must run first)")
    if not have_ffmpeg():
        return StepResult("normalize", ok=False, seconds=0.0, message="ffmpeg not found")

    tmp = ctx.workdir / "upload.norm.mp4"
    try:
        run_ffmpeg([
            "-i", str(upload),
            "-af", _loudnorm_filter(ctx.config.target_lufs),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(tmp),
        ])
    except FfmpegError as exc:
        tmp.unlink(missing_ok=True)
        return StepResult("normalize", ok=False, seconds=time.monotonic() - start, message=str(exc))

    if not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return StepResult("normalize", ok=False, seconds=time.monotonic() - start,
                          message="normalize produced no output")
    os.replace(tmp, upload)  # atomic swap in place
    return StepResult("normalize", ok=True, seconds=time.monotonic() - start,
                      message=f"loudnorm I={ctx.config.target_lufs}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_step_normalize.py -v`
Expected: all PASS (real test runs on deb005; the no-upload and filter tests run everywhere).

- [ ] **Step 5: Commit**

```bash
git add kiln/steps/normalize.py tests/test_step_normalize.py
git commit -m "feat(steps): implement ffmpeg loudnorm audio normalization"
```

---

## Task 5: Transcribe step (`kiln/steps/transcribe.py`)

**Files:**
- Modify: `kiln/steps/transcribe.py`
- Test: `tests/test_step_transcribe.py` (create)

**Interfaces:**
- Consumes: `StepContext`, `StepResult`, `ctx.config.whisper_model`, `ctx.hardware`, `faster_whisper`.
- Produces:
  - `run(ctx) -> StepResult` producing `captions.srt` + `transcript.txt` in `ctx.workdir`.
  - `resolve_model(config_value: str, hw: HardwareProfile) -> str` — `"auto"` → `hw.whisper_tier()`, else the explicit value (pure, unit-tested).
  - `resolve_device(hw: HardwareProfile) -> tuple[str, str]` — returns `(device, compute_type)`: `("cuda", "float16")` if `hw.cuda_available` else `("cpu", "int8")` (pure, unit-tested).
  - `_segments_to_srt(segments) -> str` — formats an iterable of objects with `.start`, `.end`, `.text` into SRT text (pure, unit-tested with fakes).

- [ ] **Step 1: Write the failing test for the pure helpers + SRT formatting**

Create `tests/test_step_transcribe.py`:

```python
"""Tests for kiln.steps.transcribe — model/device resolution, SRT formatting, mocked run."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from kiln.steps import transcribe


def test_resolve_model_auto_uses_tier(fake_hardware) -> None:
    assert transcribe.resolve_model("auto", fake_hardware(vram_mb=16376)) == "large-v3"
    assert transcribe.resolve_model("auto", fake_hardware(vram_mb=1500)) == "base"


def test_resolve_model_explicit_passthrough(fake_hardware) -> None:
    assert transcribe.resolve_model("small", fake_hardware()) == "small"


def test_resolve_device(fake_hardware) -> None:
    assert transcribe.resolve_device(fake_hardware(cuda_available=True)) == ("cuda", "float16")
    assert transcribe.resolve_device(fake_hardware(cuda_available=False)) == ("cpu", "int8")


@dataclass
class _Seg:
    start: float
    end: float
    text: str


def test_segments_to_srt_format() -> None:
    srt = transcribe._segments_to_srt([_Seg(0.0, 1.5, " Hello"), _Seg(1.5, 3.0, " world ")])
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello\n" in srt
    assert "2\n00:00:01,500 --> 00:00:03,000\nworld\n" in srt
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_step_transcribe.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'resolve_model'`.

- [ ] **Step 3: Implement `transcribe.py`**

```python
from __future__ import annotations

import time
from typing import Iterable

from kiln.hwprobe import HardwareProfile
from kiln.runner import StepResult
from kiln.steps import StepContext


def resolve_model(config_value: str, hw: HardwareProfile) -> str:
    return hw.whisper_tier() if config_value == "auto" else config_value


def resolve_device(hw: HardwareProfile) -> tuple[str, str]:
    return ("cuda", "float16") if hw.cuda_available else ("cpu", "int8")


def _fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments: Iterable) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(f"{i}\n{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}\n{seg.text.strip()}\n")
    return "\n".join(lines)


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return StepResult("transcribe", ok=False, seconds=0.0,
                          message="faster-whisper not installed")

    model_name = resolve_model(ctx.config.whisper_model, ctx.hardware)
    device, compute_type = resolve_device(ctx.hardware)
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments, _info = model.transcribe(str(ctx.master))
        segments = list(segments)  # materialize the generator once
    except Exception as exc:  # model download failure, CUDA OOM, etc. — handled, not raised
        return StepResult("transcribe", ok=False, seconds=time.monotonic() - start,
                          message=f"whisper failed: {exc}")

    (ctx.workdir / "captions.srt").write_text(_segments_to_srt(segments))
    (ctx.workdir / "transcript.txt").write_text(
        "\n".join(seg.text.strip() for seg in segments)
    )
    return StepResult("transcribe", ok=True, seconds=time.monotonic() - start,
                      message=f"{model_name} on {device}, {len(segments)} segments")
```

- [ ] **Step 4: Run the helper tests to verify they pass**

Run: `python -m pytest tests/test_step_transcribe.py -q`
Expected: the four helper tests PASS.

- [ ] **Step 5: Add a mocked `run` test (no real model download / no GPU)**

Append to `tests/test_step_transcribe.py`:

```python
def test_run_writes_srt_and_transcript_mocked(step_ctx, monkeypatch) -> None:
    # Patch WhisperModel so the test never downloads a model or touches the GPU.
    class _FakeModel:
        def __init__(self, *a, **k): ...
        def transcribe(self, path):
            segs = [_Seg(0.0, 1.0, " hello"), _Seg(1.0, 2.0, " there")]
            return iter(segs), {"language": "en"}

    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeModel, raising=True)

    ctx = step_ctx()
    result = transcribe.run(ctx)
    assert result.ok is True, result.message
    srt = (ctx.workdir / "captions.srt").read_text()
    txt = (ctx.workdir / "transcript.txt").read_text()
    assert "00:00:00,000 --> 00:00:01,000" in srt
    assert "hello" in txt and "there" in txt
```

> Note: the step imports `WhisperModel` *inside* `run` via `from faster_whisper import WhisperModel`, so patching the attribute on the `faster_whisper` module before calling `run` takes effect.

- [ ] **Step 6: Run the full transcribe suite**

Run: `python -m pytest tests/test_step_transcribe.py -v`
Expected: all PASS (no network, no GPU — the real model is never loaded).

- [ ] **Step 7: Commit**

```bash
git add kiln/steps/transcribe.py tests/test_step_transcribe.py
git commit -m "feat(steps): implement faster-whisper transcribe with VRAM-aware model tier"
```

---

## Task 6: Chapters step (`kiln/steps/chapters.py`)

**Files:**
- Modify: `kiln/steps/chapters.py`
- Test: `tests/test_step_chapters.py` (create)

**Interfaces:**
- Consumes: `StepContext`, `StepResult`, `ctx.workdir / "transcript.txt"`.
- Produces:
  - `run(ctx) -> StepResult` producing `chapters.txt` (YouTube `M:SS Title` lines; always includes a `0:00` opener). If `transcript.txt` is absent, returns `ok=False`.
  - `build_chapters(transcript: str, max_chapters: int = 8) -> list[str]` — pure segmentation: splits the transcript into up to `max_chapters` roughly-even segments and labels each with a `M:SS Title` line where the timestamp is proportional and the title is the segment's leading words. Phase 3 uses a **deterministic, transcript-length-proportional** segmentation (no LLM) — simple, testable, good enough for a first pass; a smarter topic segmentation can replace it later behind the same signature.

> **Design note:** real per-second chapter timing needs word timestamps. Phase 3's transcript is plain text (one line per segment from transcribe), so chapters here are proportional/heuristic. This is called out as a known limitation; the `build_chapters` seam lets a future task swap in timestamp-aware segmentation without touching the runner. Documented, not a placeholder.

- [ ] **Step 1: Write the failing test**

Create `tests/test_step_chapters.py`:

```python
"""Tests for kiln.steps.chapters — deterministic transcript segmentation."""

from __future__ import annotations

from kiln.steps import chapters


def test_build_chapters_starts_at_zero() -> None:
    text = "\n".join(f"line {i} words here" for i in range(20))
    out = chapters.build_chapters(text, max_chapters=4)
    assert out[0].startswith("0:00 ")
    assert len(out) <= 4
    assert all(":" in line for line in out)  # each has a timestamp


def test_build_chapters_empty_transcript() -> None:
    out = chapters.build_chapters("", max_chapters=8)
    # Always at least the opener.
    assert out == ["0:00 Intro"]


def test_run_without_transcript_fails(step_ctx) -> None:
    ctx = step_ctx()
    result = chapters.run(ctx)
    assert result.ok is False
    assert "transcript" in result.message.lower()


def test_run_writes_chapters(step_ctx) -> None:
    ctx = step_ctx()
    (ctx.workdir / "transcript.txt").write_text(
        "\n".join(f"sentence number {i} about a topic" for i in range(30))
    )
    result = chapters.run(ctx)
    assert result.ok is True, result.message
    lines = (ctx.workdir / "chapters.txt").read_text().splitlines()
    assert lines and lines[0].startswith("0:00 ")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_step_chapters.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'build_chapters'`.

- [ ] **Step 3: Implement `chapters.py`**

```python
from __future__ import annotations

import time

from kiln.runner import StepResult
from kiln.steps import StepContext

_DEFAULT_MAX = 8


def _fmt_ts(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def build_chapters(transcript: str, max_chapters: int = _DEFAULT_MAX) -> list[str]:
    """Deterministic proportional segmentation of a plain-text transcript into chapters.

    Plain-text transcript has no word timestamps, so timing is proportional to line
    position. Good enough for a first pass; a timestamp-aware version can replace this
    behind the same signature later.
    """
    lines = [ln.strip() for ln in transcript.splitlines() if ln.strip()]
    if not lines:
        return ["0:00 Intro"]

    n = max(1, min(max_chapters, len(lines)))
    # Assume ~4 seconds of speech per transcript line as a rough proportional clock.
    seconds_per_line = 4
    total_seconds = len(lines) * seconds_per_line
    chapters: list[str] = []
    for i in range(n):
        idx = (len(lines) * i) // n
        ts = 0 if i == 0 else (total_seconds * i) // n
        title = " ".join(lines[idx].split()[:6]) or f"Part {i + 1}"
        chapters.append(f"{_fmt_ts(ts)} {title}")
    return chapters


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    transcript_file = ctx.workdir / "transcript.txt"
    if not transcript_file.is_file():
        return StepResult("chapters", ok=False, seconds=0.0,
                          message="no transcript.txt (transcribe must run first)")
    chapters = build_chapters(transcript_file.read_text())
    (ctx.workdir / "chapters.txt").write_text("\n".join(chapters) + "\n")
    return StepResult("chapters", ok=True, seconds=time.monotonic() - start,
                      message=f"{len(chapters)} chapters")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_step_chapters.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add kiln/steps/chapters.py tests/test_step_chapters.py
git commit -m "feat(steps): implement transcript-to-chapters segmentation"
```

---

## Task 7: Metadata step (`kiln/steps/metadata.py`)

**Files:**
- Modify: `kiln/steps/metadata.py`
- Test: `tests/test_step_metadata.py` (create)

**Interfaces:**
- Consumes: `StepContext`, `StepResult`, `ctx.config.llm_model`, `ctx.workdir / "transcript.txt"`.
- Produces:
  - `run(ctx) -> StepResult` producing `metadata.md`. If `transcript.txt` is absent, `ok=False`. Calls the local Ollama HTTP API (`http://127.0.0.1:11434/api/generate`) with `ctx.config.llm_model`; on any connection/HTTP error returns `ok=False` with a clear message (Ollama down / model not pulled).
  - `build_prompt(transcript: str) -> str` — pure; builds the instruction prompt asking for a description, title options, and tags (unit-tested for structure).
  - `_ollama_generate(model: str, prompt: str, host: str = "127.0.0.1", port: int = 11434, timeout: float = 120) -> str` — POSTs to Ollama, returns the `response` text; raises on failure. Isolated so tests monkeypatch it.

- [ ] **Step 1: Write the failing test (mock the Ollama call)**

Create `tests/test_step_metadata.py`:

```python
"""Tests for kiln.steps.metadata — prompt building + mocked Ollama generation."""

from __future__ import annotations

from kiln.steps import metadata


def test_build_prompt_includes_transcript_and_asks_for_fields() -> None:
    p = metadata.build_prompt("a talk about lens ballistics")
    assert "lens ballistics" in p
    low = p.lower()
    assert "description" in low and "title" in low and "tag" in low


def test_run_without_transcript_fails(step_ctx) -> None:
    ctx = step_ctx()
    result = metadata.run(ctx)
    assert result.ok is False
    assert "transcript" in result.message.lower()


def test_run_writes_metadata_mocked(step_ctx, monkeypatch) -> None:
    ctx = step_ctx()
    (ctx.workdir / "transcript.txt").write_text("hello world, this is a test video")
    monkeypatch.setattr(
        metadata, "_ollama_generate",
        lambda *a, **k: "## Description\nGreat video.\n\n## Titles\n- One\n\n## Tags\ntag1, tag2",
    )
    result = metadata.run(ctx)
    assert result.ok is True, result.message
    md = (ctx.workdir / "metadata.md").read_text()
    assert "Description" in md and "Titles" in md


def test_run_reports_ollama_failure(step_ctx, monkeypatch) -> None:
    ctx = step_ctx()
    (ctx.workdir / "transcript.txt").write_text("x")

    def _boom(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(metadata, "_ollama_generate", _boom)
    result = metadata.run(ctx)
    assert result.ok is False
    assert "ollama" in result.message.lower() or "connection" in result.message.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_step_metadata.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'build_prompt'`.

- [ ] **Step 3: Implement `metadata.py`**

```python
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from kiln.runner import StepResult
from kiln.steps import StepContext

_MAX_TRANSCRIPT_CHARS = 12000  # keep the prompt within a sane context window


def build_prompt(transcript: str) -> str:
    clipped = transcript[:_MAX_TRANSCRIPT_CHARS]
    return (
        "You are helping a YouTube creator. From the transcript below, write a Markdown "
        "document with three sections:\n"
        "## Description — a compelling 2–3 paragraph video description.\n"
        "## Titles — five alternative title options as a bullet list.\n"
        "## Tags — a comma-separated list of 10–15 relevant tags.\n\n"
        "Transcript:\n"
        f"{clipped}\n"
    )


def _ollama_generate(model: str, prompt: str, host: str = "127.0.0.1",
                     port: int = 11434, timeout: float = 120) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"http://{host}:{port}/api/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    return payload.get("response", "")


def run(ctx: StepContext) -> StepResult:
    start = time.monotonic()
    transcript_file = ctx.workdir / "transcript.txt"
    if not transcript_file.is_file():
        return StepResult("metadata", ok=False, seconds=0.0,
                          message="no transcript.txt (transcribe must run first)")
    prompt = build_prompt(transcript_file.read_text())
    try:
        text = _ollama_generate(ctx.config.llm_model, prompt)
    except (urllib.error.URLError, OSError, ConnectionError, json.JSONDecodeError) as exc:
        return StepResult("metadata", ok=False, seconds=time.monotonic() - start,
                          message=f"ollama call failed (is it running, model pulled?): {exc}")
    if not text.strip():
        return StepResult("metadata", ok=False, seconds=time.monotonic() - start,
                          message="ollama returned empty response")
    (ctx.workdir / "metadata.md").write_text(text)
    return StepResult("metadata", ok=True, seconds=time.monotonic() - start,
                      message=f"{ctx.config.llm_model}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_step_metadata.py -v`
Expected: all PASS (Ollama is mocked; no server needed).

- [ ] **Step 5: Commit**

```bash
git add kiln/steps/metadata.py tests/test_step_metadata.py
git commit -m "feat(steps): implement local Ollama metadata drafting"
```

---

## Task 8: Upscale step (`kiln/steps/upscale.py`)

**Files:**
- Modify: `kiln/steps/upscale.py`
- Test: `tests/test_step_upscale.py` (create)

**Interfaces:**
- Consumes: `StepContext`, `StepResult`, `_ffmpeg`, the `realesrgan-ncnn-vulkan` binary.
- Produces:
  - `run(ctx) -> StepResult` — opt-in AI upscale. Pipeline: extract frames from `upload.mp4` (or the master) to PNGs → run `realesrgan-ncnn-vulkan` over the frame directory → reassemble to `upscaled.mp4` with the original audio. **Graceful skip (`ok=False`, not raise)** when: the binary is absent, `upload.mp4`/master missing, or hardware clearly insufficient. Because this is heavy and off-by-default, the automated test **does not run a real upscale** — it verifies the skip paths and the command construction.
  - `have_realesrgan() -> bool` — `shutil.which("realesrgan-ncnn-vulkan") is not None`.
  - `build_upscale_cmd(in_dir: Path, out_dir: Path, model: str = "realesrgan-x4plus", scale: int = 4) -> list[str]` — pure; the ncnn-vulkan argv (unit-tested).

- [ ] **Step 1: Write the failing test**

Create `tests/test_step_upscale.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_step_upscale.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'build_upscale_cmd'`.

- [ ] **Step 3: Implement `upscale.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_step_upscale.py -v`
Expected: all PASS (no real upscale; binary is `monkeypatch`ed present/absent).

- [ ] **Step 5: Commit**

```bash
git add kiln/steps/upscale.py tests/test_step_upscale.py
git commit -m "feat(steps): implement Real-ESRGAN (ncnn-vulkan) opt-in upscale with graceful skip"
```

---

## Task 9: Repoint the STEPS registry + install/config updates + full gate

**Files:**
- Modify: `kiln/steps/__init__.py` (repoint `STEPS` from `noop` to the real modules)
- Modify: `install.sh` (realesrgan-ncnn-vulkan download + ollama/whisper notes)
- Modify: `config.example.toml` (document upscale binary + ollama model prerequisites)
- Test: the existing `tests/test_integration.py` now exercises the **real** pipeline; plus a registry test.

**Interfaces:**
- Consumes: all six real step modules.
- Produces: `STEPS` mapping each step name to the **real** module's `run`. The `noop` module stays in the tree (useful for a `--dry-run` later) but is no longer wired.

- [ ] **Step 1: Write the failing test asserting STEPS points at real modules**

Append to `tests/test_runner.py`:

```python
def test_steps_registry_uses_real_modules() -> None:
    """After Phase 3, STEPS points at the real step modules, not the no-ops."""
    from kiln.steps import STEPS
    from kiln.steps import transcode, transcribe, upscale

    assert STEPS["transcode"] is transcode.run
    assert STEPS["transcribe"] is transcribe.run
    assert STEPS["upscale"] is upscale.run
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_runner.py::test_steps_registry_uses_real_modules -q`
Expected: FAIL — `STEPS["transcode"]` is still `noop.transcode`.

- [ ] **Step 3: Repoint the registry in `kiln/steps/__init__.py`**

Replace the `_build_registry` body:

```python
def _build_registry() -> dict[str, Callable[[StepContext], StepResult]]:
    """Map step name -> the real step module's run (Phase 3)."""
    from kiln.steps import chapters, metadata, normalize, transcode, transcribe, upscale

    return {
        "transcode": transcode.run,
        "normalize": normalize.run,
        "transcribe": transcribe.run,
        "chapters": chapters.run,
        "metadata": metadata.run,
        "upscale": upscale.run,
    }
```

- [ ] **Step 4: Run the registry test + confirm no import cycle**

Run: `python -m pytest tests/test_runner.py -q`
Expected: PASS. (The real step modules import from `kiln.runner` and `kiln.steps`; the registry import is lazy inside `_build_registry`, so no cycle — same pattern as Phase 2.)

- [ ] **Step 5: Update the Phase 2 integration test for the real pipeline**

The Phase 2 `test_end_to_end_*` tests dropped a fake `master.mov` (12 bytes) and asserted the no-op `upload.mp4` equalled those bytes. With real steps, that assertion is wrong. Update `tests/test_integration.py` to use the real sample clip and assert *real* outcomes. Change `_drop_master` and the byte-equality asserts:

```python
# In tests/test_integration.py — replace the fake-bytes master with the real fixture,
# and relax the assertions to "outputs exist and are non-empty" (not byte-equality).
```

Concretely, modify `_drop_master` to copy `tests/fixtures/sample.mp4` into the job folder as `master.mp4`, and in `test_end_to_end_defer_then_drain` replace:
```python
    assert (pending / "upload.mp4").read_bytes() == b"MASTER BYTES"
```
with:
```python
    assert (pending / "upload.mp4").is_file() and (pending / "upload.mp4").stat().st_size > 0
```
Guard the whole real-pipeline integration test with `@pytest.mark.skipif(not _ffmpeg.have_ffmpeg(), reason="ffmpeg required")` since it now really transcodes. Metadata will fail gracefully if Ollama is down — that's fine; assert the *job* completed and `upload.mp4`/`captions.srt` exist, not that every step succeeded.

> Full guidance for the implementer: the integration test's job.json should request only steps that work headless without network — `transcode`, `normalize`, `transcribe` (mock WhisperModel as in Task 5, or request only transcode+normalize to keep it dependency-free). Simplest: request `{"transcode": true, "normalize": true}` only, and assert `upload.mp4` exists and is a valid 320×240 video via `_ffmpeg.probe_resolution`. Leave transcribe/metadata/upscale to their own unit tests.

- [ ] **Step 6: Extend `install.sh` and `config.example.toml`**

Add to `install.sh` (a function that downloads the realesrgan-ncnn-vulkan release for Linux and installs the binary + models to `/opt/kiln/bin`, with a note that it is optional/only needed for upscale). Add to `config.example.toml` a comment block under the models section:

```toml
# The metadata step needs an Ollama model pulled locally:  ollama pull llama3.1:8b
# The upscale step (opt-in) needs the realesrgan-ncnn-vulkan binary on PATH
#   (install.sh fetches it; upscale is skipped cleanly if it is absent).
```

- [ ] **Step 7: Full-suite + lint + type + hygiene gate**

Run:
```bash
python -m pytest -q
ruff check kiln tests && python -m mypy kiln
grep -rniE 'jschwefel|/home/|coldbore|superpowers' kiln tests config.example.toml || echo CLEAN
```
Expected: all tests pass (real-tool tests run on deb005, skip in bare CI); ruff + mypy clean; hygiene grep finds nothing in source/tests/config.

- [ ] **Step 8: Commit**

```bash
git add kiln/steps/__init__.py install.sh config.example.toml tests/test_runner.py tests/test_integration.py
git commit -m "feat(steps): repoint STEPS to real implementations; wire install + config"
```

---

## Verification (Phase 3 acceptance)

- **Unit isolation:** `python -m pytest -q` — green with no network and no GPU. Real-tool tests (`transcode`/`normalize` real-encode, `_ffmpeg`) run where ffmpeg is present and `skipif`-skip where not; Whisper/Ollama/ESRGAN are mocked or skip-guarded.
- **Real transcode on deb005 (manual):** drop `tests/fixtures/sample.mp4` (or a real ProRes master) through `kiln serve --once` with `{"transcode": true, "normalize": true}` → `upload.mp4` is produced, plays, correct resolution; on a 4K master the codec is HEVC (`hevc_nvenc`), on the 320×240 sample it's H.264.
- **AV1 gating proven:** on the A4000, transcode never selects `av1_nvenc` (Ampere) even though ffmpeg lists it — `select_codec` reads `hardware.nvenc_av1` (False here). A forced `fake_hardware(nvenc_av1=True, compute_capability="8.9")` still isn't auto-selected in Phase 3 (documented limitation).
- **Real transcribe on deb005 (manual):** run transcribe on the sample → `captions.srt` is valid SRT and `transcript.txt` non-empty; `resolve_model("auto", ...)` picks `large-v3` for the A4000's VRAM.
- **Real metadata on deb005 (manual):** with `ollama` running and `llama3.1:8b` pulled, metadata on a real transcript writes a `metadata.md` with Description/Titles/Tags. With Ollama stopped, the step returns `ok=False` gracefully (job still completes).
- **Upscale skip path:** with no `realesrgan-ncnn-vulkan` on PATH, upscale returns `ok=False` with a clear message and the job is unaffected. (Real upscale is a manual check once the binary is installed.)
- **End-to-end real pipeline:** the updated integration test drops the real sample and confirms `upload.mp4` is a valid video that lands in the archive/pending-archive exactly as in Phase 2 — proving the real steps slot into the unchanged runner/service/archiver.
- **Hygiene:** no user paths / CBB refs / superpowers path in source, tests, or config.

## What Phase 3 deliberately leaves for later

- **Timestamp-aware chapters:** Phase 3 chapters are proportional/heuristic (plain-text transcript has no word timestamps). A later task can request word timestamps from faster-whisper and replace `build_chapters` behind its signature.
- **AV1 encode path:** gated off in auto mode even on capable cards; a `codec="av1"` override + `hardware.nvenc_av1` can enable it later. The A4000 can't do it anyway.
- **Upscale performance/tuning:** frame-by-frame ncnn-vulkan on 4K is slow; batching/tiling and a VRAM-fit heuristic are future work. Phase 3 delivers a correct, opt-in, gracefully-skipping implementation.
- **Service packaging (Phase 4)** and the **Mac controller spec (Phase 5)** are unchanged by Phase 3.

---

## Self-Review Notes (completed by plan author)

- **Seam integrity:** no task modifies `runner.py`, `queue.py`, `service.py`, `watcher.py`, or `archiver.py`. Every step keeps the exact `run(ctx: StepContext) -> StepResult` signature Phase 2 established; the only structural change is repointing `STEPS` (Task 9). This is verified by the Phase 2 integration test still passing (updated only for real-output assertions, not for flow).
- **No-network / no-GPU test guarantee:** transcribe mocks `WhisperModel`; metadata mocks `_ollama_generate`; upscale mocks `have_realesrgan` and never runs a real upscale; transcode/normalize real-encode tests use the **software** encoder and `skipif` on ffmpeg presence. CI without ffmpeg still collects and passes (those tests skip).
- **Graceful-failure contract:** every step returns `StepResult(ok=False, message=...)` for handled conditions (missing tool, missing input, model/host unavailable) rather than raising — matching the Global Constraint and the runner's fail-isolation model. Only truly unexpected errors propagate to the runner's `try/except`.
- **Hardware-honest, no hardcoded card:** transcode `select_codec`, transcribe `resolve_model`/`resolve_device` all read the `HardwareProfile`; the A4000 is a test default, not an assumption. AV1 is gated exactly as Phase 2's `_av1_capable` intended.
- **Type/name consistency:** `StepResult(name, ok, seconds, message)`, `StepContext(job_id, master, workdir, config, hardware)`, and the artifact filenames (`upload.mp4`, `captions.srt`, `transcript.txt`, `chapters.txt`, `metadata.md`) match Phase 2 and the archiver's `EXPORT_ARTIFACTS` exactly.
- **No placeholders:** every code step contains the real code; every test step the real test; every run step the exact command + expected result. The chapters heuristic and AV1-off are documented design limitations with a named future seam — not TODOs.
- **One committed binary:** `tests/fixtures/sample.mp4` (<500 KB) is the only binary added — within the repo's ~5 MB rule and justified as the TDD fixture (the CLAUDE hygiene rule's spirit is "no large blobs," which this respects).
