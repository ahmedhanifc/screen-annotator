# Packaging, build & release

Reference for how `screen-annotator` ships as an installable consumer app. This
is the "as-built" companion to `installable_app.md` (which is the phased plan the
work followed). If you're changing the tray/hotkey behaviour, the frozen build,
the installer, or the release pipeline — start here.

---

## What ships

A person installs one file and starts drawing — no Python, pip, AutoHotkey, or
manual keybinding. Concretely:

- A **background tray app**. Launching it shows no window; it sits in the system
  tray. A **built-in global hotkey** (`Ctrl+Alt+A` by default) shows/hides a
  click-through drawing overlay.
- A **Settings** dialog (tray → Settings…) to change that hotkey; it persists and
  re-registers live.
- A **Windows installer** (`screen-annotator-setup-<version>.exe`) built and
  attached to a GitHub Release automatically when a `v*` tag is pushed.

---

## Runtime architecture (the tray/hotkey model)

The app is no longer "launch = overlay, Esc = exit". It's a persistent process
whose overlay is shown/hidden on demand.

- **`app.py` → `AppController`** owns the lifecycle. `QApplication` runs with
  `setQuitOnLastWindowClosed(False)` and the overlay starts **hidden**.
  - **Show**: `reset_for_show()` (fresh canvas), `showFullScreen()`, re-apply
    click-through, hide the cursor + start the marker poll, and **start the
    drawing input source**.
  - **Hide** (`Esc` / toolbar ✕ → `bridge.do_quit`): **stop the drawing input
    source**, restore the cursor, `overlay.hide()`. The process stays alive.
  - **Quit** (tray → Quit / `SIGINT` / `SIGTERM` → `bridge.do_shutdown`): stop
    everything, `app.quit()`.
