# Cross-platform port + open-source launch

## Context

`screen-annotator` is a live, click-through pen overlay: you draw on top of the *live*
screen while studying, and scrolling both scrolls the document and wipes the ink for a
fresh canvas. Today it is **Linux / X11 / XFCE only** — the input layer (~1,500 lines in
`overlay_annotator.py`) is wired directly to X11 (SHAPE, XInput2, XFixes, X grabs), and
setup is hardcoded to one machine (a `ds` conda env, absolute home paths). There is no
LICENSE.

The goal is to advertise it on LinkedIn as an open-source tool people can actually
install and use. To make the cross-platform claim real we will:

1. **Port to Windows** and keep Linux/X11 working.
2. **Restructure into a pip-installable package** with a `screen-annotator` console
   command, removing all hardcoded paths.
3. **Add an MIT LICENSE** and a **clean, simple README** that credits Satty as
   inspiration (dropping the "Why not Satty?" comparison section).
4. **Draft a LinkedIn post.**
5. **macOS** is deferred to a documented roadmap — see the full breakdown at the end of
   this doc for exactly what it would take.

The render layer (`OverlayWindow` painting, toolbar, text tool, undo/redo, and
`SignalBridge`) is already portable Qt and stays shared. Only four things are X11-bound
and get a platform abstraction: input capture, click-through, cursor hiding, and the
screenshot pipeline.

## Target package layout

Move `overlay_annotator.py` into a package (keeps the render layer intact, splits out
the platform code):

```
screen_annotator/
  __init__.py
  __main__.py          # enables `python -m screen_annotator`
  app.py               # main(): backend selection + wiring (from current main())
  config.py            # PALETTE, constants, load_prefs/save_prefs
  bridge.py            # SignalBridge (unchanged)
  render.py            # OverlayWindow — platform-agnostic Qt render layer
  backends/
    __init__.py        # get_backend() factory
    base.py            # PlatformBackend ABC + InputSource base
    x11.py             # X11 backend (current InputThread + shape + xfixes)
    windows.py         # Windows backend (new)
    macos.py           # roadmap stub: raises with a clear "not yet supported" message
pyproject.toml         # metadata, deps, console entry point
README.md              # rewritten
LICENSE                # MIT
```

Remove `annotate.sh` (legacy Satty freeze-and-mark flow). Replace the hardcoded
`overlay.sh` with a small, path-independent Linux toggle helper that calls the installed
`screen-annotator` command (`pkill` if already running, else launch).

## The platform abstraction (`backends/base.py`)

Extract a small interface; the render layer talks only to this, never to X11/Win APIs.

