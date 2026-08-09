# SPDX-License-Identifier: MIT
"""Headless, input-free smoke tests.

These run on both Linux and Windows CI without a real display or any user input.
They validate the parts that don't need a human: module wiring, the neutral
key/action tables, and per-OS backend construction. On Windows, constructing the
backend + input source forces every ctypes signature and HOOKPROC callback to
bind — which is where untested ctypes code typically crashes.

The runner sets QT_QPA_PLATFORM=offscreen (and, on Linux, a dummy DISPLAY /
XDG_SESSION_TYPE so get_backend selects the X11 backend without connecting)."""

import sys

import pytest


def test_imports():
    """Every module imports cleanly, including the Qt render/app/tray layers."""
    import screen_annotator
    import screen_annotator.app          # noqa: F401  (imports render/tray/icon + PyQt6)
    import screen_annotator.bridge       # noqa: F401
    import screen_annotator.config       # noqa: F401
    import screen_annotator.icon         # noqa: F401
    import screen_annotator.render       # noqa: F401
    import screen_annotator.settings     # noqa: F401
    import screen_annotator.tray         # noqa: F401
    assert screen_annotator.__version__


def test_bridge_has_lifecycle_signals():
    """The tray/hotkey lifecycle depends on these signals existing."""
    from screen_annotator.bridge import SignalBridge

    bridge = SignalBridge()
    for name in ("do_quit", "toggle_overlay", "do_shutdown"):
        assert hasattr(bridge, name), f"bridge missing {name!r}"


def test_hotkey_parsing_round_trips():
    """The default chord parses to a (key, mods) pair and formats back."""
    from screen_annotator.config import (
        DEFAULT_HOTKEY, format_hotkey, parse_hotkey,
    )

    key, mods = parse_hotkey(DEFAULT_HOTKEY)
    assert key == "a" and mods == frozenset({"ctrl", "alt"})
    assert format_hotkey(DEFAULT_HOTKEY) == "Ctrl+Alt+A"
    assert parse_hotkey("") is None
    assert parse_hotkey("bogusmod+a") is None


def test_settings_hotkey_validation():
    """A usable global hotkey needs a modifier + a single letter/digit key."""
    from screen_annotator.settings import _is_valid

    assert _is_valid("ctrl+alt+a")
    assert _is_valid("ctrl+shift+5")
    assert not _is_valid("a")            # no modifier -> would steal a bare key
    assert not _is_valid("ctrl+alt+f1")  # multi-char key not supported by RegisterHotKey
    assert not _is_valid("")


class _FakeSignal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _FakeBridge:
    """Records emissions, so emit_action can be tested without Qt."""

    def __init__(self):
        self._signals = {}

    def __getattr__(self, name):
        # Lazily create a recording signal for any attribute accessed.
        sig = self.__dict__.setdefault("_signals", {}).get(name)
        if sig is None:
            sig = _FakeSignal()
            self._signals[name] = sig
        return sig


def test_every_action_is_handled():
    """emit_action must handle every action string in the key tables, so the
    neutral tables can't drift away from the dispatcher."""
    from screen_annotator.bridge import emit_action
    from screen_annotator.config import CONTROL_KEYS, MOD_SHORTCUTS

    actions = set(CONTROL_KEYS.values()) | set(MOD_SHORTCUTS.values())
    assert actions, "expected some actions defined"
    for action in actions:
        bridge = _FakeBridge()
        emit_action(bridge, action)
        emitted = [n for n, s in bridge._signals.items() if s.calls]
        assert len(emitted) == 1, f"action {action!r} emitted {emitted}"


def test_backend_construction():
    """get_backend() returns the right type for this OS, and the backend plus
    its input source construct without starting (validates ctypes bindings and
    HOOKPROC callbacks on Windows)."""
    from screen_annotator.backends import get_backend
    from screen_annotator.bridge import SignalBridge

    backend = get_backend()
    expected = {"linux": "X11Backend", "win32": "WindowsBackend"}
    key = "linux" if sys.platform.startswith("linux") else sys.platform
    if key in expected:
        assert type(backend).__name__ == expected[key]

    bridge = SignalBridge()
    source = backend.make_input_source(bridge, self_draw_cursor=False)
    assert hasattr(source, "start") and hasattr(source, "stop")
    # Deliberately do NOT start(): that would install global hooks / grab input.

    # The always-on hotkey listener must construct (binds RegisterHotKey /
    # XGrabKey plumbing) without starting either.
    from screen_annotator.config import DEFAULT_HOTKEY, parse_hotkey
    hotkey = backend.make_hotkey_listener(bridge, parse_hotkey(DEFAULT_HOTKEY))
    assert hasattr(hotkey, "start") and hasattr(hotkey, "stop")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only bindings")
def test_windows_vk_map_and_bindings():
    """The Windows VK table covers the neutral keys, and the ctypes module-level
    signature block imported without error."""
    from screen_annotator.backends import windows
    from screen_annotator.config import CONTROL_KEYS

    neutral = set(windows.VK_TO_NEUTRAL.values())
    # Every bare control key should be reachable from some virtual-key code.
    for key in CONTROL_KEYS:
        assert key in neutral, f"no VK maps to neutral key {key!r}"
