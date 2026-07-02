#!/usr/bin/env bash
#
# kiln installer — sets up kiln as a systemd service on a Linux host with an NVIDIA GPU.
#
# What it does (Phase 4 will flesh out the TODO sections):
#   1. Create a dedicated unprivileged service user `kiln`.
#   2. Install the package into /opt/kiln with a pinned virtualenv.
#   3. Install and enable the systemd unit.
#   4. Run `kiln doctor` to verify GPU detection and storage reachability.
#
# Run with: sudo ./install.sh
#
# This is a scaffold. It intentionally refuses to run until Phase 4 implements it, so a
# half-built installer can never leave a machine in a partial state.

set -euo pipefail

INSTALL_DIR="/opt/kiln"
SERVICE_USER="kiln"

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "install.sh must be run as root (use: sudo ./install.sh)" >&2
        exit 1
    fi
}

main() {
    require_root
    echo "kiln installer — target: ${INSTALL_DIR}, service user: ${SERVICE_USER}"

    # TODO(Phase 4): create service user if missing
    # TODO(Phase 4): rsync/copy package to ${INSTALL_DIR}, build ${INSTALL_DIR}/.venv, pip install .
    # TODO(Phase 4): install packaging/kiln.service to /etc/systemd/system, daemon-reload, enable
    # TODO(Phase 4): su - ${SERVICE_USER} -c "${INSTALL_DIR}/.venv/bin/kiln doctor"

    echo "install.sh is a scaffold; implementation lands in Phase 4 (service packaging)." >&2
    exit 2
}

main "$@"
