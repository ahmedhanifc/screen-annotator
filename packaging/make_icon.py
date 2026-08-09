# SPDX-License-Identifier: MIT
"""Generate the app icon assets from the same glyph the tray uses.

Renders `screen_annotator.icon._paint` at several sizes (crisp per-size, not one
downscaled bitmap) and writes:
  - screen_annotator/assets/icon.ico  (Windows exe + installer branding)
  - screen_annotator/assets/icon.png  (256px, for Linux packaging later)

Run once when the glyph changes; the outputs are committed so the build doesn't
depend on this script. Needs Pillow + PyQt6:

    QT_QPA_PLATFORM=offscreen python packaging/make_icon.py
"""

import io
import sys
from pathlib import Path

from PyQt6.QtCore import QBuffer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from screen_annotator.icon import _paint  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
ASSETS = Path(__file__).resolve().parent.parent / "screen_annotator" / "assets"


def _render(size):
    pm = QPixmap(size, size)
    _paint(pm)
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main():
    app = QApplication(sys.argv)   # keep a reference (GC would destroy it)
    ASSETS.mkdir(parents=True, exist_ok=True)
    base = _render(max(SIZES))     # render once at the largest size
    base.save(ASSETS / "icon.png")
    # Pillow's ICO writer embeds one entry per requested size off this base.
    base.save(ASSETS / "icon.ico", format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {ASSETS / 'icon.ico'} and icon.png ({len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
