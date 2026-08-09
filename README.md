# screen-annotator

[![CI](https://github.com/ahmedhanifc/screen-annotator/actions/workflows/ci.yml/badge.svg)](https://github.com/ahmedhanifc/screen-annotator/actions/workflows/ci.yml)

A live, **click-through pen overlay** for your whole screen. Draw over whatever is
on screen — slides, a PDF, a webpage, code — and when you **scroll, the document
scrolls _and_ your ink wipes instantly**, giving a fresh canvas with no hotkey and no
interruption.

Unlike "freeze the screenshot and mark it up" tools, the overlay sits on top of the
**live** screen. The document underneath stays fully interactive and scrollable; you
draw on the glass.

```
 read → annotate → scroll (canvas clears) → keep reading → repeat
                        └─ press Enter any time to copy the current frame ─┘
```

**Open source (MIT). Runs on Linux (X11) and Windows.**

---

## Features

- **Transparent, always-on-top overlay** over the live desktop — no frozen screenshot
  in the way. The document below stays clickable and scrollable.
- **Scroll-to-clear**: one gesture both scrolls the document and wipes the annotations,
  so every page starts blank. Works with a mouse wheel **and** touchpad two-finger scroll.
- **Pen, highlighter, eraser, and text**: a 10-colour palette, adjustable width, a
  translucent highlighter, an eraser that wipes ink (never the screen), and click-to-place
  text.
- **Pin mode**: route any tool's ink to a permanent layer that survives scroll / clear.
  Cleared deliberately with `Shift+C`.
- **Undo / redo** (`Ctrl+Z` / `Ctrl+Shift+Z` / `Ctrl+Y`).
- **Painted toolbar** (top-center): click to switch tool, pick a colour, change size, or
  clear / copy / quit — no need to memorise the keys. It's never baked into copied
  screenshots.
- **Copy to clipboard** (`Enter`): grabs the composited screen + ink as a PNG.
- Truly non-intrusive: while active it only intercepts the drawing controls; `Ctrl+C`,
  `Ctrl+V`, etc. are left alone, and scroll is never swallowed.

---

## Install

Requires **Python 3.9+**. Install straight from the repo:

```bash
git clone https://github.com/ahmedhanifc/screen-annotator.git
cd screen-annotator
pip install .
```

This installs a `screen-annotator` command. That's it — no external screenshot tools
needed (capture and clipboard are handled internally).

> Prefer an isolated install? `pipx install .` works too.

### Platform support

| Platform | Status |
|---|---|
| **Linux — X11** (X.Org session) | ✅ Supported |
| **Windows 10 / 11** | ✅ Supported |


On Linux you need an **X11 (X.Org)** session, not Wayland — most desktops offer an
"Xorg"/"X11" option at the login screen. A compositor must be running for transparency
(GNOME/KDE/most desktops have one by default; on XFCE enable *Settings → Window Manager
Tweaks → Compositor*).

---

## Run

Launch the overlay:

```bash
screen-annotator
```

Press `Esc` (or the toolbar ✕) to quit and return to normal interaction.

### Bind it to a hotkey

The overlay is nicest as a toggle on a global hotkey.

**Linux (any desktop):** point a custom keyboard shortcut at `overlay.sh` in this repo —
it launches the overlay, or quits it if it's already running. For example, on XFCE:

```bash
xfconf-query -c xfce4-keyboard-shortcuts -p "/commands/custom/<Super>a" \
  -s "/full/path/to/screen-annotator/overlay.sh"
```

(GNOME/KDE: add a custom shortcut in Settings pointing to the same `overlay.sh`.)

**Windows:** Windows has no built-in "bind a command to a key", so use a tiny
[AutoHotkey](https://www.autohotkey.com/) script:

```ahk
; Win+A launches the overlay (Esc inside the overlay quits it).
#a::Run, screen-annotator
```

Or create a desktop shortcut to `screen-annotator` and assign it a shortcut key in the
shortcut's properties.

---

## Controls

| Input | Action |
|---|---|
| **Left-drag** | Draw with the active tool (pen / highlighter / eraser) |
| **Toolbar** (top center) | Click to pick tool, colour, size, or toggle pin / clear / copy / quit |
| **Scroll** (wheel/touchpad) | Document scrolls **and** the canvas clears |
| **Enter** | Copy the current screen + ink to the clipboard |
| **Esc** | Quit the overlay (return to normal interaction) |
| `[` / `]` | Decrease / increase pen size (also scales text) |
| `0`–`9` | Pick pen colour (red, orange, yellow, green, cyan, blue, violet, white, black, grey) |
| `h` | Toggle highlighter tool |
| `e` | Toggle eraser tool |
| `t` | Toggle text tool, then click to place a text box and type |
| `p` | Toggle **pin mode** — any tool then draws on a permanent layer that survives clears |
| **Ctrl+Z** | Undo the last stroke |
| **Ctrl+Shift+Z** / **Ctrl+Y** | Redo |
| `c` / **right-click** | Clear the canvas manually (pinned ink survives) |
| **Shift+C** | Clear the pinned (permanent) layer |

### Text tool

Pick the **T** in the toolbar (or press `t`), click where you want the text, and start
typing — the size follows the current pen size and colour.

| Input | Action |
|---|---|
| **Enter** | Commit the text onto the canvas (one undo step) |
| **Shift+Enter** | Insert a newline |
| **Backspace** | Delete the last character |
| **Esc** | Cancel the text box (nothing is baked) |
| Any click | Commits the current text, then acts on the click |

> **Drawing mode is modal.** While the overlay is up it grabs the drawing controls
> (the keys above and the left/right mouse buttons) globally, and while a text box is
> open it grabs the keyboard so you can type. Press `Esc` to exit and interact with the
> document normally. Scroll is never grabbed — it always reaches the document.

---

## How it works

Two layers run in one process:

- **Render layer (PyQt6)** — a fullscreen, frameless, always-on-top, translucent window
  that only *paints* ink onto screen-sized pixmaps. It's shared across every platform.
- **Input backend (per OS)** — because the window is click-through, the toolkit never
  sees the input, so a background layer captures it globally and decides, per button,
  what to consume versus pass through. Scroll is only *observed* (never consumed), so the
  document keeps scrolling while its arrival triggers a canvas wipe.

The click-through trick is an **empty X11 input shape** on Linux and the
**`WS_EX_TRANSPARENT`** layered-window style on Windows. Because the ink is a real
on-screen window, a normal screen grab already captures *document + ink composited
together*, which is exactly what `Enter` copies to the clipboard.

New platforms slot in behind a small `PlatformBackend` interface
(`screen_annotator/backends/`) without touching the shared render layer — see
[`implementation_details/`](implementation_details/) for the design and a starting spec
for the macOS backend.

---

## Prior art

Inspired by [**Satty**](https://github.com/gabm/Satty), a lovely screenshot-annotation
tool. screen-annotator takes a different angle — annotating the *live* screen with
scroll-to-clear, rather than freezing a screenshot first — but Satty is well worth a look
if you want the freeze-and-mark workflow.

---

## Roadmap / contributing

- **Wayland** (Linux) and **macOS** backends — the `PlatformBackend` interface is ready
  for them; `implementation_details/cross_platform_port.md` documents what each needs.
- A **native pencil cursor** on Windows.

Contributions and issues welcome. No Windows machine? You can still test the Windows
build — see [`implementation_details/testing_windows.md`](implementation_details/testing_windows.md)
for running it in a free local VM (and note that CI already exercises the Windows build on
every push).

## License

[MIT](LICENSE) © Ahmed Hanif