```python
class PlatformBackend(ABC):
    def apply_click_through(self, window) -> None: ...   # make the Qt window pass input through
    def hide_cursor(self) -> bool: ...                   # True => app self-draws marker; False => native/none
    def show_cursor(self) -> None: ...
    def make_input_source(self, bridge, self_draw_cursor) -> InputSource: ...  # background capture -> bridge signals

class InputSource(ABC):
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

`get_backend()` (in `backends/__init__.py`) selects by platform:
- `linux` + X11 (`XDG_SESSION_TYPE != wayland` and `DISPLAY` set) -> `X11Backend`
- `linux` + Wayland / no DISPLAY -> exit with a clear "X11 session required" message
- `win32` -> `WindowsBackend`
- `darwin` -> exit with "macOS not yet supported — see roadmap / contributions welcome"

Screenshot-to-clipboard becomes **shared and Qt-native** (no backend method): rewrite
`OverlayWindow.on_copy` to use `QScreen.grabWindow(0)` + `QGuiApplication.clipboard().setPixmap()`,
keeping the existing `_suppress_ui` dance so the marker/toast/toolbar aren't baked in.
This removes the `flameshot`/`xclip` dependency on every platform.

## X11 backend (`backends/x11.py`) — verified on this machine

Relocate, mostly verbatim, behind the interface:
- `InputThread` (current class) becomes the X11 `InputSource`.
- `make_click_through()` -> `X11Backend.apply_click_through()`.
- `_hide_real_cursor()` / `_show_real_cursor()` + `_make_marker_cursor()` -> the
  backend's `hide_cursor`/`show_cursor` (self-draw when XFixes works, grab-cursor
  fallback otherwise). `python-xlib` becomes a Linux-only dependency.

Behavior must be unchanged from `overlay_annotator.py` (InputThread `:181`,
make_click_through `:1319`, cursor hide/show `:1349`). This is the path that can be run
and verified end-to-end on Linux.

## Windows backend (`backends/windows.py`) — new, best-effort, needs on-device testing

Pure `ctypes` (stdlib — no extra pip dependency):

- **Click-through** (`apply_click_through`): on the Qt HWND (`int(window.winId())`), OR
  the extended style with `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW |
  WS_EX_NOACTIVATE` via `GetWindowLongW`/`SetWindowLongW`. `WS_EX_TRANSPARENT` is the
  Windows analogue of the empty X11 input shape — mouse falls through to the document.

- **Input capture** (`InputSource`): a thread that installs `WH_MOUSE_LL` and
  `WH_KEYBOARD_LL` via `SetWindowsHookEx` and runs a `GetMessage` pump. Callbacks
  (`ctypes.CFUNCTYPE`) mirror the X11 per-button logic and emit the *same* bridge
  signals, so the render layer is untouched:
  - Left button down/move/up -> toolbar hit-test / text-begin / stroke begin-point-end.
    Consume (return 1) while a stroke is active so the document gets no stray selection
    (mirrors the X11 Button-1 grab).
  - Right button -> clear; consume.
  - Wheel (`WM_MOUSEWHEEL`/`WM_MOUSEHWHEEL`) -> emit `clear_canvas`, **never consume**
    (document scrolls) — mirrors the X11 raw-scroll observation.
  - Keyboard (`WM_KEYDOWN`/`WM_SYSKEYDOWN`): map VK codes + `GetAsyncKeyState` Ctrl/Shift
    to the existing `CONTROL_KEYS`/`MOD_SHORTCUTS` actions; consume matched keys, pass
    the rest (so Ctrl+C/V reach the document, matching X11). Text mode: consume keys and
    translate via `ToUnicode` into `text_char`/`text_backspace`/commit/cancel signals.

- **Cursor**: replace the system arrow with a native pencil via `SetSystemCursor`
  (a real hardware cursor — no self-draw needed), restored on exit with
  `SystemParametersInfo(SPI_SETCURSORS)`. `hide_cursor()` returns `False`. If flaky on a
  real machine, fall back to no custom cursor.

- **Global hotkey**: Windows has no built-in "bind any command to a key" like XFCE. The
  README documents launching via a desktop shortcut with an assigned shortcut key, or a
  tiny AutoHotkey snippet, for the Super+A-equivalent toggle.

## Packaging (`pyproject.toml`)

```toml
[project]
name = "screen-annotator"
requires-python = ">=3.9"
dependencies = [
  "PyQt6>=6.6",
  "python-xlib>=0.33; sys_platform == 'linux'",
]
[project.scripts]
screen-annotator = "screen_annotator.app:main"
```

Install everywhere with `pip install .` (or `pipx install .`). Replaces the conda `ds`
env and every hardcoded path.

## README rewrite (clean + simple)

- One-line hook + short "what it is" + a demo GIF placeholder.
- **Install**: `pip install .` — same on Linux & Windows; system-tool section removed
  (Qt-native capture). Platform-support table: Linux/X11 ✅, Windows ✅,
  Wayland/macOS = roadmap.
- **Usage / Controls**: keep the existing controls tables, add a Windows launch note
  (shortcut/AutoHotkey) alongside the XFCE Super+A binding.
- **How it works**: condensed two-layer explanation, framed as "shared Qt render layer +
  per-OS input backend."
- **Prior art / inspiration**: short note crediting
  [Satty](https://github.com/gabm/Satty). **Remove "Why not Satty?"** and all
  `annotate.sh` references.
- **Roadmap**: macOS + Wayland, "contributions welcome," pointing to the macOS section
  below.
- **License**: MIT.

## LICENSE

Add a standard **MIT LICENSE**, `Copyright (c) 2026 Ahmed Hanif`, plus an SPDX
`# SPDX-License-Identifier: MIT` header on the package's main modules.

