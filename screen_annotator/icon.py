# SPDX-License-Identifier: MIT
"""The app icon, painted with Qt so no binary asset is needed at runtime.

A modern rounded-square badge with a marker/pen glyph, used for the tray icon
and the window/taskbar icon. The Windows installer's .ico is produced from the
same drawing at build time (see packaging/)."""

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap, QPolygon,
)


def _paint(pm: QPixmap) -> None:
    pm.fill(Qt.GlobalColor.transparent)
    s = pm.width()
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Rounded-square badge with a diagonal violet gradient.
    grad = QLinearGradient(0, 0, s, s)
    grad.setColorAt(0.0, QColor("#6d5bff"))
    grad.setColorAt(1.0, QColor("#b14bff"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    radius = s * 0.22
    p.drawRoundedRect(QRect(0, 0, s, s), radius, radius)

    # A marker leaning 45°, nib pointing to the lower-left, drawn about centre.
    p.translate(s * 0.5, s * 0.53)
    p.rotate(45)
    body_w = s * 0.22
    body_len = s * 0.40
    nib_len = s * 0.15
    outline = QPen(QColor(0, 0, 0, 70), max(1.0, s / 48))

    # Barrel.
    p.setPen(outline)
    p.setBrush(QColor("#f7f7fb"))
    p.drawRoundedRect(
        QRect(int(-body_w / 2), int(-(nib_len + body_len)),
              int(body_w), int(body_len)),
        int(s * 0.05), int(s * 0.05),
    )
    # Collar band near the nib.
    p.setBrush(QColor("#c7c7d4"))
    p.drawRect(QRect(int(-body_w / 2), int(-nib_len - s * 0.07),
                     int(body_w), int(s * 0.05)))
    # Nib, in a warm accent.
    p.setBrush(QColor("#ffd166"))
    p.setPen(outline)
    p.drawPolygon(QPolygon([
        QPoint(0, 0),
        QPoint(int(-body_w / 2), int(-nib_len)),
        QPoint(int(body_w / 2), int(-nib_len)),
    ]))
    p.end()


def make_app_icon() -> QIcon:
    """A multi-resolution QIcon. Requires a QGuiApplication to already exist."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        _paint(pm)
        icon.addPixmap(pm)
    return icon
