#!/usr/bin/env bash
#
# kiln installer — installs kiln as a systemd service on a Linux host with an NVIDIA GPU.
#
#   sudo ./install.sh          # install/upgrade
#   sudo ./install.sh --check  # dry-run: validate prerequisites, mutate nothing
#
# Idempotent: safe to re-run. Creates the service user if missing, (re)builds the venv,
# (re)installs the unit.
#
# External-tool prerequisites (the pipeline steps shell out to these):
#   * ffmpeg with NVENC          — transcode/normalize (and upscale reassembly).
#   * faster-whisper             — installed via the `ai` extra; for GPU transcription the
#                                  host also needs NVIDIA cuBLAS + cuDNN (CUDA 12) runtime
#                                  libs, else it falls back to CPU. See docs/deb005-deployment.md.
#   * ollama + a pulled model    — metadata step (default: `ollama pull llama3.1:8b`).
#   * realesrgan-ncnn-vulkan     — OPTIONAL, only for the opt-in upscale step; skipped cleanly
#                                  if absent.
# --check reports what is missing without changing anything.

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
    # Copy repo (excluding venv/git/fixtures) to /opt/kiln, then build a pinned venv there.
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
