# SPDX-License-Identifier: MIT
"""X11 backend (Linux).

Input layer: a background thread on its own X connection that decides, per
button, what to capture vs. pass through:
  - Button 1 (draw)  -> passive core grab on root; consumed.
  - Button 3 (clear) -> passive core grab; consumed.
  - Buttons 4/5/6/7 (scroll) -> observed via XInput2 raw events; never consumed,
                        so the document scrolls; used only to trigger a wipe.
  - Control keys     -> passive core grabs on root.
Click-through is an empty X11 input shape (SHAPE ext); the cursor is hidden via
XFixes so the overlay can self-draw a pencil (grab-cursor fallback otherwise)."""

import select
import struct
import subprocess
import sys
import threading

from Xlib import X, XK, Xcursorfont, display
from Xlib.ext import shape, xinput, xfixes

from ..bridge import emit_action
from ..config import CONTROL_KEYS, MOD_SHORTCUTS, dbg
from .base import InputSource, PlatformBackend

SCROLL_BUTTONS = frozenset({4, 5, 6, 7})   # wheel up/down + horizontal
DRAW_BUTTON = 1
CLEAR_BUTTON = 3

# Grab the bare key under every lock-key combination so CapsLock / NumLock don't
# swallow our control keys.
LOCK_COMBOS = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)


def _x_keysym_names(neutral):
    """Map a neutral key name (config tables) to the X keysym name(s) to grab,
    including keypad variants where they exist."""
    special = {
        "return": ("Return", "KP_Enter"),
        "escape": ("Escape",),
        "bracketleft": ("bracketleft",),
        "bracketright": ("bracketright",),
    }
    if neutral in special:
        return special[neutral]
    if len(neutral) == 1 and neutral.isdigit():
        return (neutral, f"KP_{neutral}")
    return (neutral,)   # single letters: h / e / t / p / c


def _x_modmask(mods):
    m = 0
    if "ctrl" in mods:
        m |= X.ControlMask
    if "shift" in mods:
        m |= X.ShiftMask
    return m


def _hotkey_modmask(mods):
    """Like _x_modmask but also covers Alt/Super, which the toggle hotkey may
    use (the drawing shortcuts only ever use Ctrl/Shift)."""
    m = _x_modmask(mods)
    if "alt" in mods:
        m |= X.Mod1Mask
    if "super" in mods:
        m |= X.Mod4Mask
    return m


