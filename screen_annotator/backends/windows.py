# SPDX-License-Identifier: MIT
"""Windows backend.

Click-through: WS_EX_LAYERED | WS_EX_TRANSPARENT on the Qt HWND (the Windows
analogue of the empty X11 input shape — mouse falls through to the document).

Input layer: global low-level hooks (WH_MOUSE_LL + WH_KEYBOARD_LL) on a thread
with its own message pump. The callbacks mirror the X11 per-button logic and emit
the same SignalBridge signals, so the render layer is unchanged. Returning 1 from
a hook consumes the event; calling CallNextHookEx passes it through.

Cursor: Windows can't hide the system cursor for a background click-through
window, so the native pointer is used (no self-drawn marker). Clipboard: Qt's
clipboard, which is global on Windows and persists after the app exits.

Implemented with ctypes only (no third-party dependency)."""

import ctypes
import threading
from ctypes import wintypes

from ..bridge import emit_action
from ..config import CONTROL_KEYS, MOD_SHORTCUTS, dbg
from .base import InputSource, PlatformBackend

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# Explicit signatures matter on 64-bit Windows: without them ctypes assumes
# 32-bit int returns/args and truncates HHOOK/LRESULT/LPARAM values.
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [
    wintypes.LPMSG, wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.SetWindowLongW.restype = wintypes.LONG
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT
user32.GetKeyboardState.argtypes = [ctypes.c_void_p]
user32.GetKeyboardState.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.ToUnicode.argtypes = [
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
    wintypes.LPWSTR, ctypes.c_int, wintypes.UINT]
user32.ToUnicode.restype = ctypes.c_int
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

# -- window styles ---------------------------------------------------------- #
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
SWP_NOACTIVATE = 0x0010

# -- hooks / messages ------------------------------------------------------- #
WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
HC_ACTION = 0

WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

# -- RegisterHotKey modifiers ----------------------------------------------- #
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000   # don't repeat WM_HOTKEY while the chord is held

# -- virtual-key codes ------------------------------------------------------ #
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


# Neutral key name -> Windows virtual-key code(s), matching config's key tables.
def _build_vk_map():
    vk = {}
    for d in range(10):
        vk[0x30 + d] = str(d)   # top-row digits
        vk[0x60 + d] = str(d)   # numpad digits
    for ch in "hetpczy":
        vk[ord(ch.upper())] = ch
    vk[VK_RETURN] = "return"
    vk[0x6C] = "return"         # VK_SEPARATOR / numpad Enter reports as VK_RETURN
    vk[VK_ESCAPE] = "escape"
    vk[0xDB] = "bracketleft"    # VK_OEM_4
    vk[0xDD] = "bracketright"   # VK_OEM_6
    return vk


VK_TO_NEUTRAL = _build_vk_map()


def _held(vk):
    # Real-time physical state (GetKeyState reflects the thread's message queue,
    # which is unreliable inside a low-level hook).
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _hotkey_vk(key):
    """Virtual-key code for a single-character hotkey key name, or None."""
    if len(key) == 1:
        if key.isdigit():
            return 0x30 + int(key)
        if key.isalpha():
            return ord(key.upper())
    return None


def _hotkey_mods(mods):
    """RegisterHotKey fsModifiers bitmask for a neutral modifier set."""
    fs = 0
    if "ctrl" in mods:
        fs |= MOD_CONTROL
    if "alt" in mods:
        fs |= MOD_ALT
    if "shift" in mods:
        fs |= MOD_SHIFT
    if "super" in mods:
        fs |= MOD_WIN
    return fs


class _HookThread(threading.Thread, InputSource):
    """Owns the low-level hooks and their message pump."""

    def __init__(self, bridge):
        super().__init__(daemon=True)
        self.bridge = bridge
        self._thread_id = 0
        self._drawing = False
        self._text_mode = False
        # Keep the ctypes callbacks alive for the hooks' lifetime.
        self._mouse_proc = HOOKPROC(self._mouse_cb)
        self._kbd_proc = HOOKPROC(self._kbd_cb)
        self._mouse_hook = None
        self._kbd_hook = None

    # -- lifecycle ---------------------------------------------------------- #

    def run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_proc, None, 0)
        self._kbd_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kbd_proc, None, 0)
        if not self._mouse_hook or not self._kbd_hook:
            err = ctypes.get_last_error()
            self.bridge.do_quit.emit()
            raise ctypes.WinError(err)
        dbg("installed WH_MOUSE_LL + WH_KEYBOARD_LL hooks")

        # Standard LL-hook message pump; PostThreadMessage(WM_QUIT) ends it.
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
        if self._kbd_hook:
            user32.UnhookWindowsHookEx(self._kbd_hook)

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    # -- mouse -------------------------------------------------------------- #

    def _mouse_cb(self, nCode, wParam, lParam):
        if nCode == HC_ACTION:
            try:
                if self._handle_mouse(wParam, lParam):
                    return 1   # consume
            except Exception as exc:      # pragma: no cover - defensive
                dbg(f"mouse hook error: {exc}")
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _handle_mouse(self, msg, lParam):
        """Return True to consume the event. Mirrors x11._on_button_press."""
        info = ctypes.cast(
            lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        x, y = info.pt.x, info.pt.y

        if msg == WM_MOUSEMOVE:
            if self._drawing:
                self.bridge.stroke_point.emit(x, y)
                return True
            return False

        if msg == WM_LBUTTONDOWN:
            # End any open text box first (a click commits it).
            if self._text_mode:
                self._text_mode = False
                self.bridge.text_commit.emit()
            if self._in_toolbar(x, y):
                self.bridge.toolbar_press.emit(x, y)
                return True
            if getattr(self.bridge, "current_tool", None) == "text":
                self._text_mode = True
                self.bridge.text_begin.emit(x, y)
                return True
            self._drawing = True
            self.bridge.stroke_begin.emit(x, y)
            return True

        if msg == WM_LBUTTONUP:
            if self._drawing:
                self._drawing = False
                self.bridge.stroke_end.emit()
                return True
            return False

        if msg == WM_RBUTTONDOWN:
            if self._text_mode:
                self._text_mode = False
                self.bridge.text_cancel.emit()
            self.bridge.clear_canvas.emit()
            return True

        if msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            # Observe only: let the document scroll, use it to wipe the canvas.
            if self._text_mode:
                self._text_mode = False
                self.bridge.text_cancel.emit()
            self.bridge.clear_canvas.emit()
            return False

        return False

    def _in_toolbar(self, x, y):
        rect = getattr(self.bridge, "toolbar_rect", None)
        if not rect:
            return False
        rx, ry, rw, rh = rect
        return rx <= x < rx + rw and ry <= y < ry + rh

    # -- keyboard ----------------------------------------------------------- #

    def _kbd_cb(self, nCode, wParam, lParam):
        if nCode == HC_ACTION and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            try:
                info = ctypes.cast(
                    lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if self._handle_key(info.vkCode, info.scanCode):
                    return 1   # consume
            except Exception as exc:      # pragma: no cover - defensive
                dbg(f"keyboard hook error: {exc}")
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _handle_key(self, vk, scan):
        """Return True to consume the key. Mirrors x11._on_key / _on_text_key."""
        ctrl = _held(VK_CONTROL)
        shift = _held(VK_SHIFT)

        if self._text_mode:
            return self._handle_text_key(vk, scan, shift)

        mods = set()
        if ctrl:
            mods.add("ctrl")
        if shift:
            mods.add("shift")
        name = VK_TO_NEUTRAL.get(vk)
        if name is None:
            return False
        action = MOD_SHORTCUTS.get((name, frozenset(mods)))
        # Bare control keys only fire without Ctrl/Shift, so we never steal
        # Ctrl+C / Ctrl+V etc. from the document.
        if action is None and not mods:
            action = CONTROL_KEYS.get(name)
        if action is None:
            return False
        emit_action(self.bridge, action)
        return True

    def _handle_text_key(self, vk, scan, shift):
        if vk == VK_ESCAPE:
            self._text_mode = False
            self.bridge.text_cancel.emit()
            return True
        if vk == VK_RETURN:
            if shift:
                self.bridge.text_char.emit("\n")
            else:
                self._text_mode = False
                self.bridge.text_commit.emit()
            return True
        if vk == VK_BACK:
            self.bridge.text_backspace.emit()
            return True
        if vk == VK_TAB:
            self.bridge.text_char.emit("\t")
            return True
        ch = self._vk_to_char(vk, scan)
        if ch and ch.isprintable():
            self.bridge.text_char.emit(ch)
        return True

    def _vk_to_char(self, vk, scan):
        """Translate a virtual key to its typed character via the current
        keyboard layout/state (honours Shift, CapsLock, AltGr)."""
        state = (ctypes.c_ubyte * 256)()
        user32.GetKeyboardState(ctypes.byref(state))
        buf = ctypes.create_unicode_buffer(8)
        n = user32.ToUnicode(vk, scan, state, buf, len(buf), 0)
        if n > 0:
            return buf[:n]
        return ""


class _HotkeyThread(threading.Thread, InputSource):
    """Always-on global toggle hotkey via RegisterHotKey + a message pump. Kept
    separate from the drawing LL-hook thread so it fires whether the overlay is
    up or down."""

    _ID = 1

    def __init__(self, bridge, chord):
        super().__init__(daemon=True)
        self.bridge = bridge
        self._chord = chord          # (key_name, frozenset(mods))
        self._thread_id = 0

    def run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        key, mods = self._chord
        vk = _hotkey_vk(key)
        if vk is None:
            dbg(f"hotkey: unsupported key {key!r}")
            return
        fs = _hotkey_mods(mods) | MOD_NOREPEAT
        # hWnd NULL: WM_HOTKEY is posted to this thread's message queue.
        if not user32.RegisterHotKey(None, self._ID, fs, vk):
            dbg(f"RegisterHotKey failed (chord in use?): {ctypes.get_last_error()}")
            return
        dbg(f"registered global hotkey vk={vk:#x} mods={fs:#x}")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.bridge.toggle_overlay.emit()
        user32.UnregisterHotKey(None, self._ID)

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


class WindowsBackend(PlatformBackend):
    def apply_click_through(self, window):
        hwnd = int(window.winId())
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex |= (WS_EX_LAYERED | WS_EX_TRANSPARENT
               | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        # Force the new frame styles to take effect.
        user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
            | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )

    def hide_cursor(self):
        # Can't reliably hide the OS cursor for a background click-through
        # window; use the native pointer and skip the self-drawn marker.
        return False

    def show_cursor(self):
        pass

    def make_input_source(self, bridge, self_draw_cursor):
        return _HookThread(bridge)

    def make_hotkey_listener(self, bridge, chord):
        return _HotkeyThread(bridge, chord)

    def copy_pixmap_to_clipboard(self, pixmap):
        from PyQt6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setPixmap(pixmap)
