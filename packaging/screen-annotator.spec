# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — one-dir, windowed build of screen-annotator.
#
#   pyinstaller packaging/screen-annotator.spec
#
# One-dir (not one-file): faster startup, and the installer hides the folder
# from users anyway. Built per-OS (no cross-compile) — the Windows exe must be
# built on Windows; the same spec also freezes on Linux for the AppImage.
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)                      # repo root (spec lives in packaging/)
ENTRY = os.path.join(ROOT, "packaging", "entry.py")   # absolute-import entry (not __main__.py)
ICON = os.path.join(ROOT, "screen_annotator", "assets", "icon.ico")
ASSETS = os.path.join(ROOT, "screen_annotator", "assets")

# The text tool's handwriting font is read at runtime, so it must be shipped
# (with its OFL licence). The icon needs no entry: it is compiled into the exe.
fonts = [
    (os.path.join(ASSETS, name), "screen_annotator/assets")
    for name in ("Caveat-Bold.ttf", "Caveat-OFL.txt")
]

# The platform backends are imported lazily inside get_backend(); pull them in
# explicitly so neither is dropped. (On Windows the x11 backend is bundled but
# never imported — its Xlib import just yields a harmless build-time warning.)
hidden = collect_submodules("screen_annotator.backends")

a = Analysis(
    [ENTRY],
    pathex=[ROOT],
    binaries=[],
    datas=fonts,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="screen-annotator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed: no console window on launch
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="screen-annotator",
)
