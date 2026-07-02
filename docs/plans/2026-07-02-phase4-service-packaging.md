# Kiln Phase 4 — Service Packaging & Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to work through this plan. Unlike Phases 2–3, several tasks are **live root-level actions on deb005** that must be run with explicit per-step user approval (they mutate the machine and are not fully reversible). Author-and-commit tasks use normal TDD-ish verification; execution tasks are marked **[LIVE deb005]** and pause for approval.

**Goal:** Package kiln as a production systemd service on deb005 — a real `install.sh` (service user, `/opt/kiln`, pinned venv, unit install), a corrected `kiln.service`, a working `config.toml` pointing at the already-mounted ark archives, a Samba export of `$INBOX` for the Mac (real user, no guest), and the CUDA 12 cuBLAS/cuDNN runtime so faster-whisper uses the A4000 instead of CPU.

**Architecture:** kiln installs to `/opt/kiln` (code + venv), runs as the unprivileged `kiln` user via `kiln.service`, watches `/var/lib/kiln/inbox` (local NVMe, same filesystem as scratch/state), and archives to `/mnt/masters` + `/mnt/exports` (NFS from ark, already mounted). The Mac drops masters into `$INBOX` over a Samba share deb005 exports. GPU: NVENC already works; this phase adds the CUDA 12 cuBLAS/cuDNN libs that CTranslate2 (faster-whisper) needs for GPU transcription.

**Tech Stack:** bash (`install.sh`), systemd unit, Samba (`smbd`), Python venv (`pip install .[ai]`), NVIDIA CUDA 12 runtime libs (cuBLAS + cuDNN). deb005 is Debian; `/` is `/dev/nvme0n1p2` (~97 GB free).

## Global Constraints

- **Live deb005 actions require per-step approval.** Every `sudo`, `apt install`, `useradd`, `systemctl`, or file write under `/opt` or `/etc` is run only after showing the exact command and getting a yes. No batching irreversible actions.
- **Same-filesystem invariant.** `inbox`, `scratch_dir`, `state_dir` all under `/var/lib/kiln` on `/` (nvme0n1p2). `kiln doctor` verifies via `st_dev`. Archives (`/mnt/masters`, `/mnt/exports`) are NFS and MAY differ — the archiver uses `shutil.move` (cross-fs safe).
- **No guest SMB.** The `$INBOX` Samba export uses a real user with a password, guest off — same rule proven on ark. Its own credential, not reused.
- **Storage cutover is already done — do not redo it.** ark (172.31.1.20) NFS shares are mounted at `/mnt/masters` + `/mnt/exports`, writable, persistent in fstab (`_netdev,nofail`). Verified write+read 2026-07-02. This phase only *documents* and *points config at* them.
- **The service must actually start.** The scaffold's `ExecStart ... watch` is wrong — the CLI command is `serve`. Fix to `serve`. Hardening must not block the NFS archive mounts or GPU devices.
- **Idempotent install.** `install.sh` must be safe to re-run: create-if-missing user, refresh venv, reinstall unit. A `--check` (dry-run) mode validates prerequisites without mutating.
- **Conventional commits, personal repo, GPG-signed.**

---

## File Structure

| File | Responsibility | Phase 4 action |
|---|---|---|
| `packaging/kiln.service` | systemd unit — corrected command + hardening that allows the NFS mounts and GPU. | Rewrite. |
| `install.sh` | Real installer: service user, `/opt/kiln`, venv, unit install, `kiln doctor`; `--check` dry-run. | Rewrite. |
| `packaging/smb-kiln-inbox.conf` | **(new)** documented Samba share stanza for `$INBOX` (copied into `/etc/samba/smb.conf` by hand or a snippet include). | Create. |
| `packaging/config.deb005.toml` | **(new, committed sample; the real one is NOT committed)** the deb005 config with local `/var/lib/kiln/*` paths + ark archive mounts, as a reference. The live `/opt/kiln/config.toml` is generated from it. | Create. |
| `docs/deb005-deployment.md` | **(new)** the deb005 setup runbook: what install.sh does, the Samba setup, the CUDA-12 cuBLAS/cuDNN install, verification. | Create. |
| `tests/test_install_check.py` | **(new)** lints `install.sh` structure / asserts `--check` mode exists and the unit's ExecStart uses `serve`. Lightweight guard, no root. | Create. |