## LinkedIn post

Draft in `LINKEDIN.md`: short, punchy — the problem (marking up live docs while studying
without freezing the screen), the one-liner demo, "open source, MIT, Linux + Windows," a
repo link, and a tasteful ask for contributors (macOS backend). **Hold the public
"Windows" claim until the Windows backend is verified on a real Windows machine.**

## Files to create / modify

- **Create**: `screen_annotator/` package (`__init__.py`, `__main__.py`, `app.py`,
  `config.py`, `bridge.py`, `render.py`, `backends/{__init__,base,x11,windows,macos}.py`),
  `pyproject.toml`, `LICENSE`, `LINKEDIN.md`.
- **Rewrite**: `README.md`; `overlay.sh` (path-independent Linux toggle helper).
- **Delete**: `overlay_annotator.py` (content migrated), `annotate.sh`.
- **Untouched**: `implementation_details/`, `iterations/`, the gitignored `Satty/` clone.

## Verification

**Linux (runnable here):**
1. `pip install -e .` in a fresh venv; run `screen-annotator`.
2. Draw (pen/highlighter/eraser); scroll to confirm the document scrolls **and** the
   canvas clears; tool/colour/size via keys and toolbar; text tool; undo/redo; pin mode +
   Shift+C; `Enter` copy (confirm the new Qt-native capture puts document+ink on the
   clipboard and excludes marker/toast); `Esc` quit restores normal interaction and the
   real cursor.
3. Confirm `flameshot`/`xclip` are no longer needed.

**Windows (requires a real Windows box — cannot be run from Linux):** manual checklist —
install via pip; window is click-through (can still click/scroll the document
underneath); left-drag draws; scroll clears; keys and toolbar work; `Enter` copies;
pencil cursor shows and is restored on exit; no keys stolen from other apps except our
controls. Expect on-device iteration on the LL-hook and `SetSystemCursor` code.

**Order of work:** (1) refactor into the package with the X11 backend, verify Linux is
unchanged; (2) add Qt-native capture, verify; (3) add the Windows backend; (4) README +
LICENSE + LinkedIn; (5) user verifies Windows and we iterate.

---

# macOS support — what it would take (roadmap)

macOS is achievable but is the highest-friction platform: it needs **two OS permissions**,
cursor-hiding is unreliable, there is **no built-in global hotkey binding**, and real
distribution requires **code-signing + notarization**. It also can only be developed and
tested on Apple hardware. This section is the starting spec for a contributor building
`backends/macos.py`.

### Dependencies
- `pyobjc-framework-Quartz` — `CGEventTap*`, `CGDisplayHideCursor`, `CGEvent*`.
- `pyobjc-framework-Cocoa` — `NSWindow`, `NSCursor`, `NSEvent`.
- Add to `pyproject.toml` under a macOS marker:
  `"pyobjc-framework-Quartz>=10; sys_platform == 'darwin'"` (and Cocoa likewise).

