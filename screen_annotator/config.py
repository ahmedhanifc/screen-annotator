# SPDX-License-Identifier: MIT
"""Shared, platform-neutral configuration: palette, tool metrics, persisted
preferences, and the key/action tables the input backends map onto."""

import json
import os
import sys
from pathlib import Path

# Set OVERLAY_DEBUG=1 to trace input events / actions on stderr.
DEBUG = bool(os.environ.get("OVERLAY_DEBUG"))


def dbg(msg):
    if DEBUG:
        sys.stderr.write(f"[overlay] {msg}\n")
        sys.stderr.flush()


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #

# Palette bound to number keys 0-9.
PALETTE = [
    ("#ff2d2d", "red"),
    ("#ff8c00", "orange"),
    ("#ffd500", "yellow"),
    ("#31d843", "green"),
    ("#12d7d7", "cyan"),
    ("#2e7bff", "blue"),
    ("#c65bff", "violet"),
    ("#ffffff", "white"),
    ("#101010", "black"),
    ("#9aa0a6", "grey"),
]

DEFAULT_COLOR_INDEX = 0
DEFAULT_WIDTH = 4
MIN_WIDTH = 1
MAX_WIDTH = 60
HIGHLIGHTER_ALPHA = 45          # 0-255; translucent
HIGHLIGHTER_WIDTH_FACTOR = 4    # highlighter is this much fatter than the pen
ERASER_WIDTH_FACTOR = 6         # eraser is this much fatter than the pen
TEXT_SIZE_FACTOR = 8            # text pixel size = width * this (min 12)
UNDO_LIMIT = 25                 # max canvas snapshots kept for undo

# Bundled handwriting font for the text tool (assets/, SIL OFL 1.1). Its small
# x-height is why TEXT_SIZE_FACTOR is larger than the pen-width factors.
TEXT_FONT_FILE = "Caveat-Bold.ttf"

# Drawing tools, cycled through the toolbar (or the h / e / t keys).
TOOLS = ("pen", "highlighter", "eraser", "text")

TOAST_MS = 1100       # how long a status toast stays on screen
CURSOR_POLL_MS = 16   # ~60 Hz refresh for the self-drawn marker cursor
CURSOR_RADIUS = 40    # repaint radius (px) around the marker as it moves


# --------------------------------------------------------------------------- #
# Persisted preferences (last-selected tool / colour / size)
# --------------------------------------------------------------------------- #

CONFIG_PATH = Path(os.path.expanduser("~/.config/screen-annotator/prefs.json"))


def load_prefs():
    """Return the saved prefs dict, or {} on any error (missing/corrupt file)."""
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_prefs(**changes):
    """Merge `changes` into the saved prefs and write back. Never raise — a
    failed save must not crash. Merging (rather than overwriting) lets the
    drawing prefs (tool/color_index/width) and the hotkey be saved independently
    without clobbering each other."""
    try:
        prefs = load_prefs()
        prefs.update(changes)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(prefs))
    except Exception as exc:
        dbg(f"save_prefs failed: {exc}")


# --------------------------------------------------------------------------- #
# Global toggle hotkey (platform-neutral chord)
#
# Represented on disk / in the UI as a "ctrl+alt+a"-style string; backends
# translate the parsed (key, mods) chord to their OS hotkey API. Kept here with
# the other neutral tables so "which chord toggles the overlay" lives in one
# place. Persistence + a Settings editor come in the settings layer.
# --------------------------------------------------------------------------- #

DEFAULT_HOTKEY = "ctrl+alt+a"

_HOTKEY_MOD_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift",
    "alt": "alt", "option": "alt",
    "super": "super", "win": "super", "cmd": "super", "meta": "super",
}


def parse_hotkey(spec):
    """Parse a chord like 'ctrl+alt+a' into (key_name, frozenset(mods)), or None
    if the spec is empty or names an unknown modifier."""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        return None
    key = parts[-1]
    mods = set()
    for m in parts[:-1]:
        canon = _HOTKEY_MOD_ALIASES.get(m)
        if canon is None:
            return None
        mods.add(canon)
    return (key, frozenset(mods)) if key else None


def format_hotkey(spec):
    """Human-readable label for a chord spec, e.g. 'Ctrl+Alt+A'."""
    parsed = parse_hotkey(spec)
    if parsed is None:
        return str(spec)
    key, mods = parsed
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "super": "Super"}
    label = [names[m] for m in ("ctrl", "alt", "shift", "super") if m in mods]
    label.append(key.upper() if len(key) == 1 else key.capitalize())
    return "+".join(label)


# --------------------------------------------------------------------------- #
# Key / action tables (platform-neutral)
#
# Each input backend translates its own OS key representation to these neutral
# key names, then looks up the action here. This keeps "which key does what" in
# one place; backends only know "how do I detect key X on my OS".
# --------------------------------------------------------------------------- #

# Bare keys (no Ctrl/Shift) -> action.
CONTROL_KEYS = {
    "return": "copy",
    "escape": "quit",
    "bracketleft": "size-",
    "bracketright": "size+",
    "h": "toggle-hl",
    "e": "toggle-eraser",
    "t": "toggle-text",
    "p": "toggle-pin",
    "c": "clear",
    **{str(d): f"color-{d}" for d in range(10)},
}

# (key, frozenset-of-modifiers) -> action. Grabbed *with* their modifier so they
# only fire on the exact chord — e.g. Ctrl+Z, never a bare Z.
MOD_SHORTCUTS = {
    ("z", frozenset({"ctrl"})): "undo",
    ("z", frozenset({"ctrl", "shift"})): "redo",
    ("y", frozenset({"ctrl"})): "redo",
    ("c", frozenset({"shift"})): "clear-pins",
}
