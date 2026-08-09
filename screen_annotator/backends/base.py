# SPDX-License-Identifier: MIT
"""Platform backend interface.

The render layer talks only to this; each OS provides a concrete backend. A
backend makes the Qt window click-through, manages the cursor, sets the
clipboard, and produces an InputSource that drives the SignalBridge from global
OS input events."""

from abc import ABC, abstractmethod


class InputSource(ABC):
    """Background capture of global input; emits SignalBridge signals."""

    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...


class PlatformBackend(ABC):
    @abstractmethod
    def apply_click_through(self, window) -> None:
        """Make the Qt window pass all input through to the document below."""

    @abstractmethod
    def hide_cursor(self) -> bool:
        """Hide the real cursor if possible. Return True when the app should
        self-draw the marker (real cursor hidden), False when the OS shows a
        cursor itself."""

    @abstractmethod
    def show_cursor(self) -> None:
        """Restore the real cursor (best-effort)."""

    @abstractmethod
    def make_input_source(self, bridge, self_draw_cursor: bool) -> InputSource:
        ...

    @abstractmethod
    def make_hotkey_listener(self, bridge, chord) -> InputSource:
        """Return an always-on InputSource that emits bridge.toggle_overlay when
        the global chord `(key_name, frozenset(mods))` is pressed. Runs
        independently of the drawing input source (which is only active while the
        overlay is visible)."""

    @abstractmethod
    def copy_pixmap_to_clipboard(self, pixmap) -> None:
        """Put a QPixmap on the system clipboard as an image."""
