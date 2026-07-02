"""Smoke tests for the kiln scaffold.

These assert only that the package structure is coherent and importable — real behavior is
covered by step tests added in Phase 3. They exist so the skeleton stays green in CI from
day one and so a broken import is caught immediately.
"""

from __future__ import annotations

import importlib

import pytest

MODULES = [
    "kiln",
    "kiln.config",
    "kiln.hwprobe",
    "kiln.queue",
    "kiln.watcher",
    "kiln.runner",
    "kiln.archiver",
    "kiln.cli",
    "kiln.steps",
    "kiln.steps.transcode",
    "kiln.steps.normalize",
    "kiln.steps.transcribe",
    "kiln.steps.chapters",
    "kiln.steps.metadata",
    "kiln.steps.upscale",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name: str) -> None:
    """Every kiln module imports cleanly."""
    assert importlib.import_module(module_name) is not None


def test_version_present() -> None:
    import kiln

    assert isinstance(kiln.__version__, str) and kiln.__version__


def test_step_modules_expose_run() -> None:
    """Each pipeline step exposes a module-level ``run`` callable (the Step protocol)."""
    for step in ["transcode", "normalize", "transcribe", "chapters", "metadata", "upscale"]:
        mod = importlib.import_module(f"kiln.steps.{step}")
        assert callable(getattr(mod, "run", None)), f"{step} is missing run()"


def test_cli_parser_builds() -> None:
    """The argument parser constructs and rejects an unknown command."""
    from kiln.cli import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus-command"])
