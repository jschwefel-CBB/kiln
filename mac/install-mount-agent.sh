#!/bin/bash
#
# install-mount-agent.sh — install the kiln inbox auto-mount LaunchAgent (persist across reboots).
#
# What it does:
#   1. Copies mount-inbox.sh to ~/bin/kiln-mount-inbox.sh (executable).
#   2. Materializes com.kiln.mount-inbox.plist into ~/Library/LaunchAgents/, substituting the
#      absolute script path (LaunchAgents can't use ~/$HOME).
#   3. Loads (bootstraps) the agent so it mounts now and at every login / network change.
#
# PREREQUISITE — store the SMB password in the login Keychain ONCE (no secret goes in any file):
#   security add-internet-password -a kilndrop -s 172.31.1.100 -r 'smb ' \
#     -l 'kiln-inbox (kilndrop@172.31.1.100)' -D 'network password' \
#     -w '<the-kilndrop-password>' -U -T /sbin/mount_smbfs -T /usr/bin/security
#
# Idempotent: re-running re-installs and reloads cleanly.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.kiln.mount-inbox"
SRC_SCRIPT="${SCRIPT_DIR}/mount-inbox.sh"
SRC_PLIST="${SCRIPT_DIR}/${LABEL}.plist"

DST_SCRIPT="${HOME}/bin/kiln-mount-inbox.sh"
DST_PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

[ -f "$SRC_SCRIPT" ] || { echo "missing $SRC_SCRIPT" >&2; exit 1; }
[ -f "$SRC_PLIST" ]  || { echo "missing $SRC_PLIST" >&2; exit 1; }

# 1. Install the mount script.
mkdir -p "${HOME}/bin"
cp "$SRC_SCRIPT" "$DST_SCRIPT"
chmod +x "$DST_SCRIPT"
echo "installed script -> $DST_SCRIPT"

# 2. Materialize the plist with the real script path.
mkdir -p "${HOME}/Library/LaunchAgents"
sed "s|__MOUNT_SCRIPT__|${DST_SCRIPT}|g" "$SRC_PLIST" > "$DST_PLIST"
echo "installed agent  -> $DST_PLIST"

# 3. (Re)load the agent for this GUI login session.
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
# bootout is fine to fail (agent may not be loaded yet); then bootstrap fresh.
launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "${DOMAIN}" "$DST_PLIST"
launchctl enable "${DOMAIN}/${LABEL}"
echo "agent loaded in ${DOMAIN}"

# Kick it once now so the mount is up immediately (don't wait for next login).
launchctl kickstart "${DOMAIN}/${LABEL}" 2>/dev/null || true

echo
echo "Done. The kiln inbox will auto-mount at /Volumes/kiln-inbox on login and on network change."
echo "Verify:  mount | grep kiln-inbox"
echo "Logs:    /tmp/${LABEL}.out  and  log show --predicate 'eventMessage contains \"kiln-mount\"' --last 5m"
