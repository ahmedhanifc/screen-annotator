# Implementation details — live overlay annotator with scroll-to-clear

## 1. Context / why

Goal: a "read → annotate → scroll → fresh canvas" study tool. While reading a
book/PDF, draw over the screen with a pen; when you scroll, the document scrolls
**and** the annotations wipe instantly so you get a blank canvas — no hotkey,
no interruption.

We trialed **Satty**, but it can't do this. Satty freezes a screenshot and puts
a *solid* full-screen window on top that swallows all input, so the document
underneath can't scroll and there's no "scroll for a new canvas." The correct
model is a **transparent overlay over the live screen**: you draw on the glass,
the real document is still live and scrollable underneath.

### Confirmed design decisions
- **Clipboard**: on-screen drawing by default; press **Enter** to copy the
  current screen + annotations to the clipboard when you want to keep a frame.
- **Draw vs click**: left-drag always draws; press **Esc** to turn the overlay
  off when you need to click/interact with the document normally.
- **Scroll source**: must work for both mouse wheel and touchpad two-finger
  scroll (handled automatically — see §3).

### Environment (verified)
- X11 (`DISPLAY=:0.0`), XFCE desktop, Xubuntu.
- `xinput`, `xev` present. `flameshot` + `xclip` installed and working (from the
  Satty setup).
- Python = miniconda 3.13 at `/home/ahmed-hanif/miniconda3/bin/python3`.
- **Not yet installed**: `PyQt6`, `python-xlib`.

---

## 2. Architecture overview

Two layers, running in one process:

1. **Render layer (PyQt6)** — a fullscreen, transparent, frameless,
   always-on-top window that only *draws* the ink. It is made **click-through**
   (empty X11 input shape) so it never steals pointer/keyboard from the
   document underneath.
2. **Input layer (python-xlib + XInput2)** — runs the X event loop in a
   background thread and decides, *per button*, what to capture vs pass through.
   It emits Qt signals to the render layer to add points / clear / quit.

Because the overlay is click-through, we can't rely on Qt to receive mouse/key
events — all input is captured at the X11 layer instead. Root-window
coordinates map 1:1 to the overlay (overlay is fullscreen at 0,0).

```
 X server ──raw events──► [Xlib thread]
                             │  Button1 press/motion/release  → signal: draw
                             │  Button4/5/6/7 (scroll, observed)→ signal: clear (event still passes to doc)
                             │  Enter / Esc / [ ] / 0-9 / h / c → signal: copy / quit / size / color / clear
                             ▼
                        pyqtSignal → [Qt main thread] paints ink on transparent overlay
```

---

## 3. The core trick (per-button selectivity)

The whole feature hinges on treating the left button and the scroll differently:

- **Left button (draw)** — `XGrabButton` a **passive grab of Button 1 only** on
  the root window, `owner_events=False`, `pointer_mode=Async`. When Button 1 is
  pressed we receive press → motion → release and draw from them; crucially the
  event does **not** propagate to the document (no accidental text selection or
  page-turn while drawing).
- **Scroll (clear + pass through)** — we do **not** grab scroll. We *observe* it
  via **XInput2 raw button events** (`Xlib.ext.xinput`, select `RawButtonPress`
  for buttons 4/5/6/7 on the root). Raw/observed events don't consume the
  event, so the document still scrolls normally; we just use it as the trigger
  to wipe the canvas.
- **Mouse vs touchpad is automatic** — on X11 the server emulates the legacy
  button-4/5/6/7 scroll events from a touchpad's smooth-scroll valuators, so a
  single detection path catches both a mouse wheel and a two-finger touchpad
  scroll. (Confirmed against the X Input 2 protocol spec.)
- **Keys** — `XGrabKey` for Enter, Esc, `[`, `]`, `0`–`9`, `h`, `c` (see §5).

---

## 4. Clipboard capture (Enter)

Because the ink is a **real on-screen window** (not an internal buffer), a normal
screenshot already captures *document + ink composited together*. So Enter just
reuses the pipeline already proven in `annotate.sh`:

```
flameshot full --raw | xclip -selection clipboard -t image/png
```

No manual compositing needed. (Optional refinement: briefly hide our own UI
hints, if any, before the shot — not needed for a plain pen overlay.)

---

## 5. Controls

| Input                 | Action                                             |
|-----------------------|----------------------------------------------------|
| Left-drag             | Draw with the active tool (pen/highlighter/eraser) |
| Toolbar (top center)  | Click to pick tool, colour, size, or clear/copy/quit |
| Scroll (wheel/touchpad)| Document scrolls **and** canvas clears            |
| **Enter**             | Copy current screen + ink to clipboard             |
| **Esc**               | Quit the overlay (return to normal interaction)    |
| `[` / `]`             | Decrease / increase pen size (also scales text)    |
| `0`–`9`               | Pick pen color (small preset palette)              |
| `h`                   | Toggle highlighter tool (thick, translucent stroke)|
| `e`                   | Toggle eraser tool (wipes ink, not the screen)     |
| `t`                   | Toggle text tool (click to place, then type)       |
| **Ctrl+Z**            | Undo the last stroke                               |
| **Ctrl+Shift+Z** / **Ctrl+Y** | Redo                                       |
| `c` or right-click    | Manually clear the canvas (resets undo history)    |
| **Super+A** (global)  | Launch the overlay; press again to quit (toggle)   |

Text-box keys (only while a box is open): `Enter` commits, `Shift+Enter` inserts
a newline, `Backspace` deletes, `Esc` cancels.

### Toolbar (added later)

