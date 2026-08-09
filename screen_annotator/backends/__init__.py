# SPDX-License-Identifier: MIT
"""Backend selection."""

import os
import sys


def get_backend():
    """Return the PlatformBackend for the current OS, or exit with a clear
    message when the platform/session is unsupported."""
    if sys.platform.startswith("linux"):
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session == "wayland" or not os.environ.get("DISPLAY"):
            sys.exit(
                "screen-annotator needs an X11 session. You appear to be on "
                "Wayland (or no X display is set).\n"
                "On GNOME/KDE you can log in to an 'Xorg'/'X11' session from the "
                "login screen. Wayland support is on the roadmap."
            )
        from .x11 import X11Backend
        return X11Backend()

    if sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()

    if sys.platform == "darwin":
        sys.exit(
            "screen-annotator does not support macOS yet. "
            "Contributions welcome — see implementation_details/."
        )

    sys.exit(f"screen-annotator: unsupported platform {sys.platform!r}.")