- **Why start/stop the drawing input on show/hide:** those global button/key
  grabs are modal and must not be held while the overlay is down (otherwise input
  wouldn't reach other apps). Only the *toggle hotkey* is always-on.
- **The toggle hotkey** is a separate always-on `InputSource` per backend
  (`make_hotkey_listener` in `backends/base.py`): Windows uses `RegisterHotKey` +
  a `WM_HOTKEY` message pump; X11 uses `XGrabKey` on the root window. It emits
  `bridge.toggle_overlay`.
- **Tray** (`tray.py`, `QSystemTrayIcon`): Toggle / Settings… / Quit, plus
  click-to-toggle. If no system tray is available, the hotkey still works.
- **Settings** (`settings.py`, `QDialog` + `QKeySequenceEdit`): edits only the
  hotkey (tool/colour/size already auto-persist as last-used). Valid chords need a
  modifier + a single letter/digit. Saved via `config.save_prefs(hotkey=...)`
  (merge-on-write) and applied through `AppController.set_hotkey`, which stops the
  old listener and starts a new one. First launch shows a one-time tray hint.

Signals added to `bridge.py`: `toggle_overlay`, `do_shutdown` (and `do_quit` now
means *hide*).

---

## Icon assets

The tray/window icon is painted in code (`icon.py::make_app_icon`) so the running
app needs no image file. The Windows exe/installer branding needs a real `.ico`:

- **`packaging/make_icon.py`** renders the same glyph at 7 sizes and writes
  `screen_annotator/assets/icon.ico` (+ `icon.png` for Linux). Committed outputs;
  only re-run when the glyph changes:
  ```bash
  QT_QPA_PLATFORM=offscreen python packaging/make_icon.py
  ```

---

## Building the standalone binary (PyInstaller)

- **Spec:** `packaging/screen-annotator.spec` — one-dir (faster startup; the
  installer hides the folder), windowed (`console=False`), icon applied on Windows.
- **Entry point:** `packaging/entry.py`, **not** `screen_annotator/__main__.py`.
  `__main__.py`'s `from .app import main` is a relative import that fails when run
  as the frozen top-level script (`attempted relative import with no known parent
  package`); `entry.py` uses an absolute import instead.
- **No cross-compile:** build the Windows exe on Windows, the Linux binary on
  Linux. Same spec, different runner.
- **Local build:**
  ```bash
  pip install ".[build]"          # installs pyinstaller
  pyinstaller packaging/screen-annotator.spec
  # -> dist/screen-annotator/screen-annotator[.exe]
  ```
  A Linux freeze is a useful smoke test of the spec even though the shipping
  target is Windows (it caught the entry-point bug above).

---

## Windows installer (Inno Setup)

`packaging/windows/installer.iss`:

- **Per-user install** into `%LOCALAPPDATA%` (`PrivilegesRequired=lowest`) — no
  UAC prompt, one less barrier for non-technical users.
- MIT licence page, Start-Menu entry, **optional** desktop shortcut, **optional**
  "Launch at login" (a per-user `HKCU\...\Run` value, removed on uninstall), and
  launch-on-finish.
- Output: `dist/installer/screen-annotator-setup-<version>.exe`.
- Build locally (after PyInstaller), from the repo root:
  ```
  iscc /DMyAppVersion=1.0.0 packaging\windows\installer.iss
  ```

---

## Release pipeline (`.github/workflows/release.yml`)

- **Trigger:** push a `v*` tag (`git tag v1.0.1 && git push origin v1.0.1`).
  `workflow_dispatch` also runs it, producing the installer as an artifact only
  (no release) — handy for a test build.
- **Steps (windows-latest):** `pip install ".[build]"` → `pyinstaller` →
  `choco install innosetup` → `iscc` (version taken from the tag) → upload the
  setup exe as a build artifact → attach it to a **GitHub Release**.
- **The release is created as a draft** (`draft: true`) so an unsigned build can
  be verified before it's public. Flip to `draft: false` to auto-publish.
- **Unsigned for now.** A commented `signtool` step is left as the insertion
  point; add a code-signing cert as a secret and uncomment when available.
- The existing `ci.yml` smoke matrix is unchanged (build vs. test separation).

The `.exe` appears in two places per run: the **draft Release** (owner-visible)
and the run's **`screen-annotator-setup` artifact** (fallback).

---

## Testing on Windows

CI proves it *builds*; it can't prove the GUI *runs*. Use a Windows VM (or cloud
VM over RDP) and the checklist in **`testing_windows.md`**: installer wizard,
per-user install, tray icon, `Ctrl+Alt+A` toggle, draw / click-through / scroll-
clear / copy, Settings hotkey change + persistence, "Launch at login", clean
uninstall, and the one-time SmartScreen "Run anyway" path.

---

## Signing & the Microsoft Store (future)

Shipping unsigned means first-run SmartScreen shows "unknown publisher" until
either a code-signing certificate is added (uncomment the `signtool` step) or the
binary accrues reputation.

**MSIX / Microsoft Store** is a documented fast-follow, not the first target: the
Store is the only route to free re-signing (sideloaded MSIX still needs a trusted
cert), and this app installs a global low-level keyboard hook + global hotkey —
keylogger-like to an automated reviewer, so it draws extra Store-certification
scrutiny and gates every update. Revisit once the `.exe` path is proven; no app
changes are needed to add it later.

---

## Linux packaging (planned — Phase E)

An **AppImage** is the Linux "download and run" equivalent: wrap the PyInstaller
one-dir output with `appimagetool`, ship a `.desktop` file + `icon.png`, build on
`ubuntu-latest`. Carries the existing Linux caveats — **X11 only** (not Wayland),
a **compositor** must run for transparency, and **`xclip`** is needed for
clipboard *persistence* after quit. Not yet built.

---

## Known limitations

- Windows uses the **native pointer** (no self-drawn pencil cursor) — hiding the
  cursor for a click-through window isn't reliable there.
- If another app already owns the toggle hotkey, registration fails silently —
  change it in Settings.
- The hotkey key must be a single letter/digit with a modifier (what both OS
  hotkey APIs reliably support).
