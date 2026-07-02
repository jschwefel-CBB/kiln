"""Configuration loading for kiln.

A single ``config.toml`` (see ``config.example.toml``) defines the three storage
paths (``inbox``, ``scratch_dir``, ``archive``), model choices, and per-job defaults.
Environment variables of the form ``KILN_<KEY>`` override file values.

Storage paths are first-class and configurable so that:
  * kiln runs before a storage server exists (``archive = ""`` holds jobs locally), and
  * changing the final destination is a one-line edit, verified with ``kiln doctor``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for <3.11
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class Config:
    """Resolved kiln configuration."""

    inbox: Path
    scratch_dir: Path
    state_dir: Path
    archive: Path | None  # None means "no archive configured; hold jobs locally"
    whisper_model: str = "auto"
    llm_model: str = "llama3.1:8b"
    codec: str = "auto"
    target_lufs: int = -14
    jobs: dict[str, bool] = field(default_factory=dict)
    submit_host: str = "127.0.0.1"
    submit_port: int = 8765


def load(path: str | Path = "config.toml") -> Config:
    """Load and validate configuration from ``path`` (TOML), applying env overrides.

    Not yet implemented — Phase 2.
    """
    raise NotImplementedError("config.load is implemented in Phase 2 (engine core)")
