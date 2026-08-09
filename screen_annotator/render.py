# SPDX-License-Identifier: MIT
"""Render layer — the click-through overlay window.

Platform-agnostic Qt: it only paints ink onto screen-sized pixmaps and reacts to
signals from the input backend. The backend makes the window click-through, hides
the cursor, and sets the clipboard; this module never touches OS input APIs."""

from functools import lru_cache
from importlib.resources import files

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import (
    QColor, QCursor, QFont, QFontDatabase, QFontMetrics, QGuiApplication,
    QPainter, QPainterPath, QPen, QPixmap, QPolygon,
)
from PyQt6.QtWidgets import QApplication, QWidget

from .bridge import SignalBridge
from .config import (
    CURSOR_RADIUS, DEFAULT_COLOR_INDEX, DEFAULT_WIDTH, ERASER_WIDTH_FACTOR,
    HIGHLIGHTER_ALPHA, HIGHLIGHTER_WIDTH_FACTOR, MAX_WIDTH, MIN_WIDTH, PALETTE,
    TEXT_FONT_FILE, TEXT_SIZE_FACTOR, TOAST_MS, TOOLS, UNDO_LIMIT, dbg,
    load_prefs, save_prefs,
)


@lru_cache(maxsize=1)
def _text_font_family() -> str | None:
    """Register the bundled handwriting font with Qt (once) and return its
    family name, or None to fall back to the system default. Loaded from bytes
    rather than a path so it also works from a frozen/zipped install."""
    try:
        data = (files(__package__) / "assets" / TEXT_FONT_FILE).read_bytes()
    except OSError as exc:
        dbg(f"text font {TEXT_FONT_FILE} unreadable: {exc}")
        return None
    font_id = QFontDatabase.addApplicationFontFromData(data)
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        dbg(f"Qt rejected text font {TEXT_FONT_FILE}")
        return None
    return families[0]