class InputThread(threading.Thread, InputSource):
    """Grab draw/clear buttons + control keys, observe scroll, emit signals."""

    # XI2 raw button-press layout, past the generic-event header, is:
    #   deviceid (H) | time (I) | detail/button (I) | ...
    # python-xlib 0.33 has no parser for raw events, so decode `detail` by hand.
    _RAW_DETAIL_OFFSET = 6

    def __init__(self, bridge, use_grab_cursor: bool = True):
        super().__init__(daemon=True)
        self.bridge = bridge
        self.use_grab_cursor = use_grab_cursor
        self._running = True
        self._drawing = False
        self._text_mode = False   # actively editing a text box (keyboard grabbed)
        self._grabbed_keys = []   # (keycode, modifier) pairs to ungrab on exit
        self._mod_actions = {}    # (keycode, base modmask) -> action
        self.display = None
        self.root = None
        self._cursor = X.NONE     # fallback marker cursor (while a button held)

    # -- setup -------------------------------------------------------------- #

    def _make_marker_cursor(self):
        """Build a pencil/marker cursor from the X cursor font (fallback path,
        used only when the persistent self-drawn cursor is unavailable)."""
        try:
            font = self.display.open_font("cursor")
            return font.create_glyph_cursor(
                font,
                Xcursorfont.pencil,
                Xcursorfont.pencil + 1,   # cursor-font masks are glyph + 1
                (0, 0, 0),                # black tip
                (65535, 65535, 65535),    # white outline
            )
        except Exception as exc:          # pragma: no cover - defensive
            dbg(f"marker cursor unavailable, using default: {exc}")
            return X.NONE

    def _keycode_actions(self):
        """Map grabbed keycodes to their action tags (bare control keys)."""
        actions = {}
        for name, action in CONTROL_KEYS.items():
            for xname in _x_keysym_names(name):
                keysym = XK.string_to_keysym(xname)
                if not keysym:
                    continue
                keycode = self.display.keysym_to_keycode(keysym)
                if keycode:
                    actions[keycode] = action
        return actions

    def _grab(self):
        # Draw + clear buttons: passive core grabs on root, consumed (owner=False).
        # AnyModifier so drawing works with Shift/Ctrl held.
        event_mask = (
            X.ButtonPressMask | X.ButtonReleaseMask | X.Button1MotionMask
        )
        for button in (DRAW_BUTTON, CLEAR_BUTTON):
            self.root.grab_button(
                button, X.AnyModifier, True, event_mask,
                X.GrabModeAsync, X.GrabModeAsync, X.NONE, self._cursor,
            )

        # Control keys: grab the bare key under every lock combo. We deliberately
        # DON'T use AnyModifier — that would steal Ctrl+C etc. from the document.
        for keycode in self._keycode_actions():
            for mod in LOCK_COMBOS:
                self.root.grab_key(
                    keycode, mod, True, X.GrabModeAsync, X.GrabModeAsync,
                )
                self._grabbed_keys.append((keycode, mod))

        # Modifier-qualified shortcuts (Ctrl+Z etc.): grab the chord under each
        # lock combo, and remember the (keycode, base modmask) -> action mapping.
        for (name, mods), action in MOD_SHORTCUTS.items():
            keysym = XK.string_to_keysym(name)
            keycode = self.display.keysym_to_keycode(keysym) if keysym else 0
            if not keycode:
                continue
            basemod = _x_modmask(mods)
            self._mod_actions[(keycode, basemod)] = action
            for lock in LOCK_COMBOS:
                self.root.grab_key(
                    keycode, basemod | lock, True,
                    X.GrabModeAsync, X.GrabModeAsync,
                )
                self._grabbed_keys.append((keycode, basemod | lock))

        # Scroll: observe (never grab) via XInput2 raw button presses.
        self.root.xinput_select_events(
            [(xinput.AllMasterDevices, xinput.RawButtonPressMask)]
        )
        self.display.sync()
        dbg(f"grabbed buttons {DRAW_BUTTON},{CLEAR_BUTTON}; "
            f"{len(self._grabbed_keys)} key-grabs; observing scroll")

    def _ungrab(self):
        try:
            # Release an active keyboard grab first (a text box may be open when
            # the overlay is hidden) so the keyboard can never get stuck.
            self.display.ungrab_keyboard(X.CurrentTime)
            self.root.ungrab_button(DRAW_BUTTON, X.AnyModifier)
            self.root.ungrab_button(CLEAR_BUTTON, X.AnyModifier)
            for keycode, mod in self._grabbed_keys:
                self.root.ungrab_key(keycode, mod)
            self.display.sync()
        except Exception:
            pass

    # -- event loop --------------------------------------------------------- #

    def run(self):
        self.display = display.Display()
        self.root = self.display.screen().root
        self.display.xinput_query_version()
        if self.use_grab_cursor:
            self._cursor = self._make_marker_cursor()
        actions = self._keycode_actions()
        try:
            self._grab()
        except Exception as exc:  # pragma: no cover - defensive
            sys.stderr.write(f"screen-annotator: failed to grab input: {exc}\n")
            self.bridge.do_quit.emit()
            return

        while self._running:
            # Wake periodically so stop() (now called on every hide, not just at
            # process exit) releases the grabs promptly instead of blocking here
            # until the next X event arrives.
            if self.display.pending_events() == 0:
                try:
                    ready, _, _ = select.select([self.display], [], [], 0.05)
                except Exception:
                    break
                if not ready:
                    continue
            try:
                ev = self.display.next_event()
            except Exception:
                break

            et = ev.type
            if et == X.ButtonPress:
                self._on_button_press(ev)
            elif et == X.MotionNotify:
                if self._drawing:
                    self.bridge.stroke_point.emit(ev.root_x, ev.root_y)
            elif et == X.ButtonRelease:
                if ev.detail == DRAW_BUTTON and self._drawing:
                    self._drawing = False
                    self.bridge.stroke_end.emit()
            elif et == X.KeyPress:
                if self._text_mode:
                    self._on_text_key(ev)
                else:
                    self._on_key(ev, actions)
            elif getattr(ev, "evtype", None) == xinput.RawButtonPress:
                self._on_raw_button(ev)

        self._ungrab()

    def _on_button_press(self, ev):
        # Any button press ends an in-progress text box, then the click is
        # handled normally. A draw-button press commits (bakes) the buffer; a
        # clear-button (right-click) press discards it, since it clears the page.
        if self._text_mode:
            self._end_text_mode()
            if ev.detail == CLEAR_BUTTON:
                self.bridge.text_cancel.emit()
            else:
                self.bridge.text_commit.emit()

        if ev.detail == DRAW_BUTTON:
            # A press inside the (painted, click-through) toolbar is a button
            # click, not the start of a stroke. Route it to the Qt thread.
            if self._in_toolbar(ev.root_x, ev.root_y):
                dbg(f"toolbar press at ({ev.root_x},{ev.root_y})")
                self.bridge.toolbar_press.emit(ev.root_x, ev.root_y)
                return
            # Text tool: grab the keyboard so typed characters route to us
            # instead of the document, then place an insertion caret here.
            if getattr(self.bridge, "current_tool", None) == "text":
                try:
                    reply = self.root.grab_keyboard(
                        True, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime,
                    )
                    ok = getattr(reply, "status", X.GrabSuccess) == X.GrabSuccess
                except Exception as exc:      # pragma: no cover - defensive
                    dbg(f"grab_keyboard failed: {exc}")
                    ok = False
                if not ok:
                    dbg("keyboard grab unavailable; text tool inert")
                    return
                dbg(f"text begin at ({ev.root_x},{ev.root_y})")
                self._text_mode = True
                self.bridge.text_begin.emit(ev.root_x, ev.root_y)
                return
            self._drawing = True
            self.bridge.stroke_begin.emit(ev.root_x, ev.root_y)
        elif ev.detail == CLEAR_BUTTON:
            self.bridge.clear_canvas.emit()

    def _end_text_mode(self):
        """Release the keyboard grab and leave text-entry mode (best-effort)."""
        try:
            self.display.ungrab_keyboard(X.CurrentTime)
        except Exception:
            pass
        self._text_mode = False

    def _in_toolbar(self, x, y):
        """Geometry-only test against the toolbar bounds published by the Qt
        thread (a plain (x, y, w, h) tuple — safe to read from here)."""
        rect = getattr(self.bridge, "toolbar_rect", None)
        if not rect:
            return False
        rx, ry, rw, rh = rect
        return rx <= x < rx + rw and ry <= y < ry + rh

    def _on_key(self, ev, actions):
        # Prefer a modifier-qualified match (Ctrl+Z etc.); fall back to the bare
        # control keys, which carry no Ctrl/Shift and so never collide.
        mods = ev.state & (X.ControlMask | X.ShiftMask)
        action = self._mod_actions.get((ev.detail, mods))
        if action is None:
            action = actions.get(ev.detail)
        dbg(f"KeyPress keycode={ev.detail} mods={mods} -> action={action}")
        if action is not None:
            emit_action(self.bridge, action)

    def _on_text_key(self, ev):
        """Decode a key while a text box is being edited (keyboard grabbed) and
        emit the matching text signal."""
        shift = bool(ev.state & X.ShiftMask)
        caps = bool(ev.state & X.LockMask)
        keysym = self.display.keycode_to_keysym(ev.detail, 1 if shift else 0)

        if keysym in (XK.XK_Escape,):
            self._end_text_mode()
            self.bridge.text_cancel.emit()
            return
        if keysym in (XK.XK_Return, XK.XK_KP_Enter):
            if shift:
                self.bridge.text_char.emit("\n")
            else:
                self._end_text_mode()
                self.bridge.text_commit.emit()
            return
        if keysym in (XK.XK_BackSpace,):
            self.bridge.text_backspace.emit()
            return
        if keysym in (XK.XK_Tab,):
            self.bridge.text_char.emit("\t")
            return
        if keysym in (XK.XK_space, XK.XK_KP_Space):
            self.bridge.text_char.emit(" ")
            return

        # Printable Latin-1 characters; ignore modifiers and unmapped keysyms.
        ch = XK.keysym_to_string(keysym)
        if not ch or not ch.isprintable():
            return
        if caps and ch.isalpha():
            ch = ch.swapcase()
        self.bridge.text_char.emit(ch)

    def _on_raw_button(self, ev):
        data = getattr(ev, "data", None)
        if not isinstance(data, (bytes, bytearray)):
            return
        if len(data) < self._RAW_DETAIL_OFFSET + 4:
            return
        button = struct.unpack_from("<I", data, self._RAW_DETAIL_OFFSET)[0]
        if button in SCROLL_BUTTONS:
            dbg(f"raw scroll button={button} -> clear")
            # A scroll wipes the page; discard any in-progress text box too so we
            # never leave the keyboard grabbed with nothing to type into.
            if self._text_mode:
                self._end_text_mode()
                self.bridge.text_cancel.emit()
            self.bridge.clear_canvas.emit()

    def stop(self):
        self._running = False


