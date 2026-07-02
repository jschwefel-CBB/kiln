# Kiln Phase 2 — Engine Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill in kiln's engine core — config loading, a crash-safe directory-of-folders processing queue, the job runner with fail-isolated dependency-ordered steps, the inbox watcher + localhost submit endpoint, the two-destination archiver with a pending-archive queue, and a working CLI — proven end-to-end with no-op passthrough steps (no GPU/ffmpeg/models needed).

**Architecture:** A job is a *folder* (master + `job.json`). Folders physically move between state subdirectories (`queued/ → processing/ → done/ | failed/`, plus `pending-archive/`) via atomic same-filesystem `os.rename`; the filesystem itself is the durable queue — no database, no lock file, human-inspectable with `ls`. The runner copies the master to a scratch workdir, runs the requested steps in dependency order (each fail-isolated), writes `result.json`, then archives the master to `$MASTERS_ARCHIVE` and the export package to `$EXPORTS_ARCHIVE` — each independently deferred to the pending-archive queue if its destination is unset or unreachable. In Phase 2 the steps are trivial built-in no-ops so the whole pipeline is testable; Phase 3 swaps real ffmpeg/Whisper/Ollama bodies into the same `run(ctx) -> StepResult` seams.

**Tech Stack:** Python 3.11+ (stdlib `tomllib`, `pathlib`, `shutil`, `os`, `json`, `http.server`, `threading`, `argparse`), `watchdog` for filesystem events, `pytest` for tests. No external services in Phase 2.

## Global Constraints

- **Python floor: 3.11** — `tomllib` is stdlib on 3.11+; the `tomli` fallback import already exists in `config.py` for <3.11 but the project targets 3.11+ (`requires-python = ">=3.11"` in `pyproject.toml`). Do not raise the floor.
- **`inbox`, `scratch_dir`, and `state_dir` MUST be on the same filesystem.** The queue moves job folders (including the multi-GB master) between `inbox`/`state` subdirs and into `scratch` by `os.rename`, which is atomic and instant only within one filesystem. `os.rename` across filesystems raises `OSError: [Errno 18] EXDEV`. This is a documented requirement enforced by `kiln doctor` and surfaced in `config.example.toml`. (The two archive destinations MAY be on other filesystems — the archiver uses `shutil.move`, which copies across filesystems.)
- **No user-specific paths or secrets in committed source or tests.** No `/home/jschwefel`, no real hostnames, no `/opt/kiln` hardcoded in source (it may appear only in docs/packaging). Tests use `tmp_path` fixtures exclusively.
- **No `superpowers/` path anywhere.** Plans live in `docs/plans/`, specs in `docs/specs/`. (Global prohibition — overrides the writing-plans skill's default `docs/superpowers/plans/` location.)
- **NVIDIA-only, generation-aware, no hardcoded reference card.** `hwprobe.probe()` detects the actual GPU; the A4000 is one detected config, never an assumption. AV1 is gated on Ada/RTX-40+ (compute capability ≥ 8.9). VRAM drives the Whisper tier.
- **No unguarded debug output.** No stray `print()` in library code; user-facing CLI output is fine in `cli.py`, and logging goes through the stdlib `logging` module or the per-job `job.log`.
- **Conventional commits.** `type(scope): description`. This is a personal repo under personal git identity — commits are GPG-signed by the environment's configured key; no CBB SDLC ceremony applies.
- **The two-destination archive is authoritative.** There is no single `$ARCHIVE`. Everywhere the older design plan says `$ARCHIVE`, the truth is two paths: `masters_archive` (master) and `exports_archive` (export package). The `Config` dataclass, `archiver.py`, and `cli.py` docstrings already reflect this.

---

## File Structure

Phase 2 modifies the existing stub modules in place (they already declare the seams) and adds a new steps module plus tests. Nothing is created from scratch except `kiln/steps/noop.py`, `kiln/paths.py`, and the test files.

| File | Responsibility | Phase 2 action |
|---|---|---|
| `kiln/config.py` | Load + validate `config.toml`, apply `KILN_*` env overrides, produce `Config`. | Implement `load()`; add validation + `state_dir`/archive parsing. |
| `kiln/paths.py` | **(new)** Own the `state_dir` layout: the names of the queued/processing/done/failed/pending-archive subdirs and helpers to create them. Single source of truth for the directory scheme so queue + archiver + CLI agree. | Create. |
| `kiln/queue.py` | Disk-backed strict-sequential FIFO. `enqueue` moves a folder into `queued/`; `dequeue` moves the oldest into `processing/`; recovery re-queues an orphan left in `processing/` after a crash. | Implement `ProcessingQueue`; extend `Job`. |
| `kiln/hwprobe.py` | Runtime capability probe (nvidia-smi + ffmpeg -encoders), `whisper_tier()`. | Implement `probe()` + `whisper_tier()`; tolerate no-GPU hosts. |
| `kiln/steps/noop.py` | **(new)** Trivial built-in steps used by the Phase 2 runner: copy master → `upload.mp4`, write placeholder artifacts. Proves the pipeline with zero external deps. | Create. |
| `kiln/steps/__init__.py` | Step protocol + `StepContext` (already defined). | Add a `STEPS` registry mapping step name → callable (Phase 2 wires no-ops; Phase 3 repoints to real modules). |
| `kiln/runner.py` | Copy master to scratch workdir, run requested steps in `STEP_ORDER` (fail-isolated), write `result.json`, return results. | Implement `run_job()` + `result.json` writer. |
| `kiln/archiver.py` | Move master → `masters_archive/<job_id>/`, export package → `exports_archive/<job_id>/`, each deferred to `pending-archive/` if unset/unreachable. Drainer retries. | Implement `archive_or_defer()` + `drain_pending()`. |
| `kiln/watcher.py` | Watch `inbox` for complete job folders; run a localhost-only HTTP submit endpoint; both enqueue. | Implement `watch()` + submit server + a `_JobStableDetector`. |
| `kiln/service.py` | **(new)** The long-running loop: start watcher (producer thread) + drain-loop + the consumer loop that dequeues → runs → archives. This is what `kiln.service` (systemd) executes. | Create. |
| `kiln/cli.py` | `kiln run <folder>`, `kiln status`, `kiln doctor`, and a `kiln serve` entry that calls `service.run()`. | Implement all four command bodies. |
| `tests/test_config.py` | Config loading, env overrides, validation, same-filesystem check. | Create. |
| `tests/test_queue.py` | enqueue/dequeue ordering, atomic move, crash recovery. | Create. |
| `tests/test_archiver.py` | Both destinations, deferral when unset, drain, `keep_master`. | Create. |
| `tests/test_runner.py` | Dependency order, fail-isolation, `result.json` shape. | Create. |
| `tests/test_watcher.py` | Stable-file detection, submit endpoint enqueues. | Create. |
| `tests/test_cli.py` | `doctor` output + checks, `status` output, `run` enqueues. | Create. |
| `tests/test_integration.py` | Full drop→queue→run→result→defer→drain round-trip with no-op steps. | Create. |

**Note on `state_dir`:** the scaffold's `Config` does not yet carry `state_dir` as a loaded field from TOML (it is a dataclass field but `config.example.toml` has `state_dir = "/var/lib/kiln/state"`). Task 1 wires it through.

---

## Task 1: Config loading, validation, and env overrides

**Files:**
- Modify: `kiln/config.py`
- Test: `tests/test_config.py` (create)

**Interfaces:**
- Consumes: nothing (leaf).
- Produces: `Config` dataclass (already declared) with all fields populated; `load(path) -> Config` fully implemented. Fields relied on by later tasks: `inbox: Path`, `scratch_dir: Path`, `state_dir: Path`, `masters_archive: Path | None`, `exports_archive: Path | None`, `whisper_model: str`, `llm_model: str`, `codec: str`, `target_lufs: int`, `jobs: dict[str, bool]`, `submit_host: str`, `submit_port: int`. Plus two new fields: `prune_masters: bool = False`, `retention_days: int = 90` (present in `config.example.toml`, consumed by Task 14 later; parse them now so the config round-trips).

- [ ] **Step 1: Write the failing test for a minimal valid config**

Create `tests/test_config.py`:

```python
"""Tests for kiln.config — TOML loading, validation, env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from kiln.config import Config, load


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body)
    return p


MINIMAL = """
inbox = "{d}/inbox"
scratch_dir = "{d}/scratch"
state_dir = "{d}/state"
masters_archive = ""
exports_archive = ""
"""


def test_load_minimal(tmp_path: Path) -> None:
    cfg = load(_write(tmp_path, MINIMAL.format(d=tmp_path)))
    assert isinstance(cfg, Config)
    assert cfg.inbox == tmp_path / "inbox"
    assert cfg.scratch_dir == tmp_path / "scratch"
    assert cfg.state_dir == tmp_path / "state"
    # Empty-string archive paths mean "not configured".
    assert cfg.masters_archive is None
    assert cfg.exports_archive is None
    # Defaults applied.
    assert cfg.whisper_model == "auto"
    assert cfg.llm_model == "llama3.1:8b"
    assert cfg.codec == "auto"
    assert cfg.target_lufs == -14
    assert cfg.prune_masters is False
    assert cfg.retention_days == 90
    assert cfg.submit_host == "127.0.0.1"
    assert cfg.submit_port == 8765
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_config.py::test_load_minimal -v`
Expected: FAIL with `NotImplementedError: config.load is implemented in Phase 2 (engine core)`.

- [ ] **Step 3: Implement `load()`**

Replace the `load()` body in `kiln/config.py`. Keep the existing module docstring and the `tomllib`/`tomli` import block. Add `import os` at the top.

```python
def _as_path(value: str) -> Path | None:
    """Turn a TOML string into a Path, treating "" as 'not configured' (None)."""
    value = value.strip()
    return Path(value).expanduser() if value else None


def load(path: str | Path = "config.toml") -> Config:
    """Load and validate configuration from ``path`` (TOML), applying env overrides.

    Environment variables of the form ``KILN_<KEY>`` (upper-case field name) override
    the corresponding file value, e.g. ``KILN_INBOX=/data/inbox``.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    def get(key: str, default: str | None = None) -> str | None:
        env = os.environ.get(f"KILN_{key.upper()}")
        if env is not None:
            return env
        return raw.get(key, default)

    # Required storage paths.
    inbox = get("inbox")
    scratch = get("scratch_dir")
    state = get("state_dir")
    for name, val in (("inbox", inbox), ("scratch_dir", scratch), ("state_dir", state)):
        if not val:
            raise ValueError(f"config: '{name}' is required and must be non-empty")

    # Archive destinations may be "" (None => hold in the pending-archive queue).
    masters = _as_path(get("masters_archive", "") or "")
    exports = _as_path(get("exports_archive", "") or "")

    # [jobs] table of per-step toggles (a job.json sidecar overrides per submission).
    jobs = dict(raw.get("jobs", {}))

    # [submit] endpoint (env KILN_SUBMIT_HOST / KILN_SUBMIT_PORT override).
    submit = raw.get("submit", {})
    submit_host = os.environ.get("KILN_SUBMIT_HOST", submit.get("host", "127.0.0.1"))
    submit_port = int(os.environ.get("KILN_SUBMIT_PORT", submit.get("port", 8765)))

    cfg = Config(
        inbox=Path(inbox).expanduser(),
        scratch_dir=Path(scratch).expanduser(),
        state_dir=Path(state).expanduser(),
        masters_archive=masters,
        exports_archive=exports,
        whisper_model=get("whisper_model", "auto") or "auto",
        llm_model=get("llm_model", "llama3.1:8b") or "llama3.1:8b",
        codec=get("codec", "auto") or "auto",
        target_lufs=int(get("target_lufs", "-14") or -14),
        jobs=jobs,
        submit_host=submit_host,
        submit_port=submit_port,
    )
    cfg.prune_masters = str(get("prune_masters", "false")).strip().lower() in {"true", "1", "yes"} \
        if isinstance(raw.get("prune_masters"), str) else bool(raw.get("prune_masters", False))
    cfg.retention_days = int(get("retention_days", "90") or 90)
    return cfg
```

Add the two new fields to the `Config` dataclass (after `submit_port`):

```python
    prune_masters: bool = False
    retention_days: int = 90
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_config.py::test_load_minimal -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test for env overrides + configured archives**

Append to `tests/test_config.py`:

```python
def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KILN_INBOX", str(tmp_path / "override-inbox"))
    monkeypatch.setenv("KILN_TARGET_LUFS", "-16")
    cfg = load(_write(tmp_path, MINIMAL.format(d=tmp_path)))
    assert cfg.inbox == tmp_path / "override-inbox"
    assert cfg.target_lufs == -16


def test_configured_archives(tmp_path: Path) -> None:
    body = MINIMAL.format(d=tmp_path).replace(
        'masters_archive = ""', f'masters_archive = "{tmp_path}/m"'
    ).replace(
        'exports_archive = ""', f'exports_archive = "{tmp_path}/e"'
    )
    cfg = load(_write(tmp_path, body))
    assert cfg.masters_archive == tmp_path / "m"
    assert cfg.exports_archive == tmp_path / "e"


def test_missing_required_raises(tmp_path: Path) -> None:
    body = 'scratch_dir = "{d}/s"\nstate_dir = "{d}/st"\nmasters_archive = ""\nexports_archive = ""\n'.format(d=tmp_path)
    with pytest.raises(ValueError, match="inbox"):
        load(_write(tmp_path, body))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "does-not-exist.toml")
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: all PASS (implementation already covers these paths).

