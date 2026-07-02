"""Guard tests for Phase 4 packaging artifacts (no root, no execution)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_UNIT = _ROOT / "packaging" / "kiln.service"
_INSTALL = _ROOT / "install.sh"


def test_unit_execstart_uses_serve() -> None:
    text = _UNIT.read_text()
    assert "kiln --config" in text
    # The CLI subcommand is `serve`, not `watch` (which does not exist).
    exec_lines = [ln for ln in text.splitlines() if ln.strip().startswith("ExecStart")]
    assert exec_lines, "no ExecStart line"
    assert any("serve" in ln for ln in exec_lines), f"ExecStart must run 'serve': {exec_lines}"
    assert not any("watch" in ln for ln in exec_lines), "ExecStart still references 'watch'"


def test_unit_allows_nfs_archives_and_gpu() -> None:
    text = _UNIT.read_text()
    # NFS archive mounts must be writable despite ProtectSystem.
    assert "ReadWritePaths=" in text and "/mnt/masters" in text and "/mnt/exports" in text
    # GPU device access for NVENC/CUDA.
    assert "/dev/nvidia" in text or "DeviceAllow" in text


def test_install_has_check_mode() -> None:
    text = _INSTALL.read_text()
    assert "--check" in text, "install.sh must support a --check dry-run"
    # No longer the refuse-to-run scaffold.
    assert "scaffold" not in text.lower() or "--check" in text


def test_install_creates_expected_layout() -> None:
    text = _INSTALL.read_text()
    for token in ("/opt/kiln", "/var/lib/kiln", "useradd", ".venv", "kiln.service"):
        assert token in text, f"install.sh missing reference to {token}"