---

## Task 1: Fix the systemd unit (`packaging/kiln.service`)

**Files:**
- Rewrite: `packaging/kiln.service`
- Test: `tests/test_install_check.py` (create — asserts the unit is correct)

**Interfaces:** none (a unit file + a guard test).

**Why:** the scaffold calls `kiln ... watch` (no such command — it's `serve`), and `ProtectHome=true`/`ProtectSystem=full` would block `/mnt/*` NFS archives and `/dev/nvidia*`. The service must start, reach the mounts, and reach the GPU.

- [ ] **Step 1: Write the failing guard test**

Create `tests/test_install_check.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_install_check.py::test_unit_execstart_uses_serve -q`
Expected: FAIL (current unit says `watch`).

- [ ] **Step 3: Rewrite `packaging/kiln.service`**

```ini
[Unit]
Description=kiln — GPU video post-processing pipeline
After=network-online.target remote-fs.target
Wants=network-online.target
# The NFS archive mounts are nice-to-have, not required to start (nofail); the
# pending-archive queue holds jobs if they are absent.

[Service]
Type=simple
User=kiln
Group=kiln
WorkingDirectory=/opt/kiln
# The service loop: watch $INBOX + the localhost submit endpoint, drive the queue,
# archive to the two destinations. `serve` is the correct CLI command.
ExecStart=/opt/kiln/.venv/bin/kiln --config /opt/kiln/config.toml serve
Restart=on-failure
RestartSec=5

# --- Hardening, relaxed exactly where kiln needs it ---
NoNewPrivileges=true
PrivateTmp=true
# ProtectSystem=full makes most of the fs read-only; kiln writes only these:
ProtectSystem=full
ReadWritePaths=/var/lib/kiln /mnt/masters /mnt/exports
# kiln has no business in user homes.
ProtectHome=true
# GPU access for NVENC (ffmpeg) and CUDA (faster-whisper / ollama client).
DeviceAllow=/dev/nvidia0 rw
DeviceAllow=/dev/nvidiactl rw
DeviceAllow=/dev/nvidia-uvm rw
DeviceAllow=/dev/nvidia-uvm-tools rw
DeviceAllow=/dev/nvidia-modeset rw
SupplementaryGroups=video render

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Run the guard tests to verify they pass**

Run: `python -m pytest tests/test_install_check.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packaging/kiln.service tests/test_install_check.py
git commit -m "fix(packaging): correct kiln.service to run 'serve' and allow NFS mounts + GPU"
```

---

## Task 2: Real `install.sh` with `--check` dry-run

**Files:**
- Rewrite: `install.sh`
- Test: extend `tests/test_install_check.py`

**Interfaces:** none (installer script + guard test).

- [ ] **Step 1: Add failing guard tests for install.sh structure**

Append to `tests/test_install_check.py`:

```python
def test_install_has_check_mode() -> None:
    text = _INSTALL.read_text()
    assert "--check" in text, "install.sh must support a --check dry-run"
    # No longer the refuse-to-run scaffold.
    assert "scaffold" not in text.lower() or "--check" in text


def test_install_creates_expected_layout() -> None:
    text = _INSTALL.read_text()
    for token in ("/opt/kiln", "/var/lib/kiln", "useradd", ".venv", "kiln.service"):
        assert token in text, f"install.sh missing reference to {token}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_install_check.py -q`
Expected: the two new tests FAIL (scaffold lacks `--check` and the layout refs).

- [ ] **Step 3: Rewrite `install.sh`**

```bash
#!/usr/bin/env bash
#
# kiln installer — installs kiln as a systemd service on a Linux host with an NVIDIA GPU.
#
#   sudo ./install.sh          # install/upgrade
#   sudo ./install.sh --check  # dry-run: validate prerequisites, mutate nothing
#
# Idempotent: safe to re-run. Creates the service user if missing, (re)builds the venv,
# (re)installs the unit. External-tool prerequisites (ffmpeg+NVENC, ollama+model, and the
# optional realesrgan-ncnn-vulkan binary, plus CUDA cuBLAS/cuDNN for GPU Whisper) are the
# operator's responsibility — see docs/deb005-deployment.md. --check reports what's missing.

set -euo pipefail

INSTALL_DIR="/opt/kiln"
STATE_BASE="/var/lib/kiln"
SERVICE_USER="kiln"
UNIT_SRC="packaging/kiln.service"
UNIT_DST="/etc/systemd/system/kiln.service"
CONFIG_DST="${INSTALL_DIR}/config.toml"
CONFIG_SRC="packaging/config.deb005.toml"

CHECK_ONLY=false
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=true

log()  { echo "[kiln-install] $*"; }
warn() { echo "[kiln-install] WARNING: $*" >&2; }
die()  { echo "[kiln-install] ERROR: $*" >&2; exit 1; }

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then die "must run as root (sudo ./install.sh)"; fi
}

check_prereqs() {
    log "checking prerequisites..."
    command -v python3 >/dev/null || warn "python3 not found"
    command -v ffmpeg  >/dev/null || warn "ffmpeg not found (transcode/normalize need it)"
    command -v nvidia-smi >/dev/null || warn "nvidia-smi not found (no GPU detected)"
    command -v ollama  >/dev/null || warn "ollama not found (metadata step needs it)"
    command -v realesrgan-ncnn-vulkan >/dev/null || warn "realesrgan-ncnn-vulkan not found (upscale is opt-in; ok)"
    [[ -f "${UNIT_SRC}" ]]   || die "run from the repo root; ${UNIT_SRC} not found"
    [[ -f "${CONFIG_SRC}" ]] || die "${CONFIG_SRC} not found"
    log "prerequisite check done."
}

create_user() {
    if id -u "${SERVICE_USER}" >/dev/null 2>&1; then
        log "service user ${SERVICE_USER} exists"
    else
        log "creating service user ${SERVICE_USER}"
        $DRY useradd --system --home-dir "${INSTALL_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
    fi
    # GPU groups for NVENC/CUDA access.
    $DRY usermod -aG video,render "${SERVICE_USER}" 2>/dev/null || true
}

make_dirs() {
    log "creating ${INSTALL_DIR} and ${STATE_BASE}/{inbox,scratch,state}"
    $DRY install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${INSTALL_DIR}"
    for d in inbox scratch state; do
        $DRY install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${STATE_BASE}/${d}"
    done
}

install_code() {
    log "installing package into ${INSTALL_DIR}"
    # Copy repo (excluding venv/git) to /opt/kiln, then build a pinned venv there.
    $DRY rsync -a --delete --exclude '.git' --exclude '.venv' --exclude 'tests/fixtures/*.mp4' ./ "${INSTALL_DIR}/"
    $DRY python3 -m venv "${INSTALL_DIR}/.venv"
    $DRY "${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade pip
    $DRY "${INSTALL_DIR}/.venv/bin/pip" install --quiet "${INSTALL_DIR}[ai]"
    $DRY chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
}

install_config() {
    if [[ -f "${CONFIG_DST}" ]]; then
        log "config exists at ${CONFIG_DST}; leaving it (edit by hand to change)"
    else
        log "installing starter config to ${CONFIG_DST}"
        $DRY install -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0644 "${CONFIG_SRC}" "${CONFIG_DST}"
    fi
}

install_unit() {
    log "installing systemd unit"
    $DRY install -m 0644 "${UNIT_SRC}" "${UNIT_DST}"
    $DRY systemctl daemon-reload
    $DRY systemctl enable kiln.service
    log "unit installed + enabled (not started; start with: systemctl start kiln)"
}

run_doctor() {
    log "running kiln doctor"
    $DRY su -s /bin/bash "${SERVICE_USER}" -c "${INSTALL_DIR}/.venv/bin/kiln --config ${CONFIG_DST} doctor" || \
        warn "kiln doctor reported problems (see output above)"
}

main() {
    if $CHECK_ONLY; then
        DRY="echo [dry-run]"
        log "=== --check (dry-run): no changes will be made ==="
        check_prereqs
        create_user; make_dirs; install_code; install_config; install_unit; run_doctor
        log "=== dry-run complete ==="
        exit 0
    fi
    require_root
    DRY=""
    check_prereqs
    create_user
    make_dirs
    install_code
    install_config
    install_unit
    run_doctor
    log "done. Start the service with:  sudo systemctl start kiln"
}

main "$@"
```

- [ ] **Step 4: Run the guard tests + shellcheck**

Run:
```bash
python -m pytest tests/test_install_check.py -q
command -v shellcheck >/dev/null && shellcheck install.sh || echo "(shellcheck not installed; skipping)"
```
Expected: pytest PASS; shellcheck clean (or skipped).

- [ ] **Step 5: Commit**

```bash
git add install.sh tests/test_install_check.py
git commit -m "feat(packaging): implement idempotent install.sh with --check dry-run"
```

---

## Task 3: Committed deb005 config + Samba stanza + deployment doc

**Files:**
- Create: `packaging/config.deb005.toml`, `packaging/smb-kiln-inbox.conf`, `docs/deb005-deployment.md`
- Test: none (docs/config; the guard test already references config.deb005.toml existence via install.sh check).

- [ ] **Step 1: Create `packaging/config.deb005.toml`**

```toml
# kiln config for deb005 (the RTX A4000 box). The live copy lives at
# /opt/kiln/config.toml (installed from this file). Local paths are on the NVMe
# root fs (/dev/nvme0n1p2) so inbox/scratch/state share one filesystem; archives
# are the ark NFS mounts.

inbox       = "/var/lib/kiln/inbox"
scratch_dir = "/var/lib/kiln/scratch"
state_dir   = "/var/lib/kiln/state"

# ark (172.31.1.20) NFS mounts — already mounted via /etc/fstab (_netdev,nofail).
masters_archive = "/mnt/masters"
exports_archive = "/mnt/exports"

# Master pruning stays OFF until explicitly enabled.
prune_masters = false
retention_days = 90

whisper_model = "auto"       # large-v3 on the A4000's VRAM
llm_model = "llama3.1:8b"    # pulled on deb005
codec = "auto"               # HEVC 4K / H.264 <=1080p; A4000 = no AV1
target_lufs = -14

[jobs]
transcode = true
captions  = true
normalize = true
chapters  = true
metadata  = true
upscale   = false            # opt-in; needs realesrgan-ncnn-vulkan

[submit]
host = "127.0.0.1"
port = 8765
```

- [ ] **Step 2: Create `packaging/smb-kiln-inbox.conf`**

```ini
; kiln $INBOX Samba share for deb005 — the Mac mounts this and FCP exports masters into it.
; Append this stanza to /etc/samba/smb.conf (or drop into an include), then:
;   sudo smbpasswd -a <user>      # create the SMB password for an existing system user
;   sudo systemctl restart smbd
; Connect from the Mac:  smb://deb005/kiln-inbox   (Registered User, NOT guest)
;
; Guest access is intentionally OFF (same policy as ark). Use a real user with its own
; password, and ensure that user can write /var/lib/kiln/inbox.

[kiln-inbox]
   path = /var/lib/kiln/inbox
   comment = kiln master drop inbox
   browseable = yes
   read only = no
   guest ok = no
   valid users = @kiln kiln
   create mask = 0664
   directory mask = 0775
   ; force group so kiln (the service user) can always read what the Mac writes
   force group = kiln
```

- [ ] **Step 3: Create `docs/deb005-deployment.md`**

Write a zero-prior-experience runbook covering: (a) what `install.sh` does and how to run it (`sudo ./install.sh`, and `--check` first); (b) the Samba `$INBOX` export (install samba, append the stanza, `smbpasswd`, restart, connect from Mac); (c) the CUDA 12 cuBLAS/cuDNN install for GPU Whisper (Task 5 — reference it); (d) verification (`kiln doctor`, drop a clip, confirm it lands on `/mnt/masters` + `/mnt/exports`); (e) the storage cutover is already done (ark mounts). Cross-reference `storage-server.md`/`storage-server-runbook.md` for ark, and note masters=deb005-only, exports also SMB-to-Mac from ark.

Full content is written in this step (not summarized) when executing — include exact commands for Debian, the smb.conf edit, and the doctor/round-trip verification.

- [ ] **Step 4: Commit**

```bash
git add packaging/config.deb005.toml packaging/smb-kiln-inbox.conf docs/deb005-deployment.md
git commit -m "docs(packaging): add deb005 config, Samba inbox stanza, and deployment runbook"
```

---

## Task 4: [LIVE deb005] Run the install and start the service

**This task mutates deb005. Each step is run only after showing the command and getting approval.**

- [ ] **Step 1: Dry-run first (no mutation)**

Run: `cd /home/jschwefel/repositories/kiln && sudo ./install.sh --check`
Expected: reports prerequisites (ffmpeg ✓, nvidia-smi ✓, ollama ✓, realesrgan ✗-ok) and echoes the mutations it *would* make. Review before proceeding.

- [ ] **Step 2: [APPROVAL] Real install**

Run: `sudo ./install.sh`
Expected: creates `kiln` user, `/opt/kiln` + venv (installs `.[ai]`), `/var/lib/kiln/{inbox,scratch,state}`, the unit (enabled), and runs `kiln doctor`. The doctor output should show the A4000 and both archives reachable at `/mnt/masters` + `/mnt/exports`.

- [ ] **Step 3: [APPROVAL] Start the service**

Run: `sudo systemctl start kiln && sleep 2 && systemctl status kiln --no-pager`
Expected: `active (running)`. If it failed, `journalctl -u kiln -n 40 --no-pager` to diagnose (likely config path or a mount permission — fix and retry).

- [ ] **Step 4: End-to-end through the real service**

Copy the sample clip into the live inbox as a job and confirm it flows to ark:
```bash
sudo install -d -o kiln -g kiln /var/lib/kiln/inbox/smoketest
sudo cp tests/fixtures/sample.mp4 /var/lib/kiln/inbox/smoketest/master.mp4
echo '{"job_id":"smoketest","jobs":{"transcode":true,"normalize":true}}' | sudo tee /var/lib/kiln/inbox/smoketest/job.json
sudo chown -R kiln:kiln /var/lib/kiln/inbox/smoketest
# wait for the watcher to pick it up, then:
sleep 20 && ls -la /mnt/masters/smoketest/ /mnt/exports/smoketest/
```
Expected: `master.mp4` in `/mnt/masters/smoketest/`, `upload.mp4` in `/mnt/exports/smoketest/`. This proves the real service archives to ark. Clean up the test dirs afterward.

- [ ] **Step 5: (no commit — this is a live-verification task; record the result in the deployment doc if anything deviated)**

---

## Task 5: [LIVE deb005] GPU Whisper — install CUDA 12 cuBLAS + cuDNN

**faster-whisper (CTranslate2) needs `libcublas.so.12` + cuDNN 8/9 for CUDA 12. deb005 currently has only Ollama's private cuBLAS 13 — wrong major version, not on the path. This task installs the correct runtime.**

> The exact package/version is verified against CTranslate2's current CUDA requirement at execution time (CUDA 12.x cuBLAS + matching cuDNN). Two viable routes; pick at execution:
> - **pip route (simplest, venv-local):** `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` into `/opt/kiln/.venv`, and set `LD_LIBRARY_PATH` (or rely on CTranslate2 finding the nvidia-* wheels) in the unit. No system-wide change.
> - **system route:** add NVIDIA's CUDA apt repo and install the cuBLAS/cuDNN runtime system-wide.

- [ ] **Step 1: Confirm the current failure (baseline)**

Run (as the kiln user or in the venv): a real transcribe on the sample with `whisper_model=base`, hardware CUDA — confirm it still fails with `libcublas.so.12 not found` (or already works if libs got pulled by `.[ai]`).

- [ ] **Step 2: [APPROVAL] Install the CUDA 12 libs (pip route preferred)**

Run: `sudo /opt/kiln/.venv/bin/pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`
Expected: the CUDA 12 cuBLAS + cuDNN wheels install into the venv.

- [ ] **Step 3: [APPROVAL] Make the libs discoverable to the service**

Add an `Environment=LD_LIBRARY_PATH=...` line to `kiln.service` pointing at the venv's `nvidia/*/lib` dirs (or a drop-in), `daemon-reload`, restart. (Exact paths determined from where the wheels landed.)

- [ ] **Step 4: Verify GPU transcription**

Run a real transcribe on the sample with `whisper_model=auto` and CUDA hardware; confirm it now runs on `cuda` (not the CPU fallback) — check the `StepResult.message` says `... on cuda` and `nvidia-smi` shows the process during the run.

- [ ] **Step 5: Commit any unit change**

```bash
git add packaging/kiln.service docs/deb005-deployment.md
git commit -m "feat(packaging): enable GPU Whisper via CUDA 12 cuBLAS/cuDNN on deb005"
```

---

## Task 6: [LIVE deb005] Samba $INBOX export for the Mac

**Mutates deb005 (installs samba, edits /etc/samba/smb.conf). Per-step approval.**

- [ ] **Step 1: [APPROVAL] Install Samba**

Run: `sudo apt update && sudo apt install -y samba`
Expected: `smbd` installed and the service present.

- [ ] **Step 2: [APPROVAL] Add the share + SMB user**

Append `packaging/smb-kiln-inbox.conf` to `/etc/samba/smb.conf`, create an SMB password for a real user (`sudo smbpasswd -a <user>` — the user must exist as a system user and be in/related to the `kiln` group so writes land group-owned), then `sudo systemctl restart smbd`. Guest stays off.

- [ ] **Step 3: Verify the share is offered**

Run: `smbclient -L localhost -U <user>` (enter the SMB password) → confirm `kiln-inbox` is listed. Optionally `testparm` to validate smb.conf.

- [ ] **Step 4: [Mac-side, user does this] Connect + drop a test master**

From the Mac: `smb://deb005/kiln-inbox`, Registered User (not guest), copy a small clip in as `<job>/master.mov` + a `job.json` → confirm kiln (the running service) picks it up and archives it to ark. (This closes the Mac→deb005→ark loop; the FCP integration itself is Phase 5.)

- [ ] **Step 5: Update the deployment doc with the confirmed procedure and commit**

```bash
git add docs/deb005-deployment.md
git commit -m "docs(packaging): document confirmed Samba inbox export for the Mac"
```

---

## Verification (Phase 4 acceptance)

- **Service runs:** `systemctl status kiln` → `active (running)`; `journalctl -u kiln` shows the watch/serve loop.
- **doctor is green on deb005:** the A4000 detected, `/var/lib/kiln/*` writable + same filesystem, `/mnt/masters` + `/mnt/exports` reachable.
- **Real archive to ark:** a clip dropped in `$INBOX` lands as `master.mp4` on `/mnt/masters/<job>/` and `upload.mp4` on `/mnt/exports/<job>/`. (Storage cutover confirmed working — mounts writable.)
- **GPU Whisper:** a real transcribe runs on `cuda` (not CPU); `nvidia-smi` shows the process; `StepResult.message` reports the cuda device.
- **Mac drop path:** `smb://deb005/kiln-inbox` mounts with a real user (guest rejected), a dropped job is processed and archived.
- **Install is idempotent:** re-running `sudo ./install.sh` does not error and leaves a working service.
- **Repo hygiene:** committed artifacts contain no secrets (SMB passwords are set via `smbpasswd`, never committed); no user-home paths hardcoded in source; the real `/opt/kiln/config.toml` is not committed (only `packaging/config.deb005.toml` sample).

## What Phase 4 leaves for later

- **FCP Share Destinations / `kiln-submit`** — Phase 5 (the Mac controller spec). Phase 4 only stands up the deb005 SMB *server* side of the drop.
- **Master retention/pruning sweep** — Task #14 (separate). `prune_masters` stays false.
- **install.sh for non-Debian** — deb005 is Debian; the script targets it. A later pass can add RHEL/Fedora package-manager branches per the multi-platform doc rule if kiln is deployed elsewhere.

---

## Self-Review Notes (plan author)

- **Live-vs-authored split is explicit:** Tasks 1–3 author + commit + guard-test with no root; Tasks 4–6 are `[LIVE deb005]` with per-step approval gates on every mutation. This honors the confirm-before-irreversible rule.
- **The scaffold bug is caught by a test, not just fixed:** `test_unit_execstart_uses_serve` fails on the current `watch` and would catch a regression. The hardening/GPU/NFS requirements are asserted too.
- **Storage cutover is treated as done, not redone:** the plan documents and points at the already-mounted, already-verified ark shares; no re-mounting. This reflects the real state observed 2026-07-02.
- **CUDA versioning is called out precisely:** the finding that only Ollama's private cuBLAS **13** exists (CTranslate2 needs **12**) is the crux of Task 5; the exact wheel/repo is verified at execution rather than guessed.
- **No secrets committed:** SMB passwords are set live via `smbpasswd`; only the *sample* config and the smb.conf *stanza* (no credentials) are committed. The live `/opt/kiln/config.toml` is generated, not committed.
- **Idempotency + dry-run:** `--check` lets the risky install be previewed with zero mutation before Task 4 Step 2 runs it for real.