- [ ] **Step 7: Commit**

```bash
git add kiln/config.py tests/test_config.py
git commit -m "feat(config): implement TOML load, env overrides, and validation"
```

---

## Task 2: State-directory layout (`kiln/paths.py`)

**Files:**
- Create: `kiln/paths.py`
- Test: `tests/test_queue.py` (create — begins here; extended in Task 3)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `QUEUED, PROCESSING, DONE, FAILED, PENDING = "queued", "processing", "done", "failed", "pending-archive"` (module constants).
  - `class StateLayout` with `__init__(self, state_dir: Path)`; properties `.queued`, `.processing`, `.done`, `.failed`, `.pending` each returning the corresponding `Path`; and `.ensure() -> None` which `mkdir(parents=True, exist_ok=True)` all five. Consumed by `ProcessingQueue` (Task 3), `archiver` (Task 5), `cli` (Task 8), `service` (Task 9).

- [ ] **Step 1: Write the failing test**

Create `tests/test_queue.py`:

```python
"""Tests for kiln.paths (state layout) and kiln.queue (directory-of-folders FIFO)."""

from __future__ import annotations

from pathlib import Path

from kiln.paths import StateLayout


def test_state_layout_creates_all_subdirs(tmp_path: Path) -> None:
    layout = StateLayout(tmp_path / "state")
    layout.ensure()
    for sub in ("queued", "processing", "done", "failed", "pending-archive"):
        assert (tmp_path / "state" / sub).is_dir()


def test_state_layout_properties(tmp_path: Path) -> None:
    layout = StateLayout(tmp_path / "state")
    assert layout.queued == tmp_path / "state" / "queued"
    assert layout.processing == tmp_path / "state" / "processing"
    assert layout.done == tmp_path / "state" / "done"
    assert layout.failed == tmp_path / "state" / "failed"
    assert layout.pending == tmp_path / "state" / "pending-archive"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kiln.paths'`.

- [ ] **Step 3: Create `kiln/paths.py`**

```python
"""State-directory layout — the single source of truth for the queue's on-disk scheme.

kiln's queue is the filesystem: a job is a folder that physically moves between these
subdirectories of ``state_dir``. Every component (queue, archiver, CLI, service) agrees
on the scheme through this module so the names live in exactly one place.

    state_dir/
      queued/            jobs waiting to be processed (FIFO by mtime)
      processing/        the single job currently running (at most one)
      done/              successfully processed and fully archived
      failed/            processing failed; kept with its job.log, never archived
      pending-archive/   processed OK but a destination was unreachable; drainer retries
"""

from __future__ import annotations

from pathlib import Path

QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
FAILED = "failed"
PENDING = "pending-archive"

_SUBDIRS = (QUEUED, PROCESSING, DONE, FAILED, PENDING)


class StateLayout:
    """Resolves and creates the ``state_dir`` subdirectories."""

    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def queued(self) -> Path:
        return self._root / QUEUED

    @property
    def processing(self) -> Path:
        return self._root / PROCESSING

    @property
    def done(self) -> Path:
        return self._root / DONE

    @property
    def failed(self) -> Path:
        return self._root / FAILED

    @property
    def pending(self) -> Path:
        return self._root / PENDING

    def ensure(self) -> None:
        """Create every state subdirectory (idempotent)."""
        for sub in _SUBDIRS:
            (self._root / sub).mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_queue.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kiln/paths.py tests/test_queue.py
git commit -m "feat(paths): add state-directory layout for the folder queue"
```

---

## Task 3: Processing queue (`kiln/queue.py`)

**Files:**
- Modify: `kiln/queue.py`
- Test: `tests/test_queue.py` (extend)

**Interfaces:**
- Consumes: `StateLayout` (Task 2).
- Produces:
  - `Job` dataclass extended to `Job(job_id: str, folder: Path)` where `folder` is the job folder's *current* location. Add a classmethod `Job.from_folder(folder: Path) -> Job` that derives `job_id` from `folder.name`.
  - `ProcessingQueue(state_dir: Path)` with:
    - `enqueue(self, folder: Path) -> Job` — move `folder` into `queued/` via `os.rename`; return the `Job` at its new location. (If `folder` is already inside `queued/`, treat as a no-op re-adopt.)
    - `dequeue(self) -> Job | None` — move the oldest folder (by mtime) from `queued/` into `processing/`; return its `Job`, or `None` if `queued/` is empty. Enforces strict-sequential: raises `RuntimeError` if `processing/` already holds a job.
    - `recover(self) -> Job | None` — on startup, if a job was left in `processing/` by a crash, move it back to the *front* of `queued/` (so it re-runs) and return it; else `None`.
    - `current(self) -> Job | None` — the job in `processing/`, if any.
  Consumed by `service` (Task 9), `cli status` (Task 8), `watcher` (Task 6).

- [ ] **Step 1: Write the failing test for enqueue/dequeue ordering**

Append to `tests/test_queue.py`:

```python
import os
import time

from kiln.queue import Job, ProcessingQueue


def _make_job_folder(parent: Path, name: str) -> Path:
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / "job.json").write_text("{}")
    (folder / "master.mov").write_bytes(b"fake master")
    return folder


def test_enqueue_moves_folder_into_queued(tmp_path: Path) -> None:
    q = ProcessingQueue(tmp_path / "state")
    src = _make_job_folder(tmp_path / "inbox", "2026-07-02_a")
    job = q.enqueue(src)
    assert not src.exists()  # moved, not copied
    assert job.folder == tmp_path / "state" / "queued" / "2026-07-02_a"
    assert job.folder.is_dir()
    assert (job.folder / "master.mov").read_bytes() == b"fake master"
    assert job.job_id == "2026-07-02_a"


def test_dequeue_is_fifo_by_mtime(tmp_path: Path) -> None:
    q = ProcessingQueue(tmp_path / "state")
    first = q.enqueue(_make_job_folder(tmp_path / "inbox", "first"))
    time.sleep(0.01)
    second = q.enqueue(_make_job_folder(tmp_path / "inbox", "second"))
    # Force distinct, ordered mtimes regardless of filesystem granularity.
    os.utime(first.folder, (1000, 1000))
    os.utime(second.folder, (2000, 2000))

    got = q.dequeue()
    assert got is not None and got.job_id == "first"
    assert got.folder == tmp_path / "state" / "processing" / "first"


def test_dequeue_empty_returns_none(tmp_path: Path) -> None:
    q = ProcessingQueue(tmp_path / "state")
    assert q.dequeue() is None


def test_strict_sequential_blocks_second_dequeue(tmp_path: Path) -> None:
    import pytest

    q = ProcessingQueue(tmp_path / "state")
    q.enqueue(_make_job_folder(tmp_path / "inbox", "a"))
    q.enqueue(_make_job_folder(tmp_path / "inbox", "b"))
    q.dequeue()  # moves 'a' (or 'b') into processing/
    with pytest.raises(RuntimeError, match="processing"):
        q.dequeue()


def test_recover_requeues_orphan(tmp_path: Path) -> None:
    q = ProcessingQueue(tmp_path / "state")
    job = q.enqueue(_make_job_folder(tmp_path / "inbox", "crashed"))
    q.dequeue()  # now in processing/
    assert q.current() is not None
    recovered = q.recover()
    assert recovered is not None and recovered.job_id == "crashed"
    assert recovered.folder == tmp_path / "state" / "queued" / "crashed"
    assert q.current() is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_queue.py -v`