class OverlayWindow(QWidget):
    def __init__(self, bridge: SignalBridge, backend):
        super().__init__(None)
        self.bridge = bridge
        self._backend = backend      # provides copy_pixmap_to_clipboard()

        # Restore last-selected prefs, validating the (user-editable) file.
        prefs = load_prefs()
        ci = prefs.get("color_index")
        self.color_index = ci if isinstance(ci, int) and 0 <= ci < len(PALETTE) \
            else DEFAULT_COLOR_INDEX
        w = prefs.get("width")
        self.width = max(MIN_WIDTH, min(MAX_WIDTH, w)) if isinstance(w, int) \
            else DEFAULT_WIDTH
        t = prefs.get("tool")
        self.tool = t if t in TOOLS else "pen"   # one of TOOLS
        # Publish restored tool for the input thread (draw-vs-text on B1 press).
        self.bridge.current_tool = self.tool
        self.pin_mode = False         # route ink to the permanent pin layer?
        self._stroke_layer = "base"   # layer the in-progress stroke targets
        self._undo_stack = []         # (layer, snapshot) pre-stroke states
        self._redo_stack = []         # (layer, snapshot) popped by undo, for redo
        self._last_point = None       # QPoint of the previous stroke sample
        self._stroke_points = []       # pts of the in-progress highlighter stroke
        self._text_active = False     # a text box is being typed into?
        self._text_pos = None         # QPoint insertion point (top-left origin)
        self._text_buffer = ""        # pending text, not yet baked
        self._cursor_enabled = False  # self-drawn marker cursor active?
        self._cursor_pos = None       # last pointer position (widget coords)
        self._suppress_ui = False     # temporarily hide marker+toolbar (on copy)

        self.setWindowTitle("screen-annotator")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # NOTE: deliberately NOT setting WA_TransparentForMouseEvents — on X11 Qt
        # implements it by stamping its own (~3x3) input shape on the X window
        # *after* show, which overrides ours. The backend owns click-through.

        geo = QGuiApplication.primaryScreen().geometry()
        self.setGeometry(geo)
        self._canvas = QPixmap(geo.width(), geo.height())
        self._canvas.fill(Qt.GlobalColor.transparent)
        # The pin layer holds permanent ink; it survives every clear trigger
        # (right-click / scroll / c / trash) and is wiped only by Shift+C.
        self._pin_canvas = QPixmap(geo.width(), geo.height())
        self._pin_canvas.fill(Qt.GlobalColor.transparent)

        # Painted, click-through toolbar (top-center). Geometry depends only on
        # the screen width + fixed metrics, so build it once. Publish its bounds
        # on the bridge so the input thread can route clicks that land on it.
        self._toolbar_items = []      # list of (QRect, kind, value)
        self._toolbar_rect = QRect()
        self._build_toolbar(geo.width())
        r = self._toolbar_rect
        self.bridge.toolbar_rect = (r.x(), r.y(), r.width(), r.height())

        # Transient status toast.
        self._toast_text = ""
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._clear_toast)

        self._connect_signals()

    # -- signal wiring ------------------------------------------------------ #

    def _connect_signals(self):
        b = self.bridge
        b.stroke_begin.connect(self.on_stroke_begin)
        b.stroke_point.connect(self.on_stroke_point)
        b.stroke_end.connect(self.on_stroke_end)
        b.clear_canvas.connect(self.on_clear)
        b.do_copy.connect(self.on_copy)
        # do_quit is handled by the app controller (it hides the overlay); the
        # render layer only *requests* it (Esc via the backend, toolbar ✕ below).
        b.change_size.connect(self.on_change_size)
        b.change_color.connect(self.on_change_color)
        b.toggle_hl.connect(self.on_toggle_hl)
        b.toggle_eraser.connect(self.on_toggle_eraser)
        b.toggle_text.connect(self.on_toggle_text)
        b.toggle_pin.connect(self.on_toggle_pin)
        b.clear_pins.connect(self.on_clear_pins)
        b.text_begin.connect(self.on_text_begin)
        b.text_char.connect(self.on_text_char)
        b.text_backspace.connect(self.on_text_backspace)
        b.text_commit.connect(self.on_text_commit)
        b.text_cancel.connect(self.on_text_cancel)
        b.do_undo.connect(self.on_undo)
        b.do_redo.connect(self.on_redo)
        b.toolbar_press.connect(self.on_toolbar_press)

    # -- show/hide lifecycle ------------------------------------------------ #

    def reset_for_show(self):
        """Give a fresh slate each time the overlay is shown: clear both layers,
        drop undo/redo history, and discard any pending text box or stroke."""
        self._canvas.fill(Qt.GlobalColor.transparent)
        self._pin_canvas.fill(Qt.GlobalColor.transparent)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._last_point = None
        self._stroke_points = []
        self._text_active = False
        self._text_buffer = ""
        self._text_pos = None
        self._cursor_pos = None
        self.update()

    # -- layer helpers ------------------------------------------------------ #

    def _active_layer(self):
        """The layer new ink should target given the current pin mode."""
        return "pin" if self.pin_mode else "base"

    def _layer_pixmap(self, layer):
        return self._pin_canvas if layer == "pin" else self._canvas

    def _set_layer_pixmap(self, layer, pm):
        if layer == "pin":
            self._pin_canvas = pm
        else:
            self._canvas = pm

    # -- pen helpers -------------------------------------------------------- #

    def _stroke_width(self):
        """Effective stroke width for the active tool."""
        if self.tool == "highlighter":
            return self.width * HIGHLIGHTER_WIDTH_FACTOR
        if self.tool == "eraser":
            return self.width * ERASER_WIDTH_FACTOR
        return self.width

    def _pen(self):
        color = QColor(PALETTE[self.color_index][0])
        if self.tool == "highlighter":
            color.setAlpha(HIGHLIGHTER_ALPHA)
        elif self.tool == "eraser":
            # Colour is irrelevant under CompositionMode.Clear; use opaque so the
            # full stroke width zeroes the ink's alpha.
            color = QColor(0, 0, 0, 255)
        pen = QPen(color, self._stroke_width())
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def _configure_painter(self, painter):
        """Set up a canvas painter for the active tool (Clear for eraser)."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(self._pen())
        if self.tool == "eraser":
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )

    def _dirty_rect(self, p1: QPoint, p2: QPoint) -> QRect:
        pad = self.width * max(HIGHLIGHTER_WIDTH_FACTOR, ERASER_WIDTH_FACTOR) + 4
        return QRect(p1, p2).normalized().adjusted(-pad, -pad, pad, pad)

    def _draw_highlighter_path(self, painter, points):
        """Stroke a whole highlighter path as one translucent shape.

        Rendering the stroke as a single path (rather than per-segment lines)
        rasterises it into one coverage region, so overlaps and self-crossings
        composite once instead of stacking alpha and darkening. Shared by the
        live preview and the canvas bake so they match exactly."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor(PALETTE[self.color_index][0])
        color.setAlpha(HIGHLIGHTER_ALPHA)
        pen = QPen(color, self.width * HIGHLIGHTER_WIDTH_FACTOR)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if len(points) == 1:
            painter.drawPoint(points[0])
            return
        path = QPainterPath()
        path.moveTo(points[0].x(), points[0].y())
        for p in points[1:]:
            path.lineTo(p.x(), p.y())
        painter.drawPath(path)

    # -- text helpers ------------------------------------------------------- #

    def _text_font(self) -> QFont:
        """Font for the text tool; size follows the pen size control."""
        family = _text_font_family()
        font = QFont() if family is None else QFont(family)
        font.setPixelSize(max(12, self.width * TEXT_SIZE_FACTOR))
        return font

    def _draw_text(self, painter, pos: QPoint, text: str, caret: bool):
        """Render `text` with its top-left at `pos` (multi-line on '\\n'), in the
        current colour. Shared by the live preview and the canvas bake so they
        match exactly. Draws a trailing caret when `caret` is set."""
        font = self._text_font()
        painter.setFont(font)
        fm = QFontMetrics(font)
        line_h = fm.height()
        color = QColor(PALETTE[self.color_index][0])
        color.setAlpha(255)
        painter.setPen(color)
        lines = text.split("\n") if text else [""]
        for i, line in enumerate(lines):
            baseline = pos.y() + fm.ascent() + i * line_h
            painter.drawText(pos.x(), baseline, line)
        if caret:
            cx = pos.x() + fm.horizontalAdvance(lines[-1]) + 1
            cy = pos.y() + (len(lines) - 1) * line_h
            painter.setPen(QPen(color, 2))
            painter.drawLine(cx, cy + 2, cx, cy + line_h - 2)

    # -- drawing slots ------------------------------------------------------ #

    def on_stroke_begin(self, x, y):
        # Latch the target layer for the whole stroke, so toggling pin mode
        # mid-drag can't split a stroke across layers.
        self._stroke_layer = self._active_layer()
        # Snapshot the canvas before the stroke so the whole stroke is one undo.
        self._push_undo(self._stroke_layer)
        point = QPoint(x, y)
        self._last_point = point
        # The highlighter accumulates points and composites the whole stroke at
        # a single opacity (see on_stroke_end) so overlaps don't darken; it is
        # previewed live in paintEvent rather than drawn into the canvas here.
        if self.tool == "highlighter":
            self._stroke_points = [point]
            self.update(self._dirty_rect(point, point))
            return
        # A press with no drag should still leave a dot.
        painter = QPainter(self._layer_pixmap(self._stroke_layer))
        self._configure_painter(painter)
        painter.drawPoint(point)
        painter.end()
        self.update(self._dirty_rect(point, point))

    def on_stroke_point(self, x, y):
        point = QPoint(x, y)
        if self._last_point is None:
            # A stray motion before any begin: re-latch so the target layer
            # matches the current pin mode.
            self._stroke_layer = self._active_layer()
            self._last_point = point
            if self.tool == "highlighter":
                self._stroke_points = [point]
            return
        if self.tool == "highlighter":
            self._stroke_points.append(point)
            dirty = self._dirty_rect(self._last_point, point)
            self._last_point = point
            self.update(dirty)
            return
        painter = QPainter(self._layer_pixmap(self._stroke_layer))
        self._configure_painter(painter)
        painter.drawLine(self._last_point, point)
        painter.end()
        dirty = self._dirty_rect(self._last_point, point)
        self._last_point = point
        self.update(dirty)

    def on_stroke_end(self):
        # Bake the finished highlighter stroke onto its layer in one pass.
        if self.tool == "highlighter" and self._stroke_points:
            painter = QPainter(self._layer_pixmap(self._stroke_layer))
            self._draw_highlighter_path(painter, self._stroke_points)
            painter.end()
            self.update()
        self._stroke_points = []
        self._last_point = None

    # -- text slots --------------------------------------------------------- #

    def on_text_begin(self, x, y):
        # A new text box replaces any still-open one (commit it first).
        if self._text_active and self._text_buffer:
            self._bake_text()
        self._text_active = True
        self._text_pos = QPoint(x, y)
        self._text_buffer = ""
        self.update()

    def on_text_char(self, s):
        if not self._text_active:
            return
        self._text_buffer += s
        self.update()

    def on_text_backspace(self):
        if self._text_active and self._text_buffer:
            self._text_buffer = self._text_buffer[:-1]
            self.update()

    def on_text_commit(self):
        if self._text_active and self._text_buffer:
            self._bake_text()
        self._text_active = False
        self._text_buffer = ""
        self._text_pos = None
        self.update()

    def on_text_cancel(self):
        self._text_active = False
        self._text_buffer = ""
        self._text_pos = None
        self.update()

    def _bake_text(self):
        """Stamp the pending text onto the active layer as one undo step."""
        layer = self._active_layer()
        self._push_undo(layer)
        painter = QPainter(self._layer_pixmap(layer))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._draw_text(painter, self._text_pos, self._text_buffer, caret=False)
        painter.end()

    def on_clear(self):
        # A clear (scroll or manual) starts a fresh page: base history resets, so
        # you can't undo back through it. Pinned ink (and its history) survives.
        self._last_point = None
        # Defensive: the input thread already cancels any text box before a
        # scroll/right-click clear, but reset here too so state can't linger.
        self._text_active = False
        self._text_buffer = ""
        self._text_pos = None
        self._drop_history("base")
        self._canvas.fill(Qt.GlobalColor.transparent)
        self.update()

    def on_clear_pins(self):
        # Deliberately wipe the permanent layer (Shift+C). Base ink is untouched.
        self._drop_history("pin")
        self._pin_canvas.fill(Qt.GlobalColor.transparent)
        self._toast("pinned ink cleared")
        self.update()

    # -- undo / redo -------------------------------------------------------- #

    def _drop_history(self, layer):
        """Remove a layer's entries from both undo/redo stacks, so clearing one
        layer can't be undone into (or resurrect via redo)."""
        self._undo_stack = [e for e in self._undo_stack if e[0] != layer]
        self._redo_stack = [e for e in self._redo_stack if e[0] != layer]

    def _push_undo(self, layer):
        self._undo_stack.append((layer, self._layer_pixmap(layer).copy()))
        if len(self._undo_stack) > UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def on_undo(self):
        if not self._undo_stack:
            self._toast("nothing to undo")
            return
        layer, snapshot = self._undo_stack.pop()
        self._redo_stack.append((layer, self._layer_pixmap(layer).copy()))
        self._set_layer_pixmap(layer, snapshot)
        self._last_point = None
        self._toast("undo")

    def on_redo(self):
        if not self._redo_stack:
            self._toast("nothing to redo")
            return
        layer, snapshot = self._redo_stack.pop()
        self._undo_stack.append((layer, self._layer_pixmap(layer).copy()))
        self._set_layer_pixmap(layer, snapshot)
        self._last_point = None
        self._toast("redo")

    # -- control slots ------------------------------------------------------ #

    def _save_prefs(self):
        save_prefs(tool=self.tool, color_index=self.color_index, width=self.width)

    def on_change_size(self, delta):
        self.width = max(MIN_WIDTH, min(MAX_WIDTH, self.width + delta))
        self._save_prefs()
        self._toast(f"size {self.width}")

    def on_change_color(self, index):
        if 0 <= index < len(PALETTE):
            self.color_index = index
            self._save_prefs()
            self._toast(f"colour: {PALETTE[index][1]}")

    def set_tool(self, name):
        if name not in TOOLS:
            return
        self.tool = name
        # Publish for the input thread (decides draw-vs-text on Button-1 press).
        self.bridge.current_tool = name
        self._save_prefs()
        self._toast(f"tool: {name}")

    def on_toggle_hl(self):
        self.set_tool("pen" if self.tool == "highlighter" else "highlighter")

    def on_toggle_eraser(self):
        self.set_tool("pen" if self.tool == "eraser" else "eraser")

    def on_toggle_text(self):
        self.set_tool("pen" if self.tool == "text" else "text")

    def on_toggle_pin(self):
        # Route every tool's output to the permanent layer while on. The toast
        # repaints, which also refreshes the toolbar's pin-cell highlight.
        self.pin_mode = not self.pin_mode
        self._toast("pin mode on" if self.pin_mode else "pin mode off")

    def on_toolbar_press(self, x, y):
        """A Button-1 press the input thread flagged as landing on the toolbar.
        Hit-test the painted buttons and dispatch to the matching slot."""
        hit = self.hit_toolbar(x, y)
        if hit is None:
            return
        _rect, kind, value = hit
        if kind == "tool":
            self.set_tool(value)
        elif kind == "color":
            self.on_change_color(value)
        elif kind == "size":
            self.on_change_size(value)
        elif kind == "pin":
            self.on_toggle_pin()
        elif kind == "clear":
            self.on_clear()
        elif kind == "copy":
            self.on_copy()
        elif kind == "quit":
            self.on_quit()

    def on_copy(self):
        # Hide the toast and the marker cursor so neither is baked into the
        # screenshot, then paint synchronously before grabbing the composited
        # screen (document + ink). Capture is Qt-native so it works on every OS;
        # the backend owns putting the pixmap on the clipboard.
        self._toast_timer.stop()
        self._toast_text = ""
        self._suppress_ui = True
        self.repaint()
        QApplication.processEvents()
        try:
            screen = QGuiApplication.primaryScreen()
            pixmap = screen.grabWindow(0)
            self._backend.copy_pixmap_to_clipboard(pixmap)
            self._toast("copied to clipboard")
        except Exception as exc:
            dbg(f"copy failed: {exc}")
            self._toast("copy failed")
        finally:
            self._suppress_ui = False
            self.update()

    def on_quit(self):
        # Toolbar ✕ requests a hide (the app stays alive in the tray). The app
        # controller, connected to do_quit, decides what that means now.
        dbg("hide requested (toolbar)")
        self.bridge.do_quit.emit()

    # -- toast -------------------------------------------------------------- #

    def _toast(self, text):
        self._toast_text = text
        self._toast_timer.start(TOAST_MS)
        self.update()

    def _clear_toast(self):
        self._toast_text = ""
        self.update()

    # -- self-drawn marker cursor ------------------------------------------- #

    def enable_cursor(self, on):
        """Turn the self-drawn marker cursor on (the real cursor is hidden by
        the backend; we paint the pencil ourselves)."""
        self._cursor_enabled = on

    def poll_cursor(self):
        """Timer slot: track the pointer and repaint the marker where it moved.
        Uses QCursor.pos() (the pointer still moves; only its pixels are
        hidden), mapped into widget coordinates."""
        if not self._cursor_enabled:
            return
        pos = self.mapFromGlobal(QCursor.pos())
        if pos == self._cursor_pos:
            return
        old = self._cursor_pos
        self._cursor_pos = pos
        if old is not None:
            self.update(self._cursor_rect(old))   # erase the old marker
        self.update(self._cursor_rect(pos))        # draw it at the new spot
        # If the pointer is on (or just left) the toolbar, repaint the whole
        # bar so hover highlighting is clean regardless of the cursor rect.
        tb = self._toolbar_rect
        if tb.contains(pos) or (old is not None and tb.contains(old)):
            self.update(tb)

    def _cursor_rect(self, p: QPoint) -> QRect:
        r = CURSOR_RADIUS
        return QRect(p.x() - r, p.y() - r, 2 * r, 2 * r)

    def _paint_marker(self, painter, tip: QPoint):
        """Draw a cursor at `tip` (widget coords) that reflects the active tool:
        a pencil (nib tinted with the current colour, doubling as a swatch) for
        pen/highlighter, or a hollow ring for the eraser."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.tool == "eraser":
            # Ring sized to the erase width, bounded so it stays inside the
            # cursor repaint rect (avoids trailing artifacts for fat erasers).
            r = max(6, min(CURSOR_RADIUS - 4, self._stroke_width() // 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(20, 20, 20), 1))
            painter.drawEllipse(tip, r, r)
            painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
            painter.drawEllipse(tip, r - 1, r - 1)
            painter.restore()
            return
        if self.tool == "text":
            # An I-beam so the pointer signals text-entry mode.
            h = 10
            painter.setPen(QPen(QColor(20, 20, 20), 3))
            painter.drawLine(tip.x(), tip.y() - h, tip.x(), tip.y() + h)
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawLine(tip.x(), tip.y() - h, tip.x(), tip.y() + h)
            painter.drawLine(tip.x() - 4, tip.y() - h, tip.x() + 4, tip.y() - h)
            painter.drawLine(tip.x() - 4, tip.y() + h, tip.x() + 4, tip.y() + h)
            painter.restore()
            return
        painter.translate(tip)
        painter.rotate(45)           # lean like a right-hand-held pen; nib stays at tip
        outline = QPen(QColor(20, 20, 20), 1)
        body_w, body_len, nib_len = 9, 24, 8
        # barrel — tinted warm while pin mode is on, so the cursor itself signals
        # that ink will be permanent.
        painter.setPen(outline)
        painter.setBrush(QColor(255, 214, 130) if self.pin_mode
                         else QColor(240, 240, 240))
        painter.drawRoundedRect(
            QRect(-body_w // 2, -(nib_len + body_len), body_w, body_len), 3, 3
        )
        # end cap
        painter.setBrush(QColor(150, 150, 150))
        painter.drawRect(QRect(-body_w // 2, -(nib_len + body_len), body_w, 4))
        # nib in the current pen colour
        painter.setBrush(QColor(PALETTE[self.color_index][0]))
        painter.setPen(outline)
        painter.drawPolygon(QPolygon([
            QPoint(0, 0), QPoint(-body_w // 2, -nib_len), QPoint(body_w // 2, -nib_len),
        ]))
        painter.restore()

    # -- toolbar ------------------------------------------------------------ #

    def _build_toolbar(self, screen_width):
        """Lay out the painted toolbar once. Produces `self._toolbar_items`
        (a list of (QRect, kind, value)) and the bounding `self._toolbar_rect`,
        a centered pill near the top of the screen."""
        BTN = 34          # tool / action button cell
        SWC = 24          # colour-swatch cell
        GAP = 6           # space between cells
        SEP = 14          # extra space between groups
        PAD = 12          # pill inner padding
        H = 46            # pill height
        TOP = 12          # gap from the top of the screen

        # Ordered spec of cells: (kind, value, width). "sep" cells only add gap.
        spec = [("tool", name, BTN) for name in TOOLS]
        spec.append(("sep", None, SEP))
        spec += [("color", i, SWC) for i in range(len(PALETTE))]
        spec.append(("sep", None, SEP))
        spec += [("size", -1, BTN), ("size", +1, BTN)]
        spec.append(("sep", None, SEP))
        spec += [("pin", None, BTN), ("clear", None, BTN),
                 ("copy", None, BTN), ("quit", None, BTN)]

        total = sum(w for _, _, w in spec) + GAP * (len(spec) - 1) + 2 * PAD
        bar_x = max(0, (screen_width - total) // 2)

        items = []
        x = bar_x + PAD
        for kind, value, w in spec:
            if kind != "sep":
                h = SWC if kind == "color" else BTN
                items.append(
                    (QRect(x, TOP + (H - h) // 2, w, h), kind, value)
                )
            x += w + GAP

        self._toolbar_items = items
        self._toolbar_rect = QRect(bar_x, TOP, total, H)

    def hit_toolbar(self, x, y):
        """Return the (QRect, kind, value) item at a point, or None."""
        p = QPoint(x, y)
        for item in self._toolbar_items:
            if item[0].contains(p):
                return item
        return None

    def _paint_toolbar(self, painter):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Pill background.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(28, 28, 30, 214))
        painter.drawRoundedRect(self._toolbar_rect, 12, 12)

        glyph = QColor(236, 236, 236)
        for rect, kind, value in self._toolbar_items:
            active = (
                (kind == "tool" and value == self.tool)
                or (kind == "color" and value == self.color_index)
                or (kind == "pin" and self.pin_mode)
            )
            hover = (self._cursor_pos is not None
                     and rect.contains(self._cursor_pos))
            # Button-cell background (skip for colour swatches, drawn as dots).
            if kind != "color" and (active or hover):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 64 if active else 30))
                painter.drawRoundedRect(rect, 7, 7)

            if kind == "tool":
                self._paint_tool_glyph(painter, rect, value, glyph)
            elif kind == "color":
                self._paint_swatch(painter, rect, value, active, hover)
            elif kind == "size":
                self._paint_size_glyph(painter, rect, value, glyph)
            elif kind == "pin":
                self._paint_pin_glyph(painter, rect, glyph)
            elif kind == "clear":
                self._paint_clear_glyph(painter, rect, glyph)
            elif kind == "copy":
                self._paint_copy_glyph(painter, rect, glyph)
            elif kind == "quit":
                self._paint_quit_glyph(painter, rect)

    def _paint_tool_glyph(self, painter, rect, tool, glyph):
        c = rect.center()
        col = QColor(PALETTE[self.color_index][0])
        if tool == "pen":
            painter.setPen(QPen(col, 3))
            painter.drawLine(c.x() - 7, c.y() + 7, c.x() + 7, c.y() - 7)
        elif tool == "highlighter":
            bar = QColor(col)
            bar.setAlpha(150)
            painter.setPen(QPen(bar, 8))
            painter.drawLine(c.x() - 7, c.y() + 7, c.x() + 7, c.y() - 7)
        elif tool == "eraser":
            painter.setPen(QPen(glyph, 1))
            painter.setBrush(QColor(240, 180, 190))
            r = QRect(c.x() - 8, c.y() - 5, 16, 10)
            painter.drawRoundedRect(r, 2, 2)
        elif tool == "text":
            painter.setPen(QPen(col, 1))
            font = QFont()
            font.setPixelSize(20)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "T")

    def _paint_swatch(self, painter, rect, index, active, hover):
        col = QColor(PALETTE[index][0])
        d = 16
        dot = QRect(0, 0, d, d)
        dot.moveCenter(rect.center())
        painter.setBrush(col)
        if active:
            painter.setPen(QPen(QColor(255, 255, 255), 2))
        elif hover:
            painter.setPen(QPen(QColor(255, 255, 255, 150), 1))
        else:
            painter.setPen(QPen(QColor(0, 0, 0, 120), 1))
        painter.drawEllipse(dot)

    def _paint_size_glyph(self, painter, rect, delta, glyph):
        c = rect.center()
        painter.setPen(QPen(glyph, 2))
        painter.drawLine(c.x() - 6, c.y(), c.x() + 6, c.y())      # minus bar
        if delta > 0:
            painter.drawLine(c.x(), c.y() - 6, c.x(), c.y() + 6)  # vertical -> plus

    def _paint_pin_glyph(self, painter, rect, glyph):
        # A pushpin, drawn about the cell centre and leaned 45°. When pin mode
        # is on the head fills with the current pen colour (so it reads as
        # "armed"); when off it's a hollow outline.
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(rect.center())
        painter.rotate(45)
        outline = QPen(glyph, 1.5)
        painter.setPen(outline)
        if self.pin_mode:
            painter.setBrush(QColor(PALETTE[self.color_index][0]))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
        # Head + flange (the graspable top of the pin).
        painter.drawRoundedRect(QRect(-4, -9, 8, 7), 2, 2)
        painter.drawPolygon(QPolygon([
            QPoint(-6, 2), QPoint(6, 2), QPoint(4, -2), QPoint(-4, -2),
        ]))
        # Needle.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(0, 2, 0, 11)
        painter.restore()

    def _paint_clear_glyph(self, painter, rect, glyph):
        # A small trash can: lid + body.
        c = rect.center()
        painter.setPen(QPen(glyph, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(c.x() - 8, c.y() - 6, c.x() + 8, c.y() - 6)   # lid
        painter.drawLine(c.x() - 3, c.y() - 9, c.x() + 3, c.y() - 9)   # handle
        body = QRect(c.x() - 6, c.y() - 4, 12, 12)
        painter.drawRect(body)

    def _paint_copy_glyph(self, painter, rect, glyph):
        # Two offset rounded rectangles (copy icon).
        c = rect.center()
        painter.setPen(QPen(glyph, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRect(c.x() - 7, c.y() - 4, 10, 12), 2, 2)
        painter.drawRoundedRect(QRect(c.x() - 3, c.y() - 8, 10, 12), 2, 2)

    def _paint_quit_glyph(self, painter, rect):
        c = rect.center()
        painter.setPen(QPen(QColor(255, 120, 120), 2))
        painter.drawLine(c.x() - 6, c.y() - 6, c.x() + 6, c.y() + 6)
        painter.drawLine(c.x() - 6, c.y() + 6, c.x() + 6, c.y() - 6)

    # -- painting ----------------------------------------------------------- #

    def paintEvent(self, event):
        painter = QPainter(self)
        # Pinned ink sits beneath the ephemeral canvas so fresh scratch stays
        # readable on top of permanent reference marks.
        painter.drawPixmap(0, 0, self._pin_canvas)
        painter.drawPixmap(0, 0, self._canvas)
        # Preview the in-progress highlighter stroke over the (not-yet-baked)
        # canvas so it shows at its true single-pass opacity while drawing.
        if (self.tool == "highlighter" and self._stroke_points
                and not self._suppress_ui):
            self._draw_highlighter_path(painter, self._stroke_points)
        if (self._text_active and not self._suppress_ui
                and self._text_pos is not None):
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            self._draw_text(painter, self._text_pos, self._text_buffer,
                            caret=True)
        if not self._suppress_ui:
            self._paint_toolbar(painter)
        if (self._cursor_enabled and not self._suppress_ui
                and self._cursor_pos is not None):
            self._paint_marker(painter, self._cursor_pos)
        if self._toast_text:
            self._paint_toast(painter)
        painter.end()

    def _paint_toast(self, painter):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = painter.font()
        font.setPointSize(13)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text = self._toast_text
        tw = metrics.horizontalAdvance(text)
        th = metrics.height()
        pad = 12
        box = QRect(24, 24, tw + 2 * pad, th + pad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 180))
        painter.drawRoundedRect(box, 8, 8)
        # A small swatch of the current colour.
        swatch = QColor(PALETTE[self.color_index][0])
        painter.setBrush(swatch)
        painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
        painter.drawEllipse(box.left() + pad, box.top() + (box.height() - 10) // 2, 10, 10)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            box.adjusted(pad + 18, 0, 0, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )
