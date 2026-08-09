# Testing the Windows build without a Windows PC

You don't need to own a Windows machine to test `screen-annotator` on Windows. There are
two layers of testing, and they complement each other:

- **GitHub Actions CI** (`.github/workflows/ci.yml`) already runs on real
  `windows-latest` runners on every push. It builds the package, imports every module,
  and constructs the Windows backend — which forces all the `ctypes` signatures and
  low-level-hook callbacks to bind. That catches the *build / import / ctypes-crash* class
  of bugs automatically. It **cannot** drive the overlay interactively (no human at a
  desktop), so it proves the code loads and binds, not that drawing feels right.
- **A local VM** (this guide) lets you *use* the overlay yourself. Global hooks and
  layered click-through windows behave normally inside a VM, so it's a faithful test.

---

## Getting Windows for free

- **Windows 11 Enterprise evaluation ISO** — a free 90-day trial from Microsoft's
  Evaluation Center (search "Windows 11 Enterprise evaluation"). Good for either VM below.
- **"Windows 11 development environment" VM images** — Microsoft also publishes ready-made
  evaluation VM images for VirtualBox/VMware/Hyper-V/Parallels (search "Windows 11
  development environment virtual machine"). Fastest start: import and boot.

Both are time-limited evaluations — fine for testing.

---

## Option A — VirtualBox (simplest)

1. Install VirtualBox (`sudo apt install virtualbox` on Debian/Ubuntu).
2. Either **import** Microsoft's dev-environment `.ova`, or **create** a new VM and
   install from the evaluation ISO. Give it ≥ 4 GB RAM and 2 vCPUs.
3. Boot Windows, then install **Guest Additions** (Devices → Insert Guest Additions CD)
   for smooth mouse integration and a resizable display.

## Option B — QEMU/KVM (uses your CPU's VT-x)

1. Install: `sudo apt install qemu-kvm libvirt-daemon-system virt-manager`.
2. Open **virt-manager**, create a new VM from the evaluation ISO (≥ 4 GB RAM, 2 vCPUs).
3. Use **virtio** disk/network drivers for speed (load them during setup, or install the
   virtio driver ISO afterwards).

## Cloud alternative (no local VM)

Spin up a Windows VM on **Azure** or **AWS** free tier (or any cheap hourly Windows VPS),
connect over **RDP**, and run the same checklist below. Zero local setup; small/zero cost
with trial credits.

---

## Inside the Windows guest

1. Install **Python 3** from python.org (tick *"Add python.exe to PATH"*).
2. Get the code — clone with Git, or copy/share the project folder into the VM:
   ```
   git clone https://github.com/ahmedhanifc/screen-annotator.git
   cd screen-annotator
   pip install .
   ```
3. Launch it:
   ```
   screen-annotator
   ```
   It starts in the **system tray** (no window yet). Press **`Ctrl+Alt+A`** to
   toggle the overlay on, and again (or `Esc`) to toggle it off.

## Interactive test checklist

Open a normal app underneath first (a browser, or Notepad with some text) so you can
confirm the overlay stays out of the way.

- [ ] **Tray + hotkey**: a tray icon appears on launch; `Ctrl+Alt+A` shows the
      overlay and again hides it; the tray icon's menu can toggle it and **Quit**.
- [ ] **Hidden = inert**: with the overlay hidden, your keys/clicks all reach apps
      normally (the drawing grabs are only active while the overlay is visible).
- [ ] **Click-through**: with the overlay up, you can still click and **scroll** the app
      underneath — the document moves and the ink clears on scroll.
- [ ] **Draw**: left-drag paints ink; a press-with-no-drag leaves a dot.
- [ ] **Right-click / scroll clear**: both wipe the canvas (pinned ink survives).
- [ ] **Toolbar** (top center): clicking switches tool / colour / size and does
      pin / clear / copy / quit.
- [ ] **Keys**: `h` highlighter, `e` eraser, `t` text (click then type), `p` pin,
      `0`–`9` colour, `[` / `]` size, `Ctrl+Z` undo, `Ctrl+Shift+Z` / `Ctrl+Y` redo,
      `Shift+C` clear pinned. `c` clears.
- [ ] **No key leakage**: keys you *don't* bind (e.g. `Ctrl+C`, arrow keys) still reach
      the app underneath; only our controls are intercepted.
- [ ] **Copy** (`Enter`): paste into Paint / an image field — you get the screen **plus**
      your ink composited, without the toolbar/cursor baked in.
- [ ] **Hide** (`Esc`): the overlay closes and mouse/keyboard return to normal,
      but the app keeps running in the tray (re-open with `Ctrl+Alt+A`).
- [ ] **Quit** (tray → Quit): the tray icon disappears and the process exits.

## Known Windows v1 notes

- The overlay uses the **native mouse pointer** (no custom pencil cursor yet) — hiding the
  system cursor for a click-through window isn't reliable on Windows.
- The global toggle hotkey (`Ctrl+Alt+A`) is **built in** via `RegisterHotKey`; no
  AutoHotkey script is needed. If another app already owns that chord, registration
  fails silently — change the hotkey via the tray icon's **Settings…** dialog.

If something misbehaves, run from a terminal with `set OVERLAY_DEBUG=1` before
`screen-annotator` to print input events and actions to stderr.