Expected: FAIL with `NotImplementedError: ProcessingQueue is implemented in Phase 2` (and `AttributeError` for `from_folder`/`recover`/`current`).

- [ ] **Step 3: Implement `queue.py`**

Replace the body of `kiln/queue.py` (keep the module docstring):

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from kiln.paths import StateLayout


@dataclass
class Job:
    """A queued unit of work: a job folder (master + job.json) on local disk."""

    job_id: str
    folder: Path

    @classmethod
    def from_folder(cls, folder: Path) -> "Job":
        folder = Path(folder)
        return cls(job_id=folder.name, folder=folder)


class ProcessingQueue:
    """Disk-backed FIFO with exactly-once, strict-sequential semantics.

    The queue is the filesystem: enqueue/dequeue are atomic ``os.rename`` moves of the
    job folder between ``state_dir`` subdirectories, so state survives a crash with no
    database and no lock file. Requires ``state_dir`` to share a filesystem with the
    source folders (same-filesystem rename); enforced by ``kiln doctor``.
    """

    def __init__(self, state_dir: Path) -> None:
        self._layout = StateLayout(state_dir)
        self._layout.ensure()

    def enqueue(self, folder: Path) -> Job:
        folder = Path(folder)
        dest = self._layout.queued / folder.name
        if folder.resolve() == dest.resolve():
            return Job.from_folder(dest)  # already queued; adopt in place
        os.rename(folder, dest)
        return Job.from_folder(dest)

    def _oldest_queued(self) -> Path | None:
        entries = [p for p in self._layout.queued.iterdir() if p.is_dir()]
        if not entries:
            return None
        return min(entries, key=lambda p: p.stat().st_mtime)

    def current(self) -> Job | None:
        entries = [p for p in self._layout.processing.iterdir() if p.is_dir()]
        return Job.from_folder(entries[0]) if entries else None

    def dequeue(self) -> Job | None:
        if self.current() is not None:
            raise RuntimeError("cannot dequeue: a job is already in processing/")
        oldest = self._oldest_queued()
        if oldest is None:
            return None
        dest = self._layout.processing / oldest.name
        os.rename(oldest, dest)
        return Job.from_folder(dest)

    def recover(self) -> Job | None:
        """Re-queue a job orphaned in processing/ by a crash. Returns it, or None."""
        cur = self.current()
        if cur is None:
            return None
        dest = self._layout.queued / cur.folder.name
        os.rename(cur.folder, dest)
        # Make it the oldest so it re-runs first.
        os.utime(dest, (0, 0))
        return Job.from_folder(dest)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_queue.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add kiln/queue.py tests/test_queue.py
git commit -m "feat(queue): implement crash-safe directory-of-folders processing queue"
```

---

## Task 4: No-op steps and the step registry

**Files:**
- Create: `kiln/steps/noop.py`
- Modify: `kiln/steps/__init__.py`
- Test: `tests/test_runner.py` (create — begins here; extended in Task 5b)

**Interfaces:**
- Consumes: `StepContext` (already defined in `kiln/steps/__init__.py`), `StepResult` (already in `kiln/runner.py`).
- Produces:
  - `kiln/steps/noop.py` module with `run(ctx: StepContext) -> StepResult` and named per-capability callables `transcode`, `normalize`, `transcribe`, `chapters`, `metadata`, `upscale` — each a thin no-op that produces the expected output file(s) in `ctx.workdir` so the pipeline is observable end-to-end without ffmpeg/models. Output filenames (relied on by the runner + integration test): `upload.mp4`, `captions.srt`, `transcript.txt`, `chapters.txt`, `metadata.md`.
  - `STEPS: dict[str, Callable[[StepContext], StepResult]]` in `kiln/steps/__init__.py` — maps each step name in `STEP_ORDER` to a callable. In Phase 2 every entry points at the `noop` implementation; Phase 3 repoints entries to the real modules. Consumed by `runner.run_job` (Task 5b).

- [ ] **Step 1: Write the failing test for a no-op step producing its artifact**

Create `tests/test_runner.py`:

```python
"""Tests for kiln.steps.noop, the STEPS registry, and kiln.runner."""

from __future__ import annotations

from pathlib import Path

from kiln.config import Config
from kiln.hwprobe import HardwareProfile
from kiln.steps import StepContext


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
    from kiln.steps import noop

    ctx = _ctx(tmp_path)
    result = noop.transcode(ctx)
    assert result.ok is True
    assert result.name == "transcode"
    assert (ctx.workdir / "upload.mp4").exists()
    # No-op transcode copies the master bytes so the artifact is non-empty.
    assert (ctx.workdir / "upload.mp4").read_bytes() == b"fake master bytes"


def test_noop_transcribe_produces_srt_and_transcript(tmp_path: Path) -> None:
    from kiln.steps import noop

    ctx = _ctx(tmp_path)
    result = noop.transcribe(ctx)
    assert result.ok is True
    assert (ctx.workdir / "captions.srt").exists()
    assert (ctx.workdir / "transcript.txt").exists()


def test_steps_registry_covers_every_ordered_step() -> None:
    from kiln.runner import STEP_ORDER
    from kiln.steps import STEPS

    for step in STEP_ORDER:
        assert step in STEPS, f"{step} missing from STEPS registry"
        assert callable(STEPS[step])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_runner.py -v`
Expected: FAIL — `ImportError: cannot import name 'noop'` and `cannot import name 'STEPS'`.

- [ ] **Step 3: Create `kiln/steps/noop.py`**

```python
"""No-op passthrough steps for Phase 2.

These stand in for the real GPU/AI steps (Phase 3) so the whole pipeline —
queue → runner → result.json → archiver → pending-archive → drain — is testable with
zero ffmpeg/CUDA/model dependency. Each produces the same output *filenames* the real
steps will, so the runner, result.json, and the archiver's export-package logic are all
exercised for real. Phase 3 repoints the STEPS registry from here to the real modules.
"""

from __future__ import annotations

import shutil
import time

from kiln.runner import StepResult
from kiln.steps import StepContext

_PLACEHOLDER = "kiln no-op placeholder (Phase 2)\n"


def _timed(name: str, work) -> StepResult:
    start = time.monotonic()
    work()
    return StepResult(name=name, ok=True, seconds=time.monotonic() - start)


def transcode(ctx: StepContext) -> StepResult:
    """Stand-in transcode: copy the master to upload.mp4 (non-empty, observable)."""
    return _timed("transcode", lambda: shutil.copyfile(ctx.master, ctx.workdir / "upload.mp4"))


def normalize(ctx: StepContext) -> StepResult:
    """Stand-in loudness normalize: no-op (audio handled with the real transcode later)."""
    return _timed("normalize", lambda: None)


def transcribe(ctx: StepContext) -> StepResult:
    def _work() -> None:
        (ctx.workdir / "captions.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n" + _PLACEHOLDER
        )
        (ctx.workdir / "transcript.txt").write_text(_PLACEHOLDER)

    return _timed("transcribe", _work)


def chapters(ctx: StepContext) -> StepResult:
    return _timed(
        "chapters",
        lambda: (ctx.workdir / "chapters.txt").write_text("00:00 Intro\n"),
    )


def metadata(ctx: StepContext) -> StepResult:
    return _timed(
        "metadata",
        lambda: (ctx.workdir / "metadata.md").write_text("# Draft metadata\n" + _PLACEHOLDER),
    )


def upscale(ctx: StepContext) -> StepResult:
    """Opt-in upscale: no-op stand-in (real Real-ESRGAN in Phase 3)."""
    return _timed("upscale", lambda: None)


def run(ctx: StepContext) -> StepResult:
    """Default entry (unused by the runner, which calls the named functions via STEPS)."""
    return transcode(ctx)
```

- [ ] **Step 4: Add the `STEPS` registry to `kiln/steps/__init__.py`**

Append to `kiln/steps/__init__.py` (after the existing `Step` protocol). Import `noop` lazily inside a function to avoid a circular import at module load (`noop` imports `StepContext` from this package):

```python
from typing import Callable


def _build_registry() -> dict[str, Callable[[StepContext], StepResult]]:
    """Map step name -> callable. Phase 2 wires no-ops; Phase 3 repoints to real modules."""
    from kiln.steps import noop

    return {
        "transcode": noop.transcode,
        "normalize": noop.normalize,
        "transcribe": noop.transcribe,
        "chapters": noop.chapters,
        "metadata": noop.metadata,
        "upscale": noop.upscale,
    }


STEPS: dict[str, Callable[[StepContext], StepResult]] = _build_registry()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_runner.py -v`
Expected: the three tests PASS.

- [ ] **Step 6: Confirm the smoke tests still pass (registry import didn't break structure)**

Run: `python -m pytest tests/test_smoke.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add kiln/steps/noop.py kiln/steps/__init__.py tests/test_runner.py
git commit -m "feat(steps): add no-op passthrough steps and the STEPS registry"
```

---

## Task 5a: Hardware probe (`kiln/hwprobe.py`)

**Files:**
- Modify: `kiln/hwprobe.py`
- Test: `tests/test_hwprobe.py` (create)

**Interfaces:**
- Consumes: nothing (shells out to `nvidia-smi` and `ffmpeg`).
- Produces: `probe() -> HardwareProfile` fully implemented; `HardwareProfile.whisper_tier() -> str` implemented. `whisper_tier` mapping (relied on by `transcribe` in Phase 3 and reported by `doctor`): VRAM ≥ 10000 MiB → `"large-v3"`; ≥ 5000 → `"medium"`; ≥ 2000 → `"small"`; otherwise → `"base"`; no VRAM detected → `"base"` (CPU fallback territory). AV1 gate: `nvenc_av1` True only when compute capability ≥ 8.9 **and** `av1_nvenc` is in the ffmpeg encoder list.

- [ ] **Step 1: Write the failing test (pure logic, no real GPU needed)**

Create `tests/test_hwprobe.py`:

```python
"""Tests for kiln.hwprobe — VRAM->model tiering and AV1 gating logic.

probe() itself shells out to nvidia-smi/ffmpeg and is environment-dependent, so these
tests target the pure decision logic, constructing HardwareProfile directly.
"""

from __future__ import annotations

import pytest

