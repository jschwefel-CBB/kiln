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
    # inbox/scratch/state base: /var/content/kiln on deb005 (the 980 PRO), overridable via
    # STATE_BASE. All three share one filesystem (watcher os.renames inbox -> state/queued).
    for token in ("/opt/kiln", "/var/content/kiln", "useradd", ".venv", "kiln.service"):
        assert token in text, f"install.sh missing reference to {token}"


def test_install_makes_inbox_group_writable_setgid() -> None:
    # A Samba drop user (in the service group) must be able to write the inbox, and drops
    # must inherit the service group so the service can read them → setgid + group-write.
    text = _INSTALL.read_text()
    assert "chmod 2775" in text and "inbox" in text, "install.sh must setgid+group-write the inbox"


def test_install_gpu_whisper_flag_installs_cuda_libs() -> None:
    text = _INSTALL.read_text()
    assert "--gpu-whisper" in text, "install.sh must support --gpu-whisper"
    # Installs the CUDA 12 backends CTranslate2 needs...
    assert "nvidia-cublas-cu12" in text and "nvidia-cudnn-cu12" in text
    # ...and exposes them to the service via an LD_LIBRARY_PATH drop-in.
    assert "LD_LIBRARY_PATH" in text and "10-cuda-libs.conf" in text


def test_install_installs_and_enables_prune_timer() -> None:
    text = _INSTALL.read_text()
    assert "kiln-prune.service" in text and "kiln-prune.timer" in text
    assert "systemctl enable kiln-prune.timer" in text


_PRUNE_SVC = _ROOT / "packaging" / "kiln-prune.service"
_PRUNE_TIMER = _ROOT / "packaging" / "kiln-prune.timer"


def test_prune_service_runs_prune_and_needs_no_gpu() -> None:
    text = _PRUNE_SVC.read_text()
    assert "Type=oneshot" in text
    exec_lines = [ln for ln in text.splitlines() if ln.strip().startswith("ExecStart")]
    assert exec_lines and any(ln.rstrip().endswith("prune") for ln in exec_lines), exec_lines
    # Destructive job: writes only the masters archive, reads exports, no GPU devices.
    assert "ReadWritePaths=/mnt/masters" in text
    assert "ReadOnlyPaths=/mnt/exports" in text
    assert "PrivateDevices=true" in text
    assert "/dev/nvidia" not in text  # no GPU access for a delete sweep


def test_prune_timer_is_daily_and_persistent() -> None:
    text = _PRUNE_TIMER.read_text()
    assert "OnCalendar=daily" in text
    assert "Persistent=true" in text
    assert "WantedBy=timers.target" in text
