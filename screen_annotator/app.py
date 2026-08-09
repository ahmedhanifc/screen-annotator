# SPDX-License-Identifier: MIT
"""Entry point: a background tray app.

An always-on global hotkey shows/hides a click-through overlay. The overlay's
modal drawing grabs are held only while it's visible; the toggle hotkey and the
tray live for the whole process. Hiding the overlay does NOT exit — the app
stays in the tray until the user quits."""

import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .backends import get_backend
from .bridge import SignalBridge
from .config import (
    CONFIG_PATH, CURSOR_POLL_MS, DEFAULT_HOTKEY, dbg, format_hotkey, load_prefs,
    parse_hotkey, save_prefs,
)
from .icon import make_app_icon
from .render import OverlayWindow
from .tray import TrayController


class AppController:
    """Owns the overlay show/hide lifecycle, the modal drawing input source, and
    the always-on toggle hotkey.

    Show = fresh canvas + click-through + cursor + start drawing input.
    Hide = stop drawing input + restore cursor + hide the window (process lives).
    The hotkey lives here too so Settings can re-register it live."""

    def __init__(self, app, backend, bridge, overlay):
        self._app = app
        self._backend = backend
        self._bridge = bridge
        self._overlay = overlay
        self._input_source = None
        self._cursor_timer = None
        self._visible = False
        self._hotkey = None
        self._hotkey_spec = None

    @property
    def hotkey_spec(self):
        return self._hotkey_spec

    def set_hotkey(self, spec):
        """(Re)register the always-on toggle hotkey. Returns False if the spec
        doesn't parse; the previous hotkey is left running in that case."""
        chord = parse_hotkey(spec)
        if chord is None:
            return False
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey.join(timeout=1.0)   # let it release the old grab first
        self._hotkey = self._backend.make_hotkey_listener(self._bridge, chord)
        self._hotkey.start()
        self._hotkey_spec = spec
        dbg(f"hotkey set to {spec!r}")
        return True

    def stop_hotkey(self):
        if self._hotkey is not None:
            self._hotkey.stop()

    def toggle(self):
        self.hide_overlay() if self._visible else self.show_overlay()

    def show_overlay(self):
        if self._visible:
            return
        self._visible = True
        self._overlay.reset_for_show()
        self._overlay.showFullScreen()
        self._overlay.raise_()

        # Click-through must be (re)applied after the native window exists; the
        # singleShot re-applies cover X11 stamping its own input shape on first
        # expose (harmless elsewhere).
        self._backend.apply_click_through(self._overlay)
        QTimer.singleShot(0, lambda: self._backend.apply_click_through(self._overlay))
        QTimer.singleShot(300, lambda: self._backend.apply_click_through(self._overlay))

        # Self-draw a marker cursor for as long as the overlay is up, if the
        # backend can hide the real cursor.
        self_draw = self._backend.hide_cursor()
        if self_draw:
            self._overlay.enable_cursor(True)
            self._cursor_timer = QTimer()
            self._cursor_timer.timeout.connect(self._overlay.poll_cursor)
            self._cursor_timer.start(CURSOR_POLL_MS)

        self._input_source = self._backend.make_input_source(
            self._bridge, self_draw_cursor=self_draw)
        self._input_source.start()
        dbg("overlay shown")

    def hide_overlay(self):
        if not self._visible:
            return
        self._visible = False
        # Stop the modal drawing grabs first, so input reaches apps again.
        if self._input_source is not None:
            self._input_source.stop()
            self._input_source = None
        if self._cursor_timer is not None:
            self._cursor_timer.stop()
            self._cursor_timer = None
        self._overlay.enable_cursor(False)
        self._backend.show_cursor()
        self._overlay.hide()
        dbg("overlay hidden")

    def shutdown(self):
        self.hide_overlay()
        self.stop_hotkey()
        self._app.quit()


def main():
    app = QApplication(sys.argv)
    # The app lives in the tray; hiding the overlay must not exit the process.
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(make_app_icon())

    backend = get_backend()   # exits with a message on unsupported platforms

    bridge = SignalBridge()
    overlay = OverlayWindow(bridge, backend)
    controller = AppController(app, backend, bridge, overlay)

    # Esc / toolbar ✕ hide the overlay (process survives); the hotkey and tray
    # click toggle show/hide; tray Quit / signals fully exit.
    bridge.do_quit.connect(controller.hide_overlay)
    bridge.toggle_overlay.connect(controller.toggle)
    bridge.do_shutdown.connect(controller.shutdown)

    # Register the saved hotkey (or the default, if none saved / invalid).
    first_run = not CONFIG_PATH.exists()
    saved = load_prefs().get("hotkey")
    controller.set_hotkey(saved if parse_hotkey(saved or "") else DEFAULT_HOTKEY)

    def open_settings():
        from .settings import SettingsDialog
        controller.hide_overlay()   # don't edit while the overlay grabs input
        dlg = SettingsDialog(controller.hotkey_spec)
        if dlg.exec():
            spec = dlg.hotkey_spec()
            if spec and controller.set_hotkey(spec):
                save_prefs(hotkey=spec)
                tray.refresh_labels()

    tray = TrayController(controller, open_settings)

    # First launch: create the prefs file (so this hint is one-time) and point
    # the user at the hotkey they'd otherwise have no way to discover.
    if first_run:
        save_prefs(hotkey=controller.hotkey_spec)
        tray.notify(
            "screen-annotator is running",
            f"Press {format_hotkey(controller.hotkey_spec)} anywhere to draw. "
            "Right-click the tray icon for settings.")

    # Let Python process SIGINT/SIGTERM even while inside the Qt event loop.
    signal.signal(signal.SIGINT, lambda *_: bridge.do_shutdown.emit())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: bridge.do_shutdown.emit())
    heartbeat = QTimer()
    heartbeat.start(200)
    heartbeat.timeout.connect(lambda: None)

    app.aboutToQuit.connect(controller.hide_overlay)
    app.aboutToQuit.connect(controller.stop_hotkey)

    code = app.exec()
    controller.stop_hotkey()
    sys.exit(code)


if __name__ == "__main__":
    main()