from kiln.hwprobe import HardwareProfile


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_hwprobe.py -v`
Expected: FAIL with `NotImplementedError: whisper_tier is implemented in Phase 2`.

- [ ] **Step 3: Implement `whisper_tier` and `probe`**

Replace the `whisper_tier` body and the `probe` function in `kiln/hwprobe.py`. Add imports `import re`, `import shutil`, `import subprocess` at the top.

```python
    def whisper_tier(self) -> str:
        """Return the largest Whisper model that fits detected VRAM ("auto" logic)."""
        vram = self.vram_mb or 0
        if vram >= 10000:
            return "large-v3"
        if vram >= 5000:
            return "medium"
        if vram >= 2000:
            return "small"
        return "base"
```

```python
def _run(cmd: list[str]) -> str:
    """Run a command, returning stdout ('' on any failure — probe must never raise)."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _ffmpeg_encoders() -> tuple[str, ...]:
    if shutil.which("ffmpeg") is None:
        return ()
    text = _run(["ffmpeg", "-hide_banner", "-encoders"])
    found = []
    for token in ("h264_nvenc", "hevc_nvenc", "av1_nvenc", "libx264", "libx265"):
        if token in text:
            found.append(token)
    return tuple(found)


def _av1_capable(compute_capability: str | None, encoders: tuple[str, ...]) -> bool:
    """AV1 NVENC exists on Ada/RTX-40+ (compute capability >= 8.9) AND ffmpeg must have it."""
    if "av1_nvenc" not in encoders or not compute_capability:
        return False
    try:
        major, minor = (int(x) for x in compute_capability.split("."))
    except ValueError:
        return False
    return (major, minor) >= (8, 9)


def probe() -> HardwareProfile:
    """Probe the host and return a :class:`HardwareProfile`. Never raises."""
    encoders = _ffmpeg_encoders()

    gpu_name: str | None = None
    vram_mb: int | None = None
    compute_capability: str | None = None
    cuda_available = False

    if shutil.which("nvidia-smi") is not None:
        q = _run([
            "nvidia-smi",
            "--query-gpu=name,memory.total,compute_cap",
            "--format=csv,noheader,nounits",
        ])
        line = q.strip().splitlines()[0] if q.strip() else ""
        if line:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                gpu_name = parts[0] or None
                m = re.search(r"\d+", parts[1])
                vram_mb = int(m.group()) if m else None
                cuda_available = True
            if len(parts) >= 3 and re.match(r"^\d+\.\d+$", parts[2]):
                compute_capability = parts[2]

    return HardwareProfile(
        gpu_name=gpu_name,
        vram_mb=vram_mb,
        compute_capability=compute_capability,
        nvenc_h264="h264_nvenc" in encoders,
        nvenc_hevc="hevc_nvenc" in encoders,
        nvenc_av1=_av1_capable(compute_capability, encoders),
        cuda_available=cuda_available,
        ffmpeg_encoders=encoders,
    )
```

- [ ] **Step 4: Run the tier tests to verify they pass**

Run: `python -m pytest tests/test_hwprobe.py -v`
Expected: PASS.

- [ ] **Step 5: Add an AV1-gate unit test and a probe-never-raises test**

Append to `tests/test_hwprobe.py`:

```python
from kiln.hwprobe import _av1_capable, probe


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
```

- [ ] **Step 6: Run all hwprobe tests**

Run: `python -m pytest tests/test_hwprobe.py -v`
Expected: all PASS (on deb005 `probe()` will actually find the A4000; in a GPU-less CI it returns Nones — both are valid).

- [ ] **Step 7: Commit**

```bash
git add kiln/hwprobe.py tests/test_hwprobe.py
git commit -m "feat(hwprobe): implement nvidia-smi/ffmpeg probe with AV1 gating and VRAM tiering"
```

---

## Task 5b: Job runner (`kiln/runner.py`)

**Files:**
- Modify: `kiln/runner.py`
- Test: `tests/test_runner.py` (extend)

**Interfaces:**
- Consumes: `Config`, `Job`, `HardwareProfile`, `StepContext`, `STEPS` (Task 4), `STEP_ORDER` (already defined).
- Produces:
  - `run_job(job: Job, config: Config, hardware: HardwareProfile | None = None) -> list[StepResult]` — fully implemented. Reads `job.json` from `job.folder`, determines which steps to run (job.json `jobs` map overrides `config.jobs`; a step absent from both defaults to off), copies the master into a scratch workdir, runs steps in `STEP_ORDER` fail-isolated, writes `result.json` into the workdir, returns results. **Dependency rule:** if `transcribe` did not succeed, `chapters` and `metadata` are skipped (recorded as skipped, not failed).
  - `write_result(workdir: Path, job_id: str, results: list[StepResult]) -> Path` — helper that serializes `result.json`; returns its path. `result.json` shape (relied on by `cli status` and the integration test): `{"job_id": str, "ok": bool, "steps": [{"name", "ok", "seconds", "message"}, ...]}` where top-level `ok` is True iff every *attempted* (non-skipped) step succeeded.
  - `_JOB_TO_STEPS: dict[str, str]` — maps a job.json capability key to its step name where they differ. The only difference: job key `"captions"` → step `"transcribe"`. All others map to themselves.
  - `resolve_workdir(config: Config, job_id: str) -> Path` — returns `config.scratch_dir / job_id` (created).

- [ ] **Step 1: Write the failing test for dependency ordering and skip-on-missing-transcript**

Append to `tests/test_runner.py`:

```python
import json

from kiln.queue import Job
from kiln.runner import run_job, write_result


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
    from kiln.runner import StepResult

    results = [StepResult("transcode", True, 0.1), StepResult("transcribe", True, 0.2)]
    path = write_result(workdir, "job1", results)
    data = json.loads(path.read_text())
    assert data["job_id"] == "job1"
    assert data["ok"] is True
    assert [s["name"] for s in data["steps"]] == ["transcode", "transcribe"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_runner.py -k "run_job or write_result or skipped" -v`
Expected: FAIL with `NotImplementedError: runner.run_job is implemented in Phases 2-3` and `ImportError` for `write_result`.

- [ ] **Step 3: Implement the runner**

Replace the `run_job` function in `kiln/runner.py` and add the helpers. Keep the module docstring, the `StepResult` dataclass, and `STEP_ORDER`. Add imports at the top: `import json`, `import shutil`, `from kiln.hwprobe import HardwareProfile, probe`, `from kiln.steps import STEPS, StepContext`.

```python
# Maps a job.json capability key to the step name where they differ.
_JOB_TO_STEPS: dict[str, str] = {"captions": "transcribe"}

# The reverse, for reading the toggle that governs each step.
_STEP_TO_JOB: dict[str, str] = {v: k for k, v in _JOB_TO_STEPS.items()}


def resolve_workdir(config: Config, job_id: str) -> Path:
    workdir = config.scratch_dir / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _wanted(step: str, toggles: dict[str, bool]) -> bool:
    """Is this step requested? Look up its job.json key; default off if absent."""
    key = _STEP_TO_JOB.get(step, step)
    return bool(toggles.get(key, False))


def write_result(workdir: Path, job_id: str, results: list[StepResult]) -> Path:
    # A skipped step (dependency not met) has a message beginning "skipped"; it does not
    # count against the job's success. Everything else is an attempt.
    attempted = [r for r in results if not r.message.startswith("skipped")]
    ok = all(r.ok for r in attempted) if attempted else True
    payload = {
        "job_id": job_id,
        "ok": ok,
        "steps": [
            {"name": r.name, "ok": r.ok, "seconds": round(r.seconds, 3), "message": r.message}
            for r in results
        ],
    }
    out = workdir / "result.json"
    out.write_text(json.dumps(payload, indent=2))
    return out


def run_job(job: Job, config: Config, hardware: HardwareProfile | None = None) -> list[StepResult]:
    """Run all requested steps for ``job`` in dependency order; return per-step results."""
    hardware = hardware or probe()
    job_file = job.folder / "job.json"
    spec = json.loads(job_file.read_text()) if job_file.is_file() else {}
    toggles: dict[str, bool] = dict(config.jobs)
    toggles.update(spec.get("jobs", {}))

    # Copy the master into the scratch workdir (processing is fully local).
    master_src = next((p for p in job.folder.iterdir() if p.suffix.lower() in
                       {".mov", ".mp4", ".mxf", ".mkv"}), None)
    if master_src is None:
        raise FileNotFoundError(f"no master video found in {job.folder}")
    workdir = resolve_workdir(config, job.job_id)
    master = workdir / master_src.name
    shutil.copyfile(master_src, master)

    ctx = StepContext(job_id=job.job_id, master=master, workdir=workdir,
                      config=config, hardware=hardware)

    results: list[StepResult] = []
    transcribe_ok = False
    for step in STEP_ORDER:
        if not _wanted(step, toggles):
            continue
        # Dependency gate: chapters/metadata need a successful transcript.
        if step in {"chapters", "metadata"} and not transcribe_ok:
            results.append(StepResult(step, ok=False, seconds=0.0,
                                      message="skipped: requires transcript (transcribe not run/failed)"))
            continue
        try:
            result = STEPS[step](ctx)
        except Exception as exc:  # fail-isolated: record and continue
            result = StepResult(step, ok=False, seconds=0.0, message=f"error: {exc}")
        results.append(result)
        if step == "transcribe" and result.ok:
            transcribe_ok = True

    write_result(workdir, job.job_id, results)
    return results
```

- [ ] **Step 4: Run the runner tests to verify they pass**

Run: `python -m pytest tests/test_runner.py -v`
Expected: all PASS.

- [ ] **Step 5: Add a fail-isolation test (a step raising doesn't abort the rest)**

Append to `tests/test_runner.py`:

```python
def test_failed_step_is_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pytest as _pytest  # local alias to satisfy linters if reordered

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
```

Add `import pytest` at the top of `tests/test_runner.py` if not already present.

- [ ] **Step 6: Run the full runner suite**

Run: `python -m pytest tests/test_runner.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add kiln/runner.py tests/test_runner.py
git commit -m "feat(runner): implement fail-isolated, dependency-ordered job runner"
```

---

## Task 6: Two-destination archiver + pending-archive drainer (`kiln/archiver.py`)

**Files:**
- Modify: `kiln/archiver.py`
- Test: `tests/test_archiver.py` (create)

**Interfaces:**
- Consumes: `Config`, `StateLayout` (Task 2).
- Produces:
  - `archive_or_defer(job_dir: Path, config: Config) -> bool` — fully implemented. `job_dir` is a processed job folder (contains the master + a `workdir`-produced export package; see note below on layout). Moves the **master** to `masters_archive/<job_id>/` and the **export package** (upload.mp4 + artifacts + result.json) to `exports_archive/<job_id>/`, each **only if** that destination is set and writable; anything that can't be placed is written into a manifest and the whole job folder is moved to `state/pending-archive/<job_id>/`. Returns True iff **both** halves archived immediately (nothing deferred).
  - `drain_pending(config: Config) -> int` — scans `state/pending-archive/`, retries each via the same placement logic, returns the count fully archived (and removed from pending) this pass.
  - `_reachable(path: Path | None) -> bool` — a destination is reachable iff it is not None, exists (or its parent can be created), and is writable.
  - Constant `EXPORT_ARTIFACTS = ("upload.mp4", "captions.srt", "transcript.txt", "chapters.txt", "metadata.md", "result.json", "job.log")` — the files that constitute the export package.

**Job-folder layout at archive time (contract with the service, Task 9):** by the time `archive_or_defer` is called, the service has assembled a single job folder containing the original `master.*` **and** the produced export artifacts side by side (the service copies the workdir outputs next to the master before archiving). So `archive_or_defer` splits one folder into two destinations by filename: the master by extension, the export package by the `EXPORT_ARTIFACTS` allowlist.

- [ ] **Step 1: Write the failing test for both-destinations-set (immediate archive)**

Create `tests/test_archiver.py`:

```python
"""Tests for kiln.archiver — two-destination archive + pending-archive drain."""

from __future__ import annotations

import json
from pathlib import Path

from kiln.archiver import archive_or_defer, drain_pending
from kiln.config import Config


def _config(tmp_path: Path, masters, exports) -> Config:
    return Config(
        inbox=tmp_path / "inbox",
        scratch_dir=tmp_path / "scratch",
        state_dir=tmp_path / "state",
        masters_archive=masters,
        exports_archive=exports,
    )


def _assembled_job(tmp_path: Path, job_id: str = "2026-07-02_v") -> Path:
    """A processed job folder: master + export artifacts side by side."""
    folder = tmp_path / "state" / "processing" / job_id
    folder.mkdir(parents=True)
    (folder / "master.mov").write_bytes(b"MASTER")
    (folder / "upload.mp4").write_bytes(b"UPLOAD")
    (folder / "captions.srt").write_text("caps")
    (folder / "transcript.txt").write_text("script")
    (folder / "chapters.txt").write_text("00:00 Intro")
    (folder / "metadata.md").write_text("# meta")
    (folder / "result.json").write_text(json.dumps({"job_id": job_id, "ok": True}))
    return folder


def test_archive_both_destinations(tmp_path: Path) -> None:
    masters = tmp_path / "masters"
    exports = tmp_path / "exports"
    cfg = _config(tmp_path, masters, exports)
    job = _assembled_job(tmp_path)

    fully = archive_or_defer(job, cfg)
    assert fully is True
    # Master went to masters_archive/<job_id>/.
    assert (masters / "2026-07-02_v" / "master.mov").read_bytes() == b"MASTER"
    # Export package went to exports_archive/<job_id>/.
    assert (exports / "2026-07-02_v" / "upload.mp4").read_bytes() == b"UPLOAD"
    assert (exports / "2026-07-02_v" / "captions.srt").exists()
    assert (exports / "2026-07-02_v" / "result.json").exists()
    # The master did NOT leak into exports, nor artifacts into masters.
    assert not (exports / "2026-07-02_v" / "master.mov").exists()
    assert not (masters / "2026-07-02_v" / "upload.mp4").exists()
    # Source job folder consumed.
    assert not job.exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_archiver.py -v`
Expected: FAIL with `NotImplementedError: archiver.archive_or_defer is implemented in Phase 2`.

- [ ] **Step 3: Implement the archiver**

Replace the bodies in `kiln/archiver.py` (keep the module docstring). Add imports: `import json`, `import os`, `import shutil`, `from kiln.paths import StateLayout`.

```python
EXPORT_ARTIFACTS = (
    "upload.mp4", "captions.srt", "transcript.txt",
    "chapters.txt", "metadata.md", "result.json", "job.log",
)
_MASTER_SUFFIXES = {".mov", ".mp4", ".mxf", ".mkv"}


def _reachable(path: Path | None) -> bool:
    """A destination is usable iff set, creatable, and writable."""
    if path is None:
        return False
    try:
        path.mkdir(parents=True, exist_ok=True)
        return os.access(path, os.W_OK)
    except OSError:
        return False


def _master_in(folder: Path) -> Path | None:
    # upload.mp4 is an artifact, not the master — exclude it explicitly.
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in _MASTER_SUFFIXES and p.name != "upload.mp4":
            return p
    return None


def _place(files: list[Path], dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.move(str(f), str(dest_dir / f.name))


def archive_or_defer(job_dir: Path, config: Config) -> bool:
    """Split ``job_dir`` to its two destinations; defer whatever can't be placed."""
    job_dir = Path(job_dir)
    job_id = job_dir.name
    layout = StateLayout(config.state_dir)
    layout.ensure()

    master = _master_in(job_dir)
    artifacts = [job_dir / name for name in EXPORT_ARTIFACTS if (job_dir / name).is_file()]

    masters_ok = _reachable(config.masters_archive)
    exports_ok = _reachable(config.exports_archive)

    deferred: list[str] = []

    if master is not None:
        if masters_ok:
            _place([master], config.masters_archive / job_id)  # type: ignore[operator]
        else:
            deferred.append("master")

    if artifacts:
        if exports_ok:
            _place(artifacts, config.exports_archive / job_id)  # type: ignore[operator]
        else:
            deferred.append("exports")

    if not deferred:
        # Everything placed; drop the now-empty job folder.
        shutil.rmtree(job_dir, ignore_errors=True)
        return True

    # Something couldn't be placed — hold the whole (remaining) job in pending-archive.
    manifest = {"job_id": job_id, "deferred": deferred}
    (job_dir / "pending.json").write_text(json.dumps(manifest, indent=2))
    dest = layout.pending / job_id
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    os.rename(job_dir, dest)
    return False