class HotkeyThread(threading.Thread, InputSource):
    """Always-on global toggle hotkey. Passively grabs the chord on the root
    window on a dedicated X connection and emits toggle_overlay on each press —
    independent of the drawing InputThread, so it fires whether the overlay is
    up or down."""

    def __init__(self, bridge, chord):
        super().__init__(daemon=True)
        self.bridge = bridge
        self._chord = chord          # (key_name, frozenset(mods))
        self._running = True
        self.display = None
        self.root = None
        self._grabs = []             # (keycode, modifier) pairs to ungrab on exit

    def run(self):
        key, mods = self._chord
        self.display = display.Display()
        self.root = self.display.screen().root
        keysym = XK.string_to_keysym(key)
        if not keysym and len(key) == 1:
            keysym = XK.string_to_keysym(key.upper())
        keycode = self.display.keysym_to_keycode(keysym) if keysym else 0
        if not keycode:
            dbg(f"hotkey: could not resolve key {key!r}")
            return
        basemod = _hotkey_modmask(mods)
        try:
            # Grab under every lock combo so CapsLock / NumLock don't defeat it.
            for lock in LOCK_COMBOS:
                self.root.grab_key(
                    keycode, basemod | lock, True,
                    X.GrabModeAsync, X.GrabModeAsync,
                )
                self._grabs.append((keycode, basemod | lock))
            self.display.sync()
        except Exception as exc:
            # Most likely another app already owns this chord (BadAccess).
            dbg(f"hotkey grab failed (chord in use?): {exc}")
            return
        dbg(f"registered global hotkey keycode={keycode} mods={basemod}")

        while self._running:
            if self.display.pending_events() == 0:
                try:
                    ready, _, _ = select.select([self.display], [], [], 0.1)
                except Exception:
                    break
                if not ready:
                    continue
            try:
                ev = self.display.next_event()
            except Exception:
                break
            if ev.type == X.KeyPress:
                self.bridge.toggle_overlay.emit()

        for keycode, mod in self._grabs:
            try:
                self.root.ungrab_key(keycode, mod)
            except Exception:
                pass

    def stop(self):
        self._running = False


