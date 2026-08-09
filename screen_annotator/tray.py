# SPDX-License-Identifier: MIT
"""System-tray presence and menu.

Keeps the app alive in the background and lets the user toggle the overlay, open
settings, or quit without touching the keyboard. The always-on global hotkey
(see the backends) works whether or not a tray is actually available."""

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from .config import dbg, format_hotkey
from .icon import make_app_icon


class TrayController(QObject):
    """Owns the QSystemTrayIcon + its menu. `controller` provides toggle(),
    shutdown() and the current hotkey_spec; `on_settings` opens the settings
    dialog."""

    def __init__(self, controller, on_settings):
        super().__init__()
        self._controller = controller

        self.tray = QSystemTrayIcon(make_app_icon())

        # Held as an attribute so the menu isn't garbage-collected.
        self._menu = QMenu()
        self._toggle = self._menu.addAction("Toggle overlay")
        self._toggle.triggered.connect(controller.toggle)
        settings = self._menu.addAction("Settings…")
        settings.triggered.connect(on_settings)
        self._menu.addSeparator()
        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(controller.shutdown)
        self.tray.setContextMenu(self._menu)

        # A left click / double-click on the icon also toggles the overlay.
        self.tray.activated.connect(self._on_activated)

        self.refresh_labels()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            dbg("system tray unavailable; the global hotkey still toggles the overlay")
        self.tray.show()

    def refresh_labels(self):
        """Re-read the current hotkey into the menu label + tooltip."""
        label = format_hotkey(self._controller.hotkey_spec)
        self._toggle.setText(f"Toggle overlay ({label})")
        self.tray.setToolTip(f"screen-annotator — press {label} to draw")

    def notify(self, title, message):
        """Best-effort tray balloon (no-op if the platform lacks one)."""
        self.tray.showMessage(title, message)

    def _on_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._controller.toggle()
