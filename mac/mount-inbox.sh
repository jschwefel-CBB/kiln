#!/bin/bash
#
# mount-inbox.sh — mount the kiln SMB inbox at /Volumes/kiln-inbox (idempotent).
#
# Pulls the kilndrop SMB password from the login Keychain at runtime — NO secret lives
# in this file, in the LaunchAgent plist, or anywhere else on disk. Store it once with:
#
#   security add-internet-password -a kilndrop -s 172.31.1.100 -r 'smb ' \
#     -l 'kiln-inbox (kilndrop@172.31.1.100)' -D 'network password' \
#     -w '<the-kilndrop-password>' -U -T /sbin/mount_smbfs -T /usr/bin/security
#
# Run at login + on network change by ~/Library/LaunchAgents/com.kiln.mount-inbox.plist.
# Safe to run by hand any time; if already mounted it exits 0 without doing anything.
#
# NOTE: kiln lives on the WIRED interface 172.31.1.100. The name `deb005` resolves to the
# Wi-Fi interface (172.31.1.196) — the WRONG box interface — so this uses the IP, never the name.
#
# Mounting: uses the NetFS `open smb://` mounter (the same path Finder's Cmd-K uses).
# `mount_smbfs` to a hand-made /Volumes mountpoint fails with "Operation not permitted" on
# recent macOS, so it is NOT used. NetFS mounts at the canonical /Volumes/kiln-inbox and
# manages that mountpoint itself — do NOT pre-create it, or NetFS mounts at kiln-inbox-1.

set -u

SERVER="172.31.1.100"        # wired interface — do NOT use the name `deb005` (Wi-Fi = .196)
SHARE="kiln-inbox"
USER_ACCT="kilndrop"
MOUNTPOINT="/Volumes/${SHARE}"
LOG_TAG="kiln-mount"

log() { /usr/bin/logger -t "$LOG_TAG" "$*"; }

# Already mounted at the expected point? Nothing to do.
if /sbin/mount | /usr/bin/grep -q " on ${MOUNTPOINT} (smbfs"; then
  log "already mounted at ${MOUNTPOINT}"
  exit 0
fi

# A stale EMPTY /Volumes/kiln-inbox (left by a prior failed mount) makes NetFS mount at
# kiln-inbox-1 instead. Remove it if it's an empty, non-mounted directory.
if [ -d "$MOUNTPOINT" ] && ! /sbin/mount | /usr/bin/grep -q " on ${MOUNTPOINT} "; then
  if [ -z "$(/bin/ls -A "$MOUNTPOINT" 2>/dev/null)" ]; then
    /bin/rmdir "$MOUNTPOINT" 2>/dev/null || /usr/bin/sudo /bin/rmdir "$MOUNTPOINT" 2>/dev/null || \
      log "warn: stale empty ${MOUNTPOINT} present and not removable; NetFS may use ${MOUNTPOINT}-1"
  fi
fi

# Server reachable on SMB (445) before we try — avoids hanging at login off-network.
if ! /usr/bin/nc -z -G 5 "$SERVER" 445 >/dev/null 2>&1; then
  log "server ${SERVER}:445 not reachable; skipping mount"
  exit 0
fi

# Pull the password from the login Keychain into a variable ONLY (never written to disk).
PW="$(/usr/bin/security find-internet-password -a "$USER_ACCT" -s "$SERVER" -r 'smb ' -w 2>/dev/null)"
if [ -z "$PW" ]; then
  log "no keychain password for ${USER_ACCT}@${SERVER}; cannot mount (store it — see script header)"
  exit 1
fi

# NetFS `open` wants a URL-encoded password. Percent-encode everything that isn't unreserved.
urlencode() {
  local s="$1" out="" c i
  for (( i=0; i<${#s}; i++ )); do
    c="${s:$i:1}"
    case "$c" in
      [a-zA-Z0-9.~_-]) out+="$c" ;;
      *) out+=$(printf '%%%02X' "'$c") ;;
    esac
  done
  printf '%s' "$out"
}
PW_ENC="$(urlencode "$PW")"
PW=""   # clear cleartext copy as soon as it's encoded

# Mount via the NetFS mounter (async — it returns immediately, mount lands a few seconds later).
/usr/bin/open "smb://${USER_ACCT}:${PW_ENC}@${SERVER}/${SHARE}" >/dev/null 2>&1
PW_ENC=""   # clear the encoded copy too

# Poll for the canonical mountpoint to appear (up to ~20s).
for _ in $(seq 1 20); do
  if /sbin/mount | /usr/bin/grep -q " on ${MOUNTPOINT} (smbfs"; then
    log "mounted //${USER_ACCT}@${SERVER}/${SHARE} at ${MOUNTPOINT}"
    exit 0
  fi
  /bin/sleep 1
done

log "FAILED to mount ${SHARE} at ${MOUNTPOINT} within timeout"
exit 1