The overlay is fully click-through, so the toolbar is **painted** into the
window (not real Qt widgets) and it survives canvas clears. The input thread
knows the toolbar's bounding rect (published on the `SignalBridge` as a plain
`(x, y, w, h)` tuple); a Button-1 press inside it is routed to a `toolbar_press`
signal instead of starting a stroke, and the Qt thread hit-tests the painted
buttons. The eraser strokes with `QPainter.CompositionMode_Clear`, which zeroes
the ink's alpha and never touches the live document underneath.

### Text tool (added later)

The window never holds keyboard focus (it has an empty X11 input shape), so
free-text entry can't use a `QLineEdit`. Instead the input thread owns a
short-lived **active keyboard grab**: selecting the text tool publishes
`current_tool = "text"` on the `SignalBridge`, and the next Button-1 press
(outside the toolbar) emits `text_begin(x, y)`, calls `grab_keyboard`, and enters
text mode. While grabbed, every key routes to `_on_text_key`, which emits
`text_char` / `text_backspace` / `text_commit` / `text_cancel`. The Qt thread
holds the pending buffer, draws it live in `paintEvent`, and on commit bakes it
onto the canvas pixmap with `QPainter.drawText` behind one `_push_undo()` (so a
text box undoes as a single step). Any button press, a scroll, or a right-click
ends the grab first — committing on a draw-button click, discarding otherwise —
so the grab and the buffer can never disagree. Text size derives from the pen
size (`width * TEXT_SIZE_FACTOR`).

---

## 6. Files to create / change

### NEW — `overlay_annotator.py`
The app. Structure:
- **Qt side**: `QApplication` + a `QWidget` overlay with
  `Qt.WindowType.FramelessWindowHint | WindowStaysOnTopHint | Tool`,
  `WA_TranslucentBackground`, fullscreen geometry. Apply an **empty X11 input
  shape** (via `python-xlib` SHAPE ext, or `WA_TransparentForMouseEvents` plus
  input-shape for keyboard) so it's fully click-through. `paintEvent` draws the
  accumulated strokes (list of polylines, each with color + width).
- **Xlib side** (background `threading.Thread`): open a second `Xlib.display`
  connection; `XGrabButton` Button 1 on root; set up `xinput.select_events`
  for `RawButtonPress` (scroll) on root; `XGrabKey` for the control keys; run
  the `next_event()` loop. Translate events into `pyqtSignal` emissions:
  `draw_point(x, y, pressed)`, `clear()`, `copy()`, `quit()`,
  `set_size(±)`, `set_color(i)`, `toggle_highlighter()`.
- **State**: current color, width, highlighter flag, current in-progress stroke.
- **Copy**: on `copy()` run the flameshot|xclip pipeline via `subprocess`.
- **Quit**: on `quit()` ungrab everything and `QApplication.quit()`.

### NEW — `overlay.sh`
Launcher + toggle. Mirrors the toggle pattern already in `annotate.sh:18-21`:
```bash
#!/usr/bin/env bash
set -euo pipefail
APP="/home/ahmed-hanif/Desktop/dev/applications/screen-annotator/overlay_annotator.py"
PY="/home/ahmed-hanif/miniconda3/bin/python3"
# Toggle: if it's already running, quit it.
if pgrep -f "overlay_annotator.py" >/dev/null 2>&1; then
    pkill -f "overlay_annotator.py"
    exit 0
fi
exec "$PY" "$APP"
```

### CHANGE — XFCE Super+A binding
Repoint the existing shortcut from `annotate.sh` to `overlay.sh`, then reload:
```bash
xfconf-query -c xfce4-keyboard-shortcuts -p "/commands/custom/<Super>a" -r
xfconf-query -c xfce4-keyboard-shortcuts -p "/commands/custom/<Super>a" -n -t string \
  -s "/home/ahmed-hanif/Desktop/dev/applications/screen-annotator/overlay.sh"
setsid xfsettingsd --replace >/dev/null 2>&1 &
```

### KEEP — `annotate.sh`
Leave the Satty frozen-screenshot flow as-is for the occasional "capture one
frame, mark it up carefully, copy" use case — just no longer the primary hotkey.

---

## 7. Dependencies / setup

```bash
/home/ahmed-hanif/miniconda3/bin/pip install PyQt6 python-xlib
```
- `flameshot`, `xclip` — already installed.
- **XFCE compositor must be ON** (Settings → Window Manager Tweaks →
  Compositor) — required for the overlay transparency to render. On by default
  in Xubuntu; verify. If off, the overlay shows as an opaque/black rectangle.

---

## 8. Risks / fallbacks

- **Button-1 root grab conflicts** with another grabber (rare): fall back to
  *observing* Button 1 via the same XInput2 raw path and drawing without a grab
  — downside is left-drag would then also reach the document.
- **Compositor off** → overlay opaque; enabling the XFCE compositor fixes it.
- **python-xlib XI2 quirks**: if raw scroll observation misbehaves, fall back to
  passively grabbing buttons 4/5/6/7 and replaying with
  `AllowEvents(ReplayPointer)` so the document still scrolls.
- **Thread ↔ Qt safety**: only ever touch Qt widgets from the main thread; the
  Xlib thread communicates exclusively via queued `pyqtSignal`s.

---

## 9. Verification (end-to-end)

1. `pip install PyQt6 python-xlib`; confirm both import cleanly.
2. Run `overlay.sh` directly. Open a PDF / book in another window.
3. **Draw**: left-drag → ink appears over the live document.
4. **Scroll — mouse wheel**: document scrolls **and** ink clears to blank.
5. **Scroll — touchpad two-finger**: same behavior (confirms emulation path).
6. **Enter**: paste (Ctrl+V) into an image app → shows document + ink composited.
7. **Esc**: overlay exits; left-click works normally in the document again.
8. `[` / `]` change pen size; `0`–`9` change color; `h` toggles highlighter;
   `c` / right-click clears.
9. **Super+A** launches it; **Super+A** again quits it (toggle).