### 1. Click-through window (`apply_click_through`)
Unlike X11 (where Qt's own mouse-transparency fights our input shape), on macOS Qt's
`Qt.WindowType.WindowTransparentForInput` works correctly — it sets
`NSWindow.ignoresMouseEvents = True`, so events fall through to the document. Set it on
the window flags, or reach the `NSWindow` directly via pyobjc from `int(window.winId())`
(an `NSView*`) → `.window()` → `setIgnoresMouseEvents_(True)`. Also set a high window
level (floating/status level) and `collectionBehavior` to
`CanJoinAllSpaces | Stationary` so the overlay shows over every Space and doesn't move
with them.

### 2. Global input capture — `CGEventTap` (the crux)
- Create a session tap:
  `CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault, mask, callback, refcon)`.
- Mask: `leftMouseDown | leftMouseDragged | leftMouseUp | rightMouseDown | scrollWheel |
  keyDown | flagsChanged`.
- **Accessibility permission required.** Without it `CGEventTapCreate` returns NULL.
  Detect up front with `AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})`
  and show the user the Settings → Privacy & Security → Accessibility path.
- Run the tap on its own thread with a CFRunLoop: build a run-loop source
  (`CFMachPortCreateRunLoopSource`), `CFRunLoopAddSource`, then `CFRunLoopRun()` — the
  macOS analogue of the X11 `InputThread`. Bridge to Qt via the **same** `pyqtSignal`s as
  X11/Windows, so `render.py` is untouched.
- Callback contract: **return the event** to pass it through, **return NULL** to consume.
  - Left drag → emit stroke begin/point/end; consume (no stray selection).
  - Right mouse down → emit `clear_canvas`; consume.
  - Scroll wheel → emit `clear_canvas`; **return the event** so the document scrolls.
  - `keyDown` → map keycode + flags to actions; consume matched control keys, pass the
    rest. Text mode: consume and pull characters via
    `CGEventKeyboardGetUnicodeString` into `text_char`/`text_backspace`/commit/cancel.
- Robustness: the OS may disable the tap under load
  (`kCGEventTapDisabledByTimeout`/`...ByUserInput`) — handle those event types by
  re-enabling with `CGEventTapEnable(tap, True)`.

### 3. Cursor
`CGDisplayHideCursor(kCGDirectMainDisplay)` / `NSCursor.hide()` **only reliably hide the
cursor while the calling app is frontmost/active**. A background, click-through overlay is
not the active app, so global hide is unreliable. Practical path: reuse the X11-style
**self-drawn marker** (paint the pencil on the overlay at ~60 Hz, tracking
`QCursor.pos()`) and let `hide_cursor()` return `True` to enable it — accepting that the
real arrow may still show underneath. This needs real-device evaluation; it may be the
least-polished part of the macOS experience.

### 4. Screenshot to clipboard
`QScreen.grabWindow(0)` (the shared Qt-native path) requires **Screen Recording
permission** (Settings → Privacy & Security → Screen Recording) on macOS 10.15+.
Alternative: shell out to `screencapture -x -c` (silent capture straight to the
clipboard), which needs the same permission. Detect the permission and guide the user if
it's missing.

### 5. Global hotkey (the Super+A equivalent)
macOS has no per-command global hotkey in System Settings. Options, roughly in order of
practicality:
- Document a launcher binding — **Raycast/Alfred hotkey**, an **Automator Quick Action /
  Shortcuts** bound to a key, or **skhd** (`cmd - a : screen-annotator`). Lowest effort,
  no extra code.
- `NSEvent.addGlobalMonitorForEventsMatchingMask` — can *observe* a hotkey globally but
  **cannot consume** it; fine for a launch toggle.
- Carbon `RegisterEventHotKey` — works and can consume, but Carbon is legacy.

### 6. Packaging, signing & notarization
For `pip`/dev use, both permissions attach to the *terminal or Python binary* that runs
the tap — confusing, because granting them to "Terminal" is non-obvious. Real
distribution means a proper **`.app` bundle** (py2app or briefcase), **code-signed** with
a Developer ID and **notarized** so Gatekeeper allows it, with the permissions granted to
the bundle itself. This is meaningful extra work beyond the backend code.

### 7. Testing
Needs physical Apple hardware (macOS VMs are legally limited to Apple machines). Both
permissions must be granted by hand, and the cursor + permission-prompt UX need
on-device iteration. This is why macOS is a roadmap item rather than part of the first
cross-platform release.

### Effort summary
Medium code volume (a few hundred lines for the event tap + window plumbing), but **high
friction**: two permissions, an unreliable cursor, no native global hotkey, and
signing/notarization for distribution. The `PlatformBackend` interface leaves a clean
slot for `macos.py`, so a contributor can build it without touching the shared render
layer.
