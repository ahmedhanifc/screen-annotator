# SPDX-License-Identifier: MIT
"""Cross-thread channel between the input backend (worker thread) and the Qt
render layer (main thread). Backends emit; the Qt thread receives queued."""

from PyQt6.QtCore import QObject, pyqtSignal


class SignalBridge(QObject):
    """Xlib/Win thread emits, Qt thread receives (queued connections)."""

    stroke_begin = pyqtSignal(int, int)
    stroke_point = pyqtSignal(int, int)
    stroke_end = pyqtSignal()
    clear_canvas = pyqtSignal()
    do_copy = pyqtSignal()
    do_quit = pyqtSignal()          # Esc / toolbar ✕: hide the overlay (process survives)
    toggle_overlay = pyqtSignal()   # global hotkey / tray: show or hide the overlay
    do_shutdown = pyqtSignal()      # tray Quit / SIGINT / SIGTERM: exit the process
    change_size = pyqtSignal(int)
    change_color = pyqtSignal(int)
    toggle_hl = pyqtSignal()
    toggle_eraser = pyqtSignal()
    toggle_text = pyqtSignal()
    toggle_pin = pyqtSignal()
    clear_pins = pyqtSignal()
    do_undo = pyqtSignal()
    do_redo = pyqtSignal()
    toolbar_press = pyqtSignal(int, int)

    # Text-tool entry: the input thread emits these; the Qt thread keeps the
    # buffer and bakes it onto the canvas on commit.
    text_begin = pyqtSignal(int, int)   # insertion point (root coords)
    text_char = pyqtSignal(str)         # one character (or "\n")
    text_backspace = pyqtSignal()
    text_commit = pyqtSignal()
    text_cancel = pyqtSignal()

    # Set by the Qt thread before the input thread starts; read (only) by the
    # input thread as a plain (x, y, w, h) tuple to route toolbar clicks.
    toolbar_rect = None

    # Current tool name, written by the Qt thread (set_tool) and read by the
    # input thread to decide whether a Button-1 press draws or places text.
    current_tool = "pen"


def emit_action(bridge, action):
    """Translate a neutral action tag (from config.CONTROL_KEYS / MOD_SHORTCUTS)
    into the matching bridge signal. Shared by every input backend."""
    if action == "undo":
        bridge.do_undo.emit()
    elif action == "redo":
        bridge.do_redo.emit()
    elif action == "quit":
        bridge.do_quit.emit()
    elif action == "copy":
        bridge.do_copy.emit()
    elif action == "clear":
        bridge.clear_canvas.emit()
    elif action == "clear-pins":
        bridge.clear_pins.emit()
    elif action == "toggle-pin":
        bridge.toggle_pin.emit()
    elif action == "toggle-hl":
        bridge.toggle_hl.emit()
    elif action == "toggle-eraser":
        bridge.toggle_eraser.emit()
    elif action == "toggle-text":
        bridge.toggle_text.emit()
    elif action == "size+":
        bridge.change_size.emit(+1)
    elif action == "size-":
        bridge.change_size.emit(-1)
    elif action.startswith("color-"):
        bridge.change_color.emit(int(action.split("-")[1]))
