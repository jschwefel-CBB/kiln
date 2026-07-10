#!/bin/bash
#
# kiln-folder-action.sh — shared settle-check-then-submit logic for the kiln Folder Actions.
#
# Invoked by each preset's Automator Folder Action ("Run Shell Script", /bin/bash, input
# "as arguments") as:
#   kiln-folder-action.sh <preset> "$@"
# where <preset> is standard|4k|upscale|transcode-only (hardcoded per folder) and "$@" are
# the file paths Automator passes for files just added to the watched staging folder.
#
# Folder Actions can fire while FCP is still writing the ProRes master. Per
# docs/specs/2026-07-03-mac-controller-spec.md §7.1 step 2, wait until each file's size is
# stable for 3 consecutive seconds before handing off — this is the Folder-Action equivalent
# of Compressor's "run only after fully written" guarantee that this design forgoes.
#
# This is a LIBRARY script (one task = the settle+submit action; not run standalone).

set -u

PRESET="${1:?usage: kiln-folder-action.sh <preset> FILE...}"
shift

for f in "$@"; do
  case "$f" in */.*) continue;; esac          # ignore dotfiles/temp
  [ -f "$f" ] || continue

  last=-1
  while :; do
    cur=$(/usr/bin/stat -f%z "$f" 2>/dev/null) || break
    [ "$cur" = "$last" ] && [ "$cur" -gt 0 ] && break
    last=$cur
    /bin/sleep 3
  done

  /usr/bin/osascript "$HOME/bin/kiln-submit.js" "$f" --preset "$PRESET"
done
