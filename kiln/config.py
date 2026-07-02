"""Configuration loading for kiln.

A single ``config.toml`` (see ``config.example.toml``) defines the storage paths
(``inbox``, ``scratch_dir``, and the two archive destinations ``masters_archive``
and ``exports_archive``), model choices, and per-job defaults. Environment
variables of the form ``KILN_<KEY>`` override file values.

Storage paths are first-class and configurable so that:
  * kiln runs before a storage server exists (an empty archive path holds those
    jobs in the local pending-archive queue), and
  * changing a final destination is a one-line edit, verified with ``kiln doctor``.

Masters and exports archive to *separate* destinations so the large,
irreplaceable ProRes masters and the small, re-derivable export packages can sit
on different storage tiers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for <3.11
    import tomli as tomllib  # type: ignore[no-redef,import-not-found]


@dataclass
class Config:
    """Resolved kiln configuration."""

    inbox: Path
    scratch_dir: Path
    state_dir: Path
    # None means "not configured; hold those jobs in the local pending-archive queue".
    masters_archive: Path | None  # ProRes masters (large, irreplaceable; e.g. RAIDZ2)
    exports_archive: Path | None  # export packages (re-derivable, kept forever; e.g. RAIDZ1)
    whisper_model: str = "auto"
    llm_model: str = "llama3.1:8b"
    codec: str = "auto"
    target_lufs: int = -14
    jobs: dict[str, bool] = field(default_factory=dict)
    submit_host: str = "127.0.0.1"
    submit_port: int = 8765
    prune_masters: bool = False
    retention_days: int = 90


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
        value = raw.get(key, default)
        return str(value) if value is not None else None

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

    # prune_masters may be a real TOML bool or a string via env override.
    raw_prune = os.environ.get("KILN_PRUNE_MASTERS")
    if raw_prune is not None:
        prune_masters = raw_prune.strip().lower() in {"true", "1", "yes"}
    else:
        prune_masters = bool(raw.get("prune_masters", False))

    cfg = Config(
        inbox=Path(inbox).expanduser(),  # type: ignore[arg-type]
        scratch_dir=Path(scratch).expanduser(),  # type: ignore[arg-type]
        state_dir=Path(state).expanduser(),  # type: ignore[arg-type]
        masters_archive=masters,
        exports_archive=exports,
        whisper_model=get("whisper_model", "auto") or "auto",
        llm_model=get("llm_model", "llama3.1:8b") or "llama3.1:8b",
        codec=get("codec", "auto") or "auto",
        target_lufs=int(get("target_lufs", "-14") or -14),
        jobs=jobs,
        submit_host=submit_host,
        submit_port=submit_port,
        prune_masters=prune_masters,
        retention_days=int(get("retention_days", "90") or 90),
    )
    return cfg