def drain_pending(config: Config) -> int:
    """Retry deferred jobs; return the count fully archived this pass."""
    layout = StateLayout(config.state_dir)
    layout.ensure()
    drained = 0
    for job_dir in sorted(p for p in layout.pending.iterdir() if p.is_dir()):
        # Remove any stale manifest so a fully-archived job isn't misjudged.
        (job_dir / "pending.json").unlink(missing_ok=True)
        if archive_or_defer(job_dir, config):
            drained += 1
    return drained
```

- [ ] **Step 4: Run the both-destinations test to verify it passes**

Run: `python -m pytest tests/test_archiver.py -v`
Expected: PASS.

- [ ] **Step 5: Write failing tests for deferral + drain + partial (one destination set)**

Append to `tests/test_archiver.py`:

```python
def test_defer_when_both_unset(tmp_path: Path) -> None:
    cfg = _config(tmp_path, None, None)
    job = _assembled_job(tmp_path)
    fully = archive_or_defer(job, cfg)
    assert fully is False
    pending = tmp_path / "state" / "pending-archive" / "2026-07-02_v"
    assert pending.is_dir()
    assert (pending / "master.mov").exists()
    assert (pending / "upload.mp4").exists()
    assert (pending / "pending.json").exists()


def test_partial_archive_masters_only(tmp_path: Path) -> None:
    masters = tmp_path / "masters"
    cfg = _config(tmp_path, masters, None)  # exports unset
    job = _assembled_job(tmp_path)
    fully = archive_or_defer(job, cfg)
    assert fully is False
    # Master archived immediately...
    assert (masters / "2026-07-02_v" / "master.mov").exists()
    # ...exports held in pending (master no longer there).
    pending = tmp_path / "state" / "pending-archive" / "2026-07-02_v"
    assert (pending / "upload.mp4").exists()
    assert not (pending / "master.mov").exists()


def test_drain_completes_when_destinations_appear(tmp_path: Path) -> None:
    # First defer with nothing configured.
    cfg_unset = _config(tmp_path, None, None)
    archive_or_defer(_assembled_job(tmp_path), cfg_unset)
    assert (tmp_path / "state" / "pending-archive" / "2026-07-02_v").is_dir()

    # Now point both destinations and drain.
    masters = tmp_path / "masters"
    exports = tmp_path / "exports"
    cfg_set = _config(tmp_path, masters, exports)
    count = drain_pending(cfg_set)
    assert count == 1
    assert (masters / "2026-07-02_v" / "master.mov").exists()
    assert (exports / "2026-07-02_v" / "upload.mp4").exists()
    assert not (tmp_path / "state" / "pending-archive" / "2026-07-02_v").exists()
```

- [ ] **Step 6: Run the full archiver suite**

Run: `python -m pytest tests/test_archiver.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add kiln/archiver.py tests/test_archiver.py
git commit -m "feat(archiver): implement two-destination archive with pending-archive drain"
```

---

## Task 7: Inbox watcher + localhost submit endpoint (`kiln/watcher.py`)

**Files:**
- Modify: `kiln/watcher.py`
- Test: `tests/test_watcher.py` (create)

**Interfaces:**
- Consumes: `ProcessingQueue` (Task 3).
- Produces:
  - `is_stable(folder: Path, min_age_seconds: float = 2.0) -> bool` — True iff `folder` contains a `job.json` **and** a master file **and** no file inside has an mtime newer than `min_age_seconds` ago (so a still-copying master isn't enqueued mid-write). Pure, unit-testable with `os.utime`.
  - `scan_once(inbox: Path, queue: ProcessingQueue, min_age_seconds: float = 2.0) -> list[str]` — enqueue every stable, not-yet-queued folder in `inbox`; return the job_ids enqueued. (Idempotent: a folder already moved out of `inbox` won't re-enqueue.)
  - `make_submit_server(queue: ProcessingQueue, inbox: Path, host: str, port: int) -> http.server.ThreadingHTTPServer` — a localhost-only HTTP server; a `POST /submit` triggers a `scan_once`; returns the server (caller runs `.serve_forever()` in a thread). Bound strictly to `host` (default `127.0.0.1`).
  - `watch(inbox: Path, queue: ProcessingQueue, min_age_seconds: float = 2.0, poll_seconds: float = 2.0) -> None` — blocking loop that periodically `scan_once`es the inbox (watchdog-driven when available, else a poll). Consumed by `service` (Task 9). *Polling is the Phase 2 baseline; watchdog is an optimization the service may layer on.*

Rationale for poll-based `watch`: a folder drop over SMB produces many filesystem events; a debounced periodic `scan_once` with the `is_stable` age-gate is simpler and more robust than reacting to raw events, and it needs no watchdog in the test path.

- [ ] **Step 1: Write the failing test for stability detection**

Create `tests/test_watcher.py`:

```python
"""Tests for kiln.watcher — stable-folder detection, scan, and submit endpoint."""

