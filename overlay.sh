#!/usr/bin/env bash
#
# overlay.sh — launch/toggle the live click-through pen overlay (Linux/X11).
#
#   - Run once  -> starts the transparent overlay over the live screen.
#   - Run again -> quits it. Bind this to a hotkey (e.g. Super+A) to toggle.
#
# Prefers the installed `screen-annotator` command; falls back to running the
# package straight from this repo. No hardcoded paths. Tracks the running
# instance via a PID file (robust even though the repo dir is also named
# "screen-annotator").

set -euo pipefail

PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/screen-annotator.pid"

# Toggle: if a tracked instance is still alive, quit it instead of launching.
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    rm -f "$PIDFILE"
    exit 0
fi
rm -f "$PIDFILE"

if command -v screen-annotator >/dev/null 2>&1; then
    cmd=(screen-annotator)
else
    dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cmd=(env PYTHONPATH="$dir" python3 -m screen_annotator)
fi

"${cmd[@]}" &
echo $! > "$PIDFILE"
