#!/usr/bin/env bash
#
# kiln installer — installs kiln as a systemd service on a Linux host with an NVIDIA GPU.
#
#   sudo ./install.sh                 # install/upgrade
#   sudo ./install.sh --check         # dry-run: validate prerequisites, mutate nothing
#   sudo ./install.sh --gpu-whisper   # also install CUDA 12 cuBLAS+cuDNN for GPU transcription
#
# Idempotent: safe to re-run. Creates the service user if missing, (re)builds the venv,
# (re)installs the unit.
#
# --gpu-whisper installs NVIDIA cuBLAS + cuDNN (CUDA 12) wheels into kiln's venv (~2.3 GB)
# and writes a systemd drop-in putting them on LD_LIBRARY_PATH, so faster-whisper runs
# transcription on the GPU instead of falling back to CPU. Omit it to keep the install lean
# and let transcription run on CPU (valid on small cards or when the GPU is reserved for
# transcode). Requires an NVIDIA GPU; combine with --check to preview.
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
PRUNE_SVC_SRC="packaging/kiln-prune.service"
PRUNE_TIMER_SRC="packaging/kiln-prune.timer"
SYSTEMD_DIR="/etc/systemd/system"
CONFIG_DST="${INSTALL_DIR}/config.toml"
CONFIG_SRC="packaging/config.deb005.toml"

CHECK_ONLY=false
GPU_WHISPER=false
for arg in "$@"; do
    case "${arg}" in
        --check)       CHECK_ONLY=true ;;
        --gpu-whisper) GPU_WHISPER=true ;;
        *) echo "[kiln-install] ERROR: unknown argument: ${arg}" >&2; exit 1 ;;
    esac
done

UNIT_DROPIN_DIR="${UNIT_DST}.d"
CUDA_DROPIN="${UNIT_DROPIN_DIR}/10-cuda-libs.conf"

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
    [[ -f "${PRUNE_SVC_SRC}" ]]   || die "${PRUNE_SVC_SRC} not found"
    [[ -f "${PRUNE_TIMER_SRC}" ]] || die "${PRUNE_TIMER_SRC} not found"
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
    # Inbox is group-writable + setgid so a Samba drop user (in the service group) can
    # write masters into it, and everything created there inherits the service group so
    # the service can read it. Harmless when Samba isn't used.
    $DRY chmod 2775 "${STATE_BASE}/inbox"
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
    log "installing systemd units (service + prune timer)"
    $DRY install -m 0644 "${UNIT_SRC}" "${UNIT_DST}"
    $DRY install -m 0644 "${PRUNE_SVC_SRC}" "${SYSTEMD_DIR}/kiln-prune.service"
    $DRY install -m 0644 "${PRUNE_TIMER_SRC}" "${SYSTEMD_DIR}/kiln-prune.timer"
    $DRY systemctl daemon-reload
    $DRY systemctl enable kiln.service
    # The prune TIMER is enabled so the daily sweep is scheduled; the sweep itself deletes
    # nothing unless config.toml sets prune_masters=true, so enabling the timer is safe.
    $DRY systemctl enable kiln-prune.timer
    log "units installed; kiln.service enabled (not started); kiln-prune.timer enabled"
}

install_gpu_whisper() {
    # CTranslate2 (faster-whisper's backend) needs CUDA 12 cuBLAS + cuDNN on the library
    # path to use the GPU. Install the wheels into the venv and expose their lib dirs via a
    # systemd drop-in. Without this, transcription silently falls back to CPU.
    log "installing CUDA 12 cuBLAS + cuDNN into the venv (GPU Whisper; ~2.3 GB)"
    $DRY "${INSTALL_DIR}/.venv/bin/pip" install --quiet nvidia-cublas-cu12 nvidia-cudnn-cu12

    # The wheels land under .venv/lib/pythonX.Y/site-packages/nvidia/{cublas,cudnn}/lib.
    # Compute the actual python X.Y from the venv rather than assuming a version.
    if [[ -n "${DRY}" ]]; then
        log "[dry-run] would write ${CUDA_DROPIN} with LD_LIBRARY_PATH for cublas+cudnn"
        return
    fi
    local pyver
    pyver="$("${INSTALL_DIR}/.venv/bin/python" -c 'import sys;print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"
    local libbase="${INSTALL_DIR}/.venv/lib/${pyver}/site-packages/nvidia"
    local ldpath="${libbase}/cublas/lib:${libbase}/cudnn/lib"
    install -d "${UNIT_DROPIN_DIR}"
    cat > "${CUDA_DROPIN}" <<EOF
# faster-whisper (CTranslate2) needs CUDA 12 cuBLAS + cuDNN on the library path.
# Installed into kiln's venv by install.sh --gpu-whisper. Regenerated on each such run.
[Service]
Environment=LD_LIBRARY_PATH=${ldpath}
EOF
    log "wrote ${CUDA_DROPIN}"
    systemctl daemon-reload
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
        create_user; make_dirs; install_code; install_config; install_unit
        $GPU_WHISPER && install_gpu_whisper
        run_doctor
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
    $GPU_WHISPER && install_gpu_whisper
    run_doctor
    log "done. Start the service with:  sudo systemctl start kiln"
}

main "$@"