from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path

from kiln.queue import ProcessingQueue
from kiln.watcher import is_stable, make_submit_server, scan_once


def _drop(inbox: Path, name: str, *, complete: bool = True) -> Path:
    folder = inbox / name
    folder.mkdir(parents=True)
    if complete:
        (folder / "job.json").write_text("{}")
        (folder / "master.mov").write_bytes(b"data")
    return folder


def test_is_stable_requires_job_and_master(tmp_path: Path) -> None:
    folder = _drop(tmp_path / "inbox", "incomplete", complete=False)
    (folder / "job.json").write_text("{}")  # master missing
    # Age the file so the age-gate isn't the reason it's unstable.
    os.utime(folder / "job.json", (1000, 1000))
    assert is_stable(folder, min_age_seconds=0.0) is False


def test_is_stable_true_for_aged_complete_folder(tmp_path: Path) -> None:
    folder = _drop(tmp_path / "inbox", "ready")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))
    assert is_stable(folder, min_age_seconds=2.0) is True


def test_is_stable_false_for_fresh_write(tmp_path: Path) -> None:
    folder = _drop(tmp_path / "inbox", "fresh")
    # Files just written; newer than the age gate.
    assert is_stable(folder, min_age_seconds=5.0) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_watcher.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_stable'` (module currently only defines `watch`, which raises).

- [ ] **Step 3: Implement the watcher**

Replace the body of `kiln/watcher.py` (keep the docstring). Full implementation:

```python
from __future__ import annotations

import http.server
import json
import time
from pathlib import Path

from kiln.queue import ProcessingQueue


def is_stable(folder: Path, min_age_seconds: float = 2.0) -> bool:
    """True iff the folder is a complete job and nothing was written very recently."""
    folder = Path(folder)
    if not (folder / "job.json").is_file():
        return False
    has_master = any(
        p.is_file() and p.suffix.lower() in {".mov", ".mp4", ".mxf", ".mkv"} and p.name != "upload.mp4"
        for p in folder.iterdir()
    )
    if not has_master:
        return False
    cutoff = time.time() - min_age_seconds
    newest = max((p.stat().st_mtime for p in folder.iterdir()), default=0.0)
    return newest <= cutoff


def scan_once(inbox: Path, queue: ProcessingQueue, min_age_seconds: float = 2.0) -> list[str]:
    """Enqueue every stable job folder currently in ``inbox``; return enqueued job_ids."""
    inbox = Path(inbox)
    enqueued: list[str] = []
    if not inbox.is_dir():
        return enqueued
    for folder in sorted(p for p in inbox.iterdir() if p.is_dir()):
        if is_stable(folder, min_age_seconds):
            job = queue.enqueue(folder)
            enqueued.append(job.job_id)
    return enqueued


def make_submit_server(
    queue: ProcessingQueue, inbox: Path, host: str, port: int
) -> http.server.ThreadingHTTPServer:
    """Build a localhost-only HTTP server whose POST /submit triggers an inbox scan."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server naming)
            if self.path.rstrip("/") == "/submit":
                enqueued = scan_once(inbox, queue)
                body = json.dumps({"enqueued": enqueued}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args) -> None:  # silence default stderr logging
            return

    return http.server.ThreadingHTTPServer((host, port), _Handler)


def watch(
    inbox: Path,
    queue: ProcessingQueue,
    min_age_seconds: float = 2.0,
    poll_seconds: float = 2.0,
) -> None:
    """Block, periodically scanning ``inbox`` and enqueuing complete job folders."""
    while True:
        scan_once(inbox, queue, min_age_seconds)
        time.sleep(poll_seconds)
```

- [ ] **Step 4: Run the stability tests to verify they pass**

Run: `python -m pytest tests/test_watcher.py -v`
Expected: the three `is_stable` tests PASS.

- [ ] **Step 5: Write failing tests for scan_once and the submit endpoint**

Append to `tests/test_watcher.py`:

```python
def test_scan_once_enqueues_stable_folder(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    folder = _drop(inbox, "job-a")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))
    q = ProcessingQueue(tmp_path / "state")
    enqueued = scan_once(inbox, q, min_age_seconds=2.0)
    assert enqueued == ["job-a"]
    assert (tmp_path / "state" / "queued" / "job-a").is_dir()
    assert not folder.exists()  # moved out of inbox


def test_submit_endpoint_triggers_scan(tmp_path: Path) -> None:
    import threading

    inbox = tmp_path / "inbox"
    folder = _drop(inbox, "job-b")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))
    q = ProcessingQueue(tmp_path / "state")
    server = make_submit_server(q, inbox, "127.0.0.1", 0)  # port 0 = OS-assigned
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/submit", method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read())
        assert payload["enqueued"] == ["job-b"]
    finally:
        server.shutdown()
        server.server_close()
    assert (tmp_path / "state" / "queued" / "job-b").is_dir()
```

Add `import json` to the test file's imports (top).

- [ ] **Step 6: Run the full watcher suite**

Run: `python -m pytest tests/test_watcher.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add kiln/watcher.py tests/test_watcher.py
git commit -m "feat(watcher): implement inbox scan, stability gate, and localhost submit endpoint"
```

---

## Task 8: Service loop (`kiln/service.py`)

**Files:**
- Create: `kiln/service.py`
- Test: `tests/test_integration.py` (create — the service is exercised through the end-to-end test)

**Interfaces:**
- Consumes: `Config`, `ProcessingQueue` (Task 3), `run_job` (Task 5b), `archive_or_defer`/`drain_pending` (Task 6), `scan_once` (Task 7), `StateLayout` (Task 2).
- Produces:
  - `process_one(config: Config, queue: ProcessingQueue) -> str | None` — dequeue one job, run it, assemble the job folder (copy workdir artifacts next to the master), archive-or-defer it, then move the folder marker to `done/` (or `failed/` on a processing exception). Returns the processed job_id, or `None` if the queue was empty. This is the single testable "advance the pipeline by one job" unit.
  - `run(config: Config, *, once: bool = False) -> None` — the long-running entry: recover any crashed job, start the submit server + poll loop in a producer thread, then loop `process_one` + periodic `drain_pending`. `once=True` runs a single producer-scan + `process_one` + `drain` and returns (used by tests and `kiln serve --once`).