# --------------------------------------------------------------------------- #
# Window / cursor / clipboard helpers
# --------------------------------------------------------------------------- #

def _make_click_through(window_id: int):
    """Set an empty input shape so all pointer events fall through to the
    document underneath. Grabs still intercept what we want; everything else
    (notably scroll) reaches the real window below."""
    d = display.Display()
    try:
        win = d.create_resource_object("window", window_id)
        win.shape_rectangles(
            operation=shape.SO.Set,
            destination_kind=shape.SK.Input,
            ordering=0,
            x_offset=0,
            y_offset=0,
            rectangles=[],          # empty region == fully click-through
        )
        d.sync()
    finally:
        d.close()


def _hide_real_cursor():
    """Hide the real X cursor globally (XFixes) so the overlay can draw its own
    pencil. Returns the Display holding the hide — keep it open; closing it, or
    the process exiting, restores the cursor. Returns None if XFixes is
    unavailable (caller then uses the grab-cursor fallback)."""
    try:
        d = display.Display()
        d.xfixes_query_version()
        d.screen().root.xfixes_hide_cursor()
        d.sync()
        dbg("real cursor hidden via XFixes; self-drawing marker")
        return d
    except Exception as exc:
        dbg(f"XFixes cursor-hide unavailable ({exc}); using grab cursor")
        return None


class X11Backend(PlatformBackend):
    def __init__(self):
        self._cursor_display = None

    def apply_click_through(self, window):
        _make_click_through(int(window.winId()))

    def hide_cursor(self):
        self._cursor_display = _hide_real_cursor()
        return self._cursor_display is not None

    def show_cursor(self):
        d = self._cursor_display
        if d is None:
            return
        try:
            d.screen().root.xfixes_show_cursor()
            d.sync()
            # Close the connection (the overlay may be hidden/shown many times):
            # a fresh hide re-opens one, so leaving these open would leak fds.
            d.close()
        except Exception:
            pass
        self._cursor_display = None

    def make_input_source(self, bridge, self_draw_cursor):
        return InputThread(bridge, use_grab_cursor=not self_draw_cursor)

    def make_hotkey_listener(self, bridge, chord):
        return HotkeyThread(bridge, chord)

    def copy_pixmap_to_clipboard(self, pixmap):
        """Prefer xclip (owns the X selection as its own process, so the image
        survives after the overlay quits). Fall back to the Qt clipboard when
        xclip isn't installed."""
        from PyQt6.QtCore import QBuffer, QByteArray
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        pixmap.save(buf, "PNG")
        buf.close()
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png"],
                input=bytes(ba), check=False,
            )
            return
        except FileNotFoundError:
            dbg("xclip not found; using Qt clipboard (may not persist on quit)")
        from PyQt6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setPixmap(pixmap)