**Assembly detail (implements the archiver's layout contract from Task 6):** after `run_job` writes outputs into `scratch/<job_id>/`, `process_one` copies every produced artifact (the `EXPORT_ARTIFACTS` that exist) from the scratch workdir back **next to the master** in the job folder (currently under `processing/<job_id>/`), then calls `archive_or_defer` on that folder. This is why the archiver sees master + artifacts side by side.

- [ ] **Step 1: Write the failing end-to-end test (defer path, then drain)**

Create `tests/test_integration.py`:

```python
"""End-to-end integration: drop -> queue -> run (no-op steps) -> result -> defer -> drain."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from kiln.config import Config
from kiln.queue import ProcessingQueue
from kiln.service import process_one
from kiln.watcher import scan_once


def _config(tmp_path: Path, masters=None, exports=None) -> Config:
    cfg = Config(
        inbox=tmp_path / "inbox",
        scratch_dir=tmp_path / "scratch",
        state_dir=tmp_path / "state",
        masters_archive=masters,
        exports_archive=exports,
        jobs={"transcode": True, "captions": True, "normalize": True,
              "chapters": True, "metadata": True, "upscale": False},
    )
    for d in (cfg.inbox, cfg.scratch_dir, cfg.state_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


def _drop_master(inbox: Path, job_id: str) -> Path:
    folder = inbox / job_id
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({"job_id": job_id}))
    (folder / "master.mov").write_bytes(b"MASTER BYTES")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))
    return folder


def test_end_to_end_defer_then_drain(tmp_path: Path) -> None:
    # Archives unset -> processed job must land in pending-archive.
    cfg = _config(tmp_path)
    _drop_master(cfg.inbox, "2026-07-02_demo")
    q = ProcessingQueue(cfg.state_dir)

    assert scan_once(cfg.inbox, q) == ["2026-07-02_demo"]
    job_id = process_one(cfg, q)
    assert job_id == "2026-07-02_demo"

    pending = cfg.state_dir / "pending-archive" / "2026-07-02_demo"
    assert pending.is_dir()
    # Real pipeline outputs are present (produced by the no-op steps).
    assert (pending / "upload.mp4").read_bytes() == b"MASTER BYTES"
    assert (pending / "captions.srt").exists()
    assert (pending / "transcript.txt").exists()
    assert (pending / "chapters.txt").exists()
    assert (pending / "metadata.md").exists()
    result = json.loads((pending / "result.json").read_text())
    assert result["ok"] is True

    # Now configure destinations and drain.
    from kiln.archiver import drain_pending

    cfg2 = _config(tmp_path, masters=tmp_path / "masters", exports=tmp_path / "exports")
    assert drain_pending(cfg2) == 1
    assert (tmp_path / "masters" / "2026-07-02_demo" / "master.mov").exists()
    assert (tmp_path / "exports" / "2026-07-02_demo" / "upload.mp4").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_integration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kiln.service'`.

- [ ] **Step 3: Create `kiln/service.py`**

```python
"""The kiln service loop: producer (inbox watch + submit) + consumer (run + archive).

Strict-sequential: exactly one job is processed at a time. On startup a job orphaned in
``processing/`` by a crash is recovered and re-queued. Completed jobs are archived to the
two destinations (or deferred to the pending-archive queue) and their folder is retired to
``done/`` (or ``failed/`` if processing raised).
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from kiln.archiver import EXPORT_ARTIFACTS, archive_or_defer, drain_pending
from kiln.config import Config
from kiln.paths import StateLayout
from kiln.queue import ProcessingQueue
from kiln.runner import run_job
from kiln.watcher import make_submit_server, scan_once


def _assemble(job_folder: Path, workdir: Path) -> None:
    """Copy produced artifacts from the scratch workdir back next to the master."""
    for name in EXPORT_ARTIFACTS:
        src = workdir / name
        if src.is_file():
            shutil.copyfile(src, job_folder / name)


def process_one(config: Config, queue: ProcessingQueue) -> str | None:
    """Advance the pipeline by exactly one job. Returns the job_id, or None if idle."""
    job = queue.dequeue()
    if job is None:
        return None

    layout = StateLayout(config.state_dir)
    layout.ensure()
    workdir = config.scratch_dir / job.job_id

    try:
        run_job(job, config)                       # writes outputs into workdir + result.json
        _assemble(job.folder, workdir)             # artifacts now beside the master
        archive_or_defer(job.folder, config)       # split to the two destinations / defer
        # If archive_or_defer deferred, job.folder was moved to pending-archive/ and no
        # longer exists here; if it fully archived, the folder was removed. Either way the
        # processing/ slot is now clear. Nothing left to retire to done/ in the defer case;
        # record a done marker only when the folder was fully consumed.
        if not job.folder.exists():
            (layout.done / job.job_id).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        # Processing failed: move the job folder to failed/ with a log; never archive.
        dest = layout.failed / job.job_id
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        if job.folder.exists():
            (job.folder / "job.log").write_text(f"processing failed: {exc}\n")
            import os
            os.rename(job.folder, dest)
        else:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "job.log").write_text(f"processing failed: {exc}\n")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)   # free scratch
    return job.job_id


def run(config: Config, *, once: bool = False) -> None:
    """Recover, then run the producer + consumer loops. ``once`` does a single pass."""
    layout = StateLayout(config.state_dir)
    layout.ensure()
    queue = ProcessingQueue(config.state_dir)
    queue.recover()  # re-queue a crashed job, if any

    if once:
        scan_once(config.inbox, queue)
        process_one(config, queue)
        drain_pending(config)
        return

    server = make_submit_server(queue, config.inbox, config.submit_host, config.submit_port)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    last_drain = 0.0
    try:
        while True:
            scan_once(config.inbox, queue)
            processed = process_one(config, queue)
            now = time.monotonic()
            if now - last_drain > 30 or processed is None:
                drain_pending(config)
                last_drain = now
            if processed is None:
                time.sleep(2.0)  # idle backoff
    finally:
        server.shutdown()
        server.server_close()
```

- [ ] **Step 4: Run the integration test to verify it passes**

Run: `python -m pytest tests/test_integration.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test for the immediate-archive path and the failed path**

Append to `tests/test_integration.py`:

```python
def test_end_to_end_immediate_archive(tmp_path: Path) -> None:
    cfg = _config(tmp_path, masters=tmp_path / "masters", exports=tmp_path / "exports")
    _drop_master(cfg.inbox, "vid1")
    q = ProcessingQueue(cfg.state_dir)
    scan_once(cfg.inbox, q)
    assert process_one(cfg, q) == "vid1"
    # Straight to the two archives; nothing pending.
    assert (tmp_path / "masters" / "vid1" / "master.mov").exists()
    assert (tmp_path / "exports" / "vid1" / "upload.mp4").exists()
    assert not (cfg.state_dir / "pending-archive" / "vid1").exists()
    # done/ marker recorded.
    assert (cfg.state_dir / "done" / "vid1").exists()
    # scratch freed.
    assert not (cfg.scratch_dir / "vid1").exists()


def test_processing_failure_goes_to_failed(tmp_path: Path, monkeypatch) -> None:
    from kiln import steps

    def boom(ctx):
        raise RuntimeError("gpu exploded")

    # Make the very first step raise via the registry; the runner isolates it, but force a
    # hard failure by breaking the assemble contract instead: point transcode at a raiser
    # AND require it, so no upload.mp4 is produced. Processing itself still completes, so to
    # exercise the failed/ path we make run_job raise by removing the master mid-flight.
    cfg = _config(tmp_path, masters=tmp_path / "masters", exports=tmp_path / "exports")
    folder = _drop_master(cfg.inbox, "bad1")
    q = ProcessingQueue(cfg.state_dir)
    scan_once(cfg.inbox, q)

    # Remove the master from the queued folder so run_job raises FileNotFoundError.
    queued = cfg.state_dir / "queued" / "bad1"
    if queued.exists():
        (queued / "master.mov").unlink()
    else:  # already in processing (shouldn't be, but be defensive)
        pass

    assert process_one(cfg, q) == "bad1"
    assert (cfg.state_dir / "failed" / "bad1").is_dir()
    assert (cfg.state_dir / "failed" / "bad1" / "job.log").exists()
    assert not (tmp_path / "masters" / "bad1").exists()  # never archived
```

- [ ] **Step 6: Run the full integration suite**

Run: `python -m pytest tests/test_integration.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add kiln/service.py tests/test_integration.py
git commit -m "feat(service): implement strict-sequential process loop with recover and drain"
```

---

## Task 9: CLI command bodies (`kiln/cli.py`)

**Files:**
- Modify: `kiln/cli.py`
- Test: `tests/test_cli.py` (create)

**Interfaces:**
- Consumes: `load` (Task 1), `probe` (Task 5a), `ProcessingQueue` (Task 3), `run_job`/service pieces, `StateLayout` (Task 2), `service.run` (Task 8).
- Produces: `main(argv)` fully implemented for four subcommands. New `serve` subcommand added to `_build_parser` (with `--once`). Command behaviors:
  - `kiln doctor` — load config; print detected GPU/VRAM/compute-capability/NVENC(+AV1) from `probe()` and the resolved Whisper tier; verify `inbox` writable, `scratch_dir` writable, `state_dir` writable, that all three share a filesystem (same `st_dev`), and report each archive destination as configured+reachable / configured+unreachable / unset. Exit code 0 if inbox/scratch/state are all OK, 1 otherwise (archives unset is NOT a failure).
  - `kiln status` — count folders in each state subdir (`queued`, `processing`, `pending-archive`, `done`, `failed`) and list the most recent few job_ids per bucket.
  - `kiln run <folder>` — enqueue a folder directly (via `ProcessingQueue.enqueue`), print the job_id.
  - `kiln serve [--once]` — call `service.run(config, once=...)`.
  - Add `_load_config(args)` helper that calls `config.load(args.config)` and exits with a clear message if the file is missing.

- [ ] **Step 1: Write the failing test for `doctor` on a GPU-less host**

Create `tests/test_cli.py`:

```python
"""Tests for kiln.cli — doctor, status, run, serve --once."""

from __future__ import annotations

from pathlib import Path

import pytest

from kiln import cli


def _write_config(tmp_path: Path, masters: str = "", exports: str = "") -> Path:
    for sub in ("inbox", "scratch", "state"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    body = f"""
inbox = "{tmp_path}/inbox"
scratch_dir = "{tmp_path}/scratch"
state_dir = "{tmp_path}/state"
masters_archive = "{masters}"
exports_archive = "{exports}"
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(body)
    return cfg


def test_doctor_runs_and_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _write_config(tmp_path)
    rc = cli.main(["--config", str(cfg), "doctor"])
    out = capsys.readouterr().out
    assert rc == 0  # inbox/scratch/state all writable; archives unset is fine
    assert "GPU" in out or "gpu" in out
    assert "masters_archive" in out
    assert "exports_archive" in out
    # Unset archives are reported as such, not as errors.
    assert "unset" in out.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL with `NotImplementedError: kiln doctor is implemented in Phase 2 (engine core)`.

- [ ] **Step 3: Implement the CLI**

Rewrite `kiln/cli.py`. Keep the module docstring; extend `_build_parser` with `serve`; implement `main`. Full file:

```python
"""kiln command-line interface.

Subcommands:
  * ``kiln run <folder>``  — manually enqueue a job folder (master + job.json).
  * ``kiln status``        — show the processing queue, pending-archive queue, recent jobs.
  * ``kiln serve``         — run the service loop (``--once`` for a single pass).
  * ``kiln doctor``        — print detected GPU/VRAM/NVENC generation + AV1 support, and
                             verify that ``$INBOX`` is writable and both ``$MASTERS_ARCHIVE``
                             and ``$EXPORTS_ARCHIVE`` are reachable/writable. The one-command
                             "is my setup correct?".
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from kiln import config as config_mod
from kiln.paths import PENDING, StateLayout
from kiln.queue import ProcessingQueue


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kiln", description="GPU video post-processing pipeline")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="enqueue a job folder")
    run.add_argument("folder", help="path to a job folder (master + job.json)")

    sub.add_parser("status", help="show queues and recent jobs")
    sub.add_parser("doctor", help="print hardware detection and storage checks")

    serve = sub.add_parser("serve", help="run the service loop")
    serve.add_argument("--once", action="store_true", help="single pass then exit")

    return parser


def _load_config(args: argparse.Namespace):
    try:
        return config_mod.load(args.config)
    except FileNotFoundError:
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        raise SystemExit(2)
    except ValueError as exc:
        print(f"error: invalid config: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _reachable(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        path.mkdir(parents=True, exist_ok=True)
        return os.access(path, os.W_OK)
    except OSError:
        return False


def _cmd_doctor(cfg) -> int:
    from kiln.hwprobe import probe

    hw = probe()
    print("kiln doctor")
    print("-----------")
    print(f"GPU:                {hw.gpu_name or '(none detected)'}")
    print(f"VRAM (MiB):         {hw.vram_mb if hw.vram_mb is not None else '(unknown)'}")
    print(f"Compute capability: {hw.compute_capability or '(unknown)'}")
    print(f"NVENC H.264/HEVC:   {hw.nvenc_h264}/{hw.nvenc_hevc}")
    print(f"NVENC AV1:          {hw.nvenc_av1}  (Ada/RTX-40+ only)")
    print(f"CUDA available:     {hw.cuda_available}")
    print(f"Whisper tier:       {hw.whisper_tier()}")
    print()

    ok = True
    checks = [("inbox", cfg.inbox), ("scratch_dir", cfg.scratch_dir), ("state_dir", cfg.state_dir)]
    devs = {}
    for name, path in checks:
        writable = _reachable(path)
        ok = ok and writable
        try:
            devs[name] = path.stat().st_dev
        except OSError:
            devs[name] = None
        print(f"{name:12} {path}  ->  {'OK' if writable else 'NOT WRITABLE'}")

    # Same-filesystem requirement (queue uses atomic rename across these three).
    same_fs = len({d for d in devs.values() if d is not None}) <= 1
    if not same_fs:
        ok = False
        print("ERROR: inbox, scratch_dir, and state_dir must be on the SAME filesystem "
              "(atomic rename). They are currently on different devices.")

    for name, path in (("masters_archive", cfg.masters_archive), ("exports_archive", cfg.exports_archive)):
        if path is None:
            print(f"{name:16} (unset — jobs will hold in the pending-archive queue)")
        elif _reachable(path):
            print(f"{name:16} {path}  ->  reachable")
        else:
            print(f"{name:16} {path}  ->  UNREACHABLE (jobs will defer)")

    print()
    print("doctor: OK" if ok else "doctor: PROBLEMS FOUND")
    return 0 if ok else 1


def _cmd_status(cfg) -> int:
    layout = StateLayout(cfg.state_dir)
    layout.ensure()
    buckets = [("queued", layout.queued), ("processing", layout.processing),
               (PENDING, layout.pending), ("done", layout.done), ("failed", layout.failed)]
    for name, path in buckets:
        entries = sorted((p.name for p in path.iterdir() if p.is_dir()), reverse=True)
        recent = ", ".join(entries[:5]) if entries else "-"
        print(f"{name:16} {len(entries):4}  {recent}")
    return 0


def _cmd_run(cfg, folder: str) -> int:
    src = Path(folder)
    if not src.is_dir():
        print(f"error: not a folder: {folder}", file=sys.stderr)
        return 2
    queue = ProcessingQueue(cfg.state_dir)
    job = queue.enqueue(src)
    print(f"enqueued: {job.job_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point (see ``[project.scripts]`` in pyproject.toml)."""
    args = _build_parser().parse_args(argv)
    cfg = _load_config(args)

    if args.command == "doctor":
        return _cmd_doctor(cfg)
    if args.command == "status":
        return _cmd_status(cfg)
    if args.command == "run":
        return _cmd_run(cfg, args.folder)
    if args.command == "serve":
        from kiln import service

        service.run(cfg, once=args.once)
        return 0
    raise AssertionError(f"unhandled command: {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 4: Run the doctor test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Write failing tests for status, run, and serve --once**

Append to `tests/test_cli.py`:

```python
def test_run_enqueues_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _write_config(tmp_path)
    job = tmp_path / "drop" / "myvid"
    job.mkdir(parents=True)
    (job / "job.json").write_text("{}")
    (job / "master.mov").write_bytes(b"x")
    rc = cli.main(["--config", str(cfg), "run", str(job)])
    assert rc == 0
    assert "myvid" in capsys.readouterr().out
    assert (tmp_path / "state" / "queued" / "myvid").is_dir()


def test_status_reports_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _write_config(tmp_path)
    # Seed one queued job.
    q = tmp_path / "state" / "queued" / "seed"
    q.mkdir(parents=True)
    rc = cli.main(["--config", str(cfg), "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "queued" in out
    assert "seed" in out


def test_serve_once_processes_a_drop(tmp_path: Path) -> None:
    import json
    import os
    import time

    cfg = _write_config(tmp_path, masters=str(tmp_path / "m"), exports=str(tmp_path / "e"))
    folder = tmp_path / "inbox" / "oneshot"
    folder.mkdir(parents=True)
    folder_job = {"job_id": "oneshot", "jobs": {"transcode": True, "captions": True}}
    (folder / "job.json").write_text(json.dumps(folder_job))
    (folder / "master.mov").write_bytes(b"MASTER")
    old = time.time() - 10
    for p in folder.iterdir():
        os.utime(p, (old, old))

    rc = cli.main(["--config", str(cfg), "serve", "--once"])
    assert rc == 0
    assert (tmp_path / "m" / "oneshot" / "master.mov").exists()
    assert (tmp_path / "e" / "oneshot" / "upload.mp4").exists()
```

- [ ] **Step 6: Run the full CLI suite**

Run: `python -m pytest tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add kiln/cli.py tests/test_cli.py
git commit -m "feat(cli): implement doctor, status, run, and serve commands"
```

---

## Task 10: Config example + README + docstring sync; full-suite gate

**Files:**
- Modify: `config.example.toml` (document the same-filesystem requirement)
- Modify: `README.md` (Phase 2 usage: doctor/serve/run)
- Modify: `kiln/queue.py` docstring (note the same-filesystem requirement) — already added in Task 3; verify.
- Test: run the entire suite.

**Interfaces:** none (documentation + final gate).

- [ ] **Step 1: Document the same-filesystem requirement in `config.example.toml`**

Add, immediately after the `state_dir` line (around line 45):

```toml
# IMPORTANT: inbox, scratch_dir, and state_dir MUST be on the same filesystem.
# kiln moves job folders (including the multi-GB master) between these by atomic
# rename, which only works within one filesystem. `kiln doctor` verifies this.
# (The two archive destinations MAY live on other filesystems / network mounts.)
```

- [ ] **Step 2: Add a Phase 2 usage section to `README.md`**

Find the section describing usage (or add one near the top-level usage/quickstart). Add:

````markdown
## Running the engine (Phase 2)

```bash
# 1. Copy and edit the config for your machine.
cp config.example.toml config.toml
$EDITOR config.toml   # set inbox, scratch_dir, state_dir (same filesystem);
                      # leave the archives "" to hold jobs locally for now.

# 2. Verify hardware + storage.
kiln --config config.toml doctor

# 3. Run the service (Ctrl-C to stop).
kiln --config config.toml serve
#    ...or process a single pass and exit:
kiln --config config.toml serve --once

# Inspect the queues at any time:
kiln --config config.toml status

# Enqueue a folder by hand (master + job.json):
kiln --config config.toml run /path/to/job-folder
```

With the archives unset, completed jobs collect in `state_dir/pending-archive/`.
Point `masters_archive` / `exports_archive` at real mounts and they drain
automatically — no reprocessing.
````

- [ ] **Step 3: Verify the queue docstring already notes the same-filesystem requirement**

Confirm `kiln/queue.py`'s `ProcessingQueue` docstring (written in Task 3) contains the sentence about same-filesystem rename. If missing, add it. No code change expected.

- [ ] **Step 4: Run the ENTIRE test suite (the Phase 2 gate)**

Run: `python -m pytest -v`
Expected: every test in `tests/` PASSES — smoke, config, queue, runner, hwprobe, archiver, watcher, integration, cli.

- [ ] **Step 5: Lint + type-check (must be clean)**

Run: `ruff check kiln tests && python -m mypy kiln`
Expected: `ruff` reports no errors; `mypy` reports no errors. Fix any issues inline (common ones: unused imports, missing `-> None` on test helpers `mypy` flags — add annotations rather than suppress).

- [ ] **Step 6: Public-release hygiene grep (must find nothing)**

Run:
```bash
grep -rniE 'jschwefel|/home/|/opt/kiln|coldbore|superpowers' kiln tests config.example.toml README.md || echo "CLEAN"
```
Expected: `CLEAN`. (No user paths, no CBB references, no `/opt/kiln` hardcoded in source, no `superpowers` path. `/opt/kiln` may appear only in `packaging/` and top-level docs, which are outside this grep set — do not add it to source.)

- [ ] **Step 7: Commit**

```bash
git add config.example.toml README.md kiln/queue.py
git commit -m "docs: document same-filesystem requirement and Phase 2 engine usage"
```

---

## Verification (Phase 2 acceptance)

Run these after all tasks land — they map to the plan's design guarantees:

- **Unit isolation:** `python -m pytest tests/ -v` — all green, no network, no GPU, no ffmpeg required (hwprobe degrades to Nones on a GPU-less host).
- **Crash recovery:** covered by `test_recover_requeues_orphan` — a job left in `processing/` is re-queued on the next `run(...)`.
- **Strict-sequential:** covered by `test_strict_sequential_blocks_second_dequeue` — a second `dequeue` while one job is processing raises.
- **Two-destination split:** `test_archive_both_destinations` — master and export package land in different roots, neither leaks into the other.
- **Archive-when-reachable:** `test_end_to_end_defer_then_drain` — with archives unset a real (no-op) job processes fully, outputs stay local in `pending-archive/`, then drain moves them once destinations are set. This is the headline Phase 2 behavior.
- **Fail-isolation:** `test_failed_step_is_isolated` — a raising step is recorded; independent steps still run.
- **Hardware honesty on deb005 (manual):** on deb005, `kiln --config config.toml doctor` reports the RTX A4000, ~16376 MiB VRAM, compute capability 8.6, NVENC H.264+HEVC True, **AV1 False**, Whisper tier `large-v3`. This proves the generation/VRAM logic against real hardware, not hardcoded values.
- **Doctor storage checks (manual):** point `scratch_dir` at a different filesystem than `state_dir` and confirm `doctor` exits non-zero with the same-filesystem error; fix and confirm exit 0.

## What Phase 2 deliberately does NOT do (Phase 3+)

- No real ffmpeg/NVENC transcode, no loudnorm, no Whisper, no Ollama, no Real-ESRGAN — the `STEPS` registry points at no-ops. Phase 3 repoints each entry to a real module implementing the same `run(ctx) -> StepResult` seam and TDD's it against a committed <5 MB sample clip.
- No systemd unit wiring / `install.sh` execution / `$INBOX` SMB export — that is Phase 4.
- No Mac-side `kiln-submit` / FCP Share Destination — Phase 5 (spec only).
- No master retention/pruning sweep — that is the separate Task #14 (it consumes `prune_masters`/`retention_days`, which Phase 2 already parses, and verifies the exports pool before deleting a master).

---

## Self-Review Notes (completed by plan author)

- **Spec coverage:** every Phase 2 bullet from the design spec (`docs/specs/2026-07-01-kiln-design.md`) maps to a task — config (T1), queue (T3), runner (T5b), watcher (T7), archiver + pending-archive (T6), CLI incl. doctor (T9), plus the two-destination archive established this session (T6). Hardware probe (T5a) is pulled into Phase 2 because `doctor` needs it. `service.py` (T8) is new relative to the design's module list but is the natural home for the producer/consumer loop the design describes in prose; noted here as an intentional, justified addition.
- **Two-destination correctness:** the archiver splits one assembled folder by filename (master by extension; export package by the `EXPORT_ARTIFACTS` allowlist), so masters and exports provably never cross. The defer path holds the whole remaining folder in `pending-archive/`; drain re-runs the identical split. Partial (one destination set) is tested.
- **Same-filesystem invariant:** stated as a Global Constraint, enforced in `doctor`, documented in `config.example.toml` and the queue docstring. This is the one operational footgun of the directory-of-folders design and is surfaced everywhere a user would hit it.
- **Type consistency:** `Job(job_id, folder)`, `StepResult(name, ok, seconds, message)`, `StepContext(job_id, master, workdir, config, hardware)`, `HardwareProfile(...)`, and `Config(...)` field names are used identically across every task and match the existing scaffold exactly.
- **No placeholders:** every code step contains the actual code; every test step contains the actual test; every run step names the exact command and expected result.
