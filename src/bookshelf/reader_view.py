"""Distraction-free reader: one page or a two-page spread, zoom, fit width,
swipe to turn pages, a side panel you can hide, and highlighting / drawing."""
import math
import time
from collections import OrderedDict
from typing import NamedTuple

import pymupdf
from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor, QCursor, QEventPoint, QImage, QInputDevice, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup, QColorDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QToolButton,
    QVBoxLayout, QWidget,
)

from . import theme
from .annotations import Annotations, cut
from . import library
from .library import Book, Library
from .overlay import OverlayBar, bar_button as _bar_button
from .theme import Glyph, glyph_icon, themed_icon
from .tools import ToolDock

MARGIN = 40                  # space around the pages in "fit page"
PANEL_W = 290                # width of the side panel
EDGE = 16                    # gap between panel and screen edge
MAX_ZOOM = 6.0               # zooming out stops when the whole page fits
MAX_FULL_PIXELS = 5_000_000  # above this, only the visible part of a page is rendered
CACHE_BYTES = 320 * 1024 * 1024
MAX_THUMBS = 12                # tiny page copies kept for the side panel's frosted glass
IDLE_MS = 2200
SCROLL_STEP = 140            # pixels per mouse-wheel notch
WHEEL_SWIPE = 260            # sideways touchpad travel (past the edge) that turns a page
TOUCH_SWIPE = 90             # sideways finger travel (past the edge) that turns a page
GESTURE_GAP_MS = 280         # quiet time that ends a touchpad gesture
PINCH_TAIL = 0.4             # seconds to ignore plain scrolling right after a pinch
STRAIGHT_SNAP = 0.14         # straight highlighter lines this close to flat (~8°) snap level
STEADY_RADIUS = 12           # highlighter steadying: the line trails the finger by this many px
STRAIGHT_PX = 7              # a highlighter stroke wobbling less than this becomes a straight line
BLUR_SCALE = 0.1             # the panel's frosted glass is the page drawn at 1/10 size, stretched
ERASER_RADIUS = 16           # screen pixels


class Metrics(NamedTuple):
    units: list        # (page index, width in spread units); every page is unit_h tall
    unit_h: float
    scale: float       # screen pixels per spread unit
    pad_x: float
    pad_y: float
    content_w: float
    content_h: float
    vp: QRect          # the area pages are shown in


def _section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("Section")
    return label


def _segmented(group: QButtonGroup, options) -> QFrame:
    """A pill of mutually exclusive buttons. options: (id, glyph or None, label, tooltip)."""
    frame = QFrame()
    frame.setObjectName("Segmented")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(3, 3, 3, 3)
    lay.setSpacing(2)
    for key, glyph, label, tip in options:
        b = QPushButton(label)
        b.setObjectName("Segment")
        b.setCheckable(True)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if glyph:
            themed_icon(b, glyph, "TEXT", 16)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        group.addButton(b, key)
        lay.addWidget(b)
    return frame


class ReaderView(QWidget):
    close_requested = Signal()
    fullscreen_requested = Signal()
    app_theme_requested = Signal(str)  # "dark" | "light"

    FIT_IDS = {"page": 1, "width": 2}

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self.library = library
        self.book: Book | None = None
        self.doc: pymupdf.Document | None = None
        self.page = 0
        self.mode = 1
        self.fit = "page"
        self.zoom = 1.0
        self.controls_visible = False  # every book opens with the side panel hidden
        self._ox = self._oy = 0.0  # scroll offsets when the pages are bigger than the screen
        self._sizes: dict[int, tuple[float, float]] = {}
        self._cache: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._cache_bytes = 0
        self._old_frame: QPixmap | None = None
        self._fade = 0.0
        self._interacting = False
        self._drag = None          # mouse: (press position, ox, oy)
        self._dragging = False
        self._swipe = None         # touchpad gesture in progress
        self._pinch_until = 0.0
        self._pinch_anchor: QPointF | None = None
        self._zoom_spot: tuple[float, float] | None = None  # page spot a pinch is zooming toward
        self._hold_next = False
        self._free = False         # page placed freely during a pinch
        self._free_path = ((0.0, 0.0), (0.0, 0.0))
        self._touch_state = None   # touchscreen gesture in progress
        self._log_file = None      # Ctrl+Shift+G writes every gesture event here
        self._panel_blur: QPixmap | None = None
        self._thumbs: dict[int, QPixmap] = {}  # tiny page copies for the glass blur
        themes = self._page_themes()
        theme_key = library.settings.get("page_theme", "original")
        self._theme_key = theme_key if theme_key in themes else "original"
        self._theme = themes[self._theme_key]
        self._fullscreen = True
        self.annots: Annotations | None = None
        self._paths: dict[str, QPainterPath] = {}     # annotation id -> drawn path
        self._stroke: dict | None = None             # the highlight / drawing in progress
        self._mouse_drawing = False
        self._eraser_cursor = self._make_eraser_cursor()

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setDuration(200)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._fade_anim.valueChanged.connect(self._set_fade)

        self._idle = QTimer(self, singleShot=True, interval=IDLE_MS, timeout=self._on_idle)
        self._save_timer = QTimer(self, singleShot=True, interval=1000, timeout=self.library.save)
        self._prefetch_timer = QTimer(self, singleShot=True, interval=150, timeout=self._prefetch)
        self._settle_timer = QTimer(self, singleShot=True, interval=200, timeout=self._settle)
        self._toast_timer = QTimer(self, singleShot=True, interval=900, timeout=lambda: self.toast.conceal())
        self._swipe_timer = QTimer(self, singleShot=True, interval=GESTURE_GAP_MS,
                                   timeout=lambda: setattr(self, "_swipe", None))
        self._zoom_timer = QTimer(self, interval=16, timeout=self._zoom_step)  # ~60 fps
        self._pinch_end_timer = QTimer(self, singleShot=True, interval=300, timeout=self._end_pinch)
        self._free_anim = QVariantAnimation(self)
        self._free_anim.setDuration(240)
        self._free_anim.setStartValue(0.0)
        self._free_anim.setEndValue(1.0)
        self._free_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._free_anim.valueChanged.connect(self._free_step)
        self._free_anim.finished.connect(self._free_done)
        self._zoom_target = 1.0
        self._zoom_anchor: QPointF | None = None

        self._annot_timer = QTimer(self, singleShot=True, interval=800, timeout=self._save_annots)

        self._build_overlays()
        self.tools = ToolDock(library.settings, self)
        self.tools.tool_changed.connect(self._on_tool_changed)
        self.tools.undo_requested.connect(self.undo)
        self.tools.redo_requested.connect(self.redo)
        self.tools.clear_requested.connect(self.clear_spread)

    # ---------- UI ----------
    def _build_overlays(self):
        self.panel = OverlayBar("SidePanel", self)
        self.panel.set_glass(lambda: self._panel_blur)
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(14, 12, 14, 16)
        lay.setSpacing(8)

        head = QHBoxLayout()
        back = _bar_button(Glyph.BACK, "Back to library (Esc)", " Library")
        back.clicked.connect(self.close_requested)
        close = _bar_button(Glyph.CLOSE, "Hide panel (H)")
        close.clicked.connect(self.toggle_controls)
        self.fs_button = _bar_button(Glyph.EXIT_FULLSCREEN, "Full screen (F11)")
        self.fs_button.clicked.connect(self.fullscreen_requested)
        head.addWidget(back)
        head.addStretch()
        head.addWidget(self.fs_button)
        head.addWidget(close)
        lay.addLayout(head)

        self.title = QLabel()
        self.title.setObjectName("BookTitle")
        self.title.setWordWrap(True)
        self.title.setMaximumHeight(48)
        lay.addWidget(self.title)

        # --- page ---
        lay.addWidget(_section("Page"))
        nav = QHBoxLayout()
        nav.setSpacing(4)
        prev = _bar_button(Glyph.PREV, "Previous page (←)")
        prev.clicked.connect(self.prev_page)
        nxt = _bar_button(Glyph.NEXT, "Next page (→)")
        nxt.clicked.connect(self.next_page)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setTracking(False)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.valueChanged.connect(lambda v: self.go_to(v))
        self.slider.sliderMoved.connect(lambda v: self.page_label.setText(self._page_text(v)))
        nav.addWidget(prev)
        nav.addWidget(self.slider, 1)
        nav.addWidget(nxt)
        lay.addLayout(nav)
        self.page_label = QLabel()
        self.page_label.setObjectName("PageLabel")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.page_label)

        # --- layout ---
        lay.addWidget(_section("Pages on screen"))
        self.modes = QButtonGroup(self)
        lay.addWidget(_segmented(self.modes, (
            (1, Glyph.SINGLE_PAGE, " One", "One page per screen (1)"),
            (2, Glyph.TWO_PAGE, " Two", "Two pages per screen (2)"),
        )))
        self.modes.idClicked.connect(self.set_mode)

        # --- fit ---
        lay.addWidget(_section("Fit"))
        self.fits = QButtonGroup(self)
        lay.addWidget(_segmented(self.fits, (
            (1, None, "Whole page", "Show the whole page (W)"),
            (2, None, "Full width", "Fill the screen width and scroll (W)"),
        )))
        self.fits.idClicked.connect(lambda i: self.set_fit("page" if i == 1 else "width"))

        # --- zoom ---
        lay.addWidget(_section("Zoom"))
        zoom_row = QHBoxLayout()
        zoom_out = _bar_button(Glyph.ZOOM_OUT, "Zoom out (Ctrl -)")
        zoom_out.clicked.connect(lambda: self.set_zoom(self._zoom_target_now() / 1.25, animate=True))
        self.zoom_label = QToolButton()
        self.zoom_label.setObjectName("ZoomLabel")
        self.zoom_label.setToolTip("Reset zoom (Ctrl 0)")
        self.zoom_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.zoom_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.zoom_label.clicked.connect(lambda: self.set_zoom(1.0, animate=True))
        zoom_in = _bar_button(Glyph.ZOOM_IN, "Zoom in (Ctrl +)")
        zoom_in.clicked.connect(lambda: self.set_zoom(self._zoom_target_now() * 1.25, animate=True))
        zoom_row.addWidget(zoom_out)
        zoom_row.addWidget(self.zoom_label, 1)
        zoom_row.addWidget(zoom_in)
        lay.addLayout(zoom_row)

        # --- page colour ---
        lay.addWidget(_section("Page color"))
        swatches = QHBoxLayout()
        swatches.setSpacing(6)
        self._swatches: dict[str, QPushButton] = {}
        for key, t in theme.PAGE_THEMES.items():
            swatches.addWidget(self._make_swatch(key, t))
        swatches.addStretch()
        lay.addLayout(swatches)
        self._custom_row = QHBoxLayout()  # your own colours (up to 3) and the + button
        self._custom_row.setSpacing(6)
        lay.addLayout(self._custom_row)
        name_row = QHBoxLayout()
        self.theme_name = QLabel()
        self.theme_name.setObjectName("ThemeName")
        self.remove_color = QPushButton("Remove")
        self.remove_color.setObjectName("LinkButton")
        self.remove_color.setToolTip("Remove this color from your colors")
        self.remove_color.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remove_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_color.clicked.connect(self.remove_custom_color)
        name_row.addWidget(self.theme_name)
        name_row.addStretch()
        name_row.addWidget(self.remove_color)
        lay.addLayout(name_row)
        self._build_custom_swatches()

        # --- the app's own look ---
        lay.addWidget(_section("App look"))
        self.looks = QButtonGroup(self)
        lay.addWidget(_segmented(self.looks, (
            (1, Glyph.MOON, " Dark", "Dark app"),
            (2, Glyph.SUN, " Light", "Light app"),
        )))
        self.looks.idClicked.connect(lambda i: self.app_theme_requested.emit("dark" if i == 1 else "light"))
        self._sync_look()
        theme.on_change(self._sync_look)
        theme.on_change(lambda: self.set_fullscreen_state(self._fullscreen))
        theme.on_change(self.update)

        lay.addStretch(1)
        hint = QLabel("Swipe sideways to turn pages · pinch to zoom")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)
        self.set_fullscreen_state(True)

        # ☰ button in the corner that opens the panel
        self.corner = OverlayBar("CornerBar", self)
        clay = QHBoxLayout(self.corner)
        clay.setContentsMargins(2, 2, 2, 2)
        show = _bar_button(Glyph.MENU, "Show panel (H)")
        themed_icon(show, Glyph.MENU, "FAINT")
        show.clicked.connect(self.toggle_controls)
        clay.addWidget(show)

        self.toast = OverlayBar("Toast", self)
        tlay = QHBoxLayout(self.toast)
        tlay.setContentsMargins(0, 0, 0, 0)
        self.toast_label = QLabel()
        self.toast_label.setObjectName("ToastLabel")
        tlay.addWidget(self.toast_label)

    def set_fullscreen_state(self, fullscreen: bool):
        self._fullscreen = fullscreen
        self.fs_button.setIcon(glyph_icon(Glyph.EXIT_FULLSCREEN if fullscreen else Glyph.FULLSCREEN))
        self.fs_button.setToolTip("Exit full screen (F11)" if fullscreen else "Full screen (F11)")

    def _sync_look(self):
        self.looks.button(1 if theme.MODE == "dark" else 2).setChecked(True)

    # ---------- page colours ----------
    def _custom_colors(self) -> list[str]:
        colors = self.library.settings.get("custom_page_colors", [])
        return [c for c in colors if isinstance(c, str) and QColor(c).isValid()][:3]

    def _page_themes(self) -> dict:
        themes = dict(theme.PAGE_THEMES)
        for n, color in enumerate(self._custom_colors(), 1):
            themes[f"custom:{color}"] = theme.custom_page_theme(color, n)
        return themes

    def _make_swatch(self, key: str, t) -> QPushButton:
        b = QPushButton()
        b.setObjectName("Swatch")
        b.setCheckable(True)
        b.setFixedSize(30, 30)
        b.setToolTip(t.name)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        # each swatch shows the page colour with a dot of its text colour
        b.setStyleSheet(
            f"QPushButton#Swatch {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, "
            f"fx:0.5, fy:0.5, stop:0 {t.ink}, stop:0.22 {t.ink}, stop:0.26 {t.paper}, "
            f"stop:1 {t.paper}); }}")
        b.clicked.connect(lambda _=False, k=key: self.set_theme(k))
        self._swatches[key] = b
        return b

    def _build_custom_swatches(self):
        row = self._custom_row
        while row.count():
            item = row.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for key in [k for k in self._swatches if k.startswith("custom:")]:
            del self._swatches[key]
        for key, t in self._page_themes().items():
            if key.startswith("custom:"):
                row.addWidget(self._make_swatch(key, t))
        if len(self._custom_colors()) < 3:
            add = QPushButton("+")
            add.setObjectName("AddSwatch")
            add.setFixedSize(30, 30)
            add.setToolTip("Add your own page color (up to 3)")
            add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            add.setCursor(Qt.CursorShape.PointingHandCursor)
            add.clicked.connect(self.add_custom_color)
            row.addWidget(add)
        row.addStretch()
        for k in range(row.count()):
            if row.itemAt(k).widget():
                row.itemAt(k).widget().show()
        self._sync_theme_buttons()

    def add_custom_color(self):
        color = QColorDialog.getColor(QColor(self._theme.paper), self, "Choose a page color")
        self.setFocus()
        if not color.isValid():
            return
        name = color.name()
        colors = [c for c in self._custom_colors() if c != name] + [name]
        self.library.settings["custom_page_colors"] = colors[-3:]
        self._build_custom_swatches()
        self.set_theme(f"custom:{name}")

    def remove_custom_color(self):
        if not self._theme_key.startswith("custom:"):
            return
        name = self._theme_key.split(":", 1)[1]
        self.library.settings["custom_page_colors"] = [c for c in self._custom_colors() if c != name]
        self.set_theme("original")
        self._build_custom_swatches()

    def _place_overlays(self):
        self.panel.setGeometry(EDGE, EDGE, PANEL_W, self.height() - 2 * EDGE)
        self.corner.setGeometry(EDGE, EDGE, 48, 48)
        self._place_toast()
        if hasattr(self, "tools"):
            self.tools.place()

    def _place_toast(self):
        size = self.toast.sizeHint()
        vp = self._viewport()
        self.toast.setGeometry(vp.x() + (vp.width() - size.width()) // 2,
                               self.height() - 48 - size.height(), size.width(), size.height())

    def _show_toast(self, text: str):
        if self.controls_visible:
            return
        self.toast_label.setText(text)
        self.toast.adjustSize()
        self._place_toast()
        self.toast.reveal()
        self._toast_timer.start()

    def toggle_controls(self):
        self.controls_visible = not self.controls_visible
        self._apply_controls()
        self.setFocus()

    def _apply_controls(self):
        if self.controls_visible:
            self.panel.reveal()
            self.corner.conceal()
            self.toast.conceal()
        else:
            self.panel.conceal()
            self._wake()
        self._old_frame = None
        self._clamp()
        self._place_toast()
        self._touch()
        self.update()

    def _wake(self):
        """User did something: show the ☰ button for a moment (when the panel is hidden)."""
        if not self.controls_visible:
            self.corner.reveal()
        self._idle.start()

    def _on_idle(self):
        if self.corner.underMouse() or self._dragging:
            self._idle.start()
            return
        if not self.controls_visible:
            self.corner.conceal()
        if not self.panel.underMouse():
            self.setCursor(Qt.CursorShape.BlankCursor)

    def _update_cursor(self):
        tool = self.tools.tool if hasattr(self, "tools") else None
        if tool == "pen":
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif tool == "highlight":
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif tool == "eraser":
            self.setCursor(self._eraser_cursor)
        elif self._dragging:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif any(self._overflow()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_theme(self, key: str):
        themes = self._page_themes()
        if key not in themes:
            return
        self._theme_key = key
        self._theme = themes[key]
        self.library.settings["page_theme"] = key
        self._save_timer.start()
        self._sync_theme_buttons()
        self._clear_cache()
        self._old_frame = None
        self.update()

    def _sync_theme_buttons(self):
        for key, b in self._swatches.items():
            b.setChecked(key == self._theme_key)
        self.theme_name.setText(self._theme.name)
        self.remove_color.setVisible(self._theme_key.startswith("custom:"))

    def _tint(self, pix: "pymupdf.Pixmap"):
        """Recolour a rendered page: white becomes the paper colour, black the ink colour."""
        if self._theme_key == "original":
            return
        pix.tint_with(int(self._theme.ink[1:], 16), int(self._theme.paper[1:], 16))

    def _sync_buttons(self):
        self.modes.button(self.mode).setChecked(True)
        self.fits.button(self.FIT_IDS[self.fit]).setChecked(True)
        self.zoom_label.setText(f"{round(self.zoom * 100)}%")

    # ---------- book lifecycle ----------
    def open_book(self, book: Book):
        self.close_book()
        self.doc = library.open_document(book.path)
        self.book = book
        self._sizes.clear()
        self._clear_cache()
        self._old_frame = None
        self.mode = 2 if book.view_mode == 2 else 1
        self.fit = book.fit_mode if book.fit_mode in self.FIT_IDS else "page"
        self.zoom = 1.0
        self.page = self._align(min(max(book.current_page, 0), self.doc.page_count - 1))
        self._centre_view()
        self.title.setText(book.title)
        self.title.setToolTip(book.title)
        self._sync_buttons()
        self.slider.blockSignals(True)
        self.slider.setRange(0, self.doc.page_count - 1)
        self.slider.blockSignals(False)
        book.page_count = self.doc.page_count
        self.annots = Annotations(book.id)
        self._paths.clear()
        self._stroke = None
        self.tools.set_history(False, False)
        self._page_changed()
        self.setFocus()
        self.controls_visible = False  # start every book with the side panel hidden
        self._apply_controls()

    def close_book(self):
        if self.book:
            self.book.current_page = self.page
            self.book.view_mode = self.mode
            self.book.fit_mode = self.fit
            self._save_timer.stop()
            self.library.save()
        self.tools.close()
        self._save_annots()
        self.annots = None
        if self.doc:
            self.doc.close()
        self.doc = None
        self.book = None
        self._clear_cache()

    # ---------- navigation ----------
    @property
    def count(self) -> int:
        return self.doc.page_count if self.doc else 0

    def _align(self, page: int) -> int:
        return page - page % 2 if self.mode == 2 else page

    def _spread(self, page: int) -> list[int]:
        return [i for i in range(page, page + self.mode) if i < self.count]

    def go_to(self, page: int, animate: bool = True, at_end: bool = False, from_right: bool = False):
        """Show a page. at_end lands on its bottom; from_right lands on its right edge."""
        if not self.doc:
            return
        page = self._align(min(max(page, 0), self.count - 1))
        if page == self.page:
            self._sync_slider()
            return
        self._cancel_free()
        if animate:
            self._old_frame = self._snapshot()
            self._fade_anim.stop()
            self._fade_anim.start()
        self.page = page
        m = self._metrics(page)
        if m.content_h > m.vp.height():
            self._oy = float("inf") if at_end else 0.0
        if m.content_w > m.vp.width() and self.zoom <= 1.0005:
            self._ox = float("inf") if from_right else 0.0
        # zoomed in: keep the same sideways position, so you stay on the same column
        self._clamp()
        self._page_changed()
        self._show_toast(self._page_text(page))

    def next_page(self):
        self.go_to(self.page + self.mode)

    def prev_page(self):
        self.go_to(self.page - self.mode, from_right=True)

    def set_mode(self, mode: int):
        if mode == self.mode or not self.doc:
            return
        self._cancel_free()
        self.mode = mode
        self.page = self._align(self.page)
        self._centre_view()
        self._old_frame = None
        if self.book:
            self.book.view_mode = mode
        self._sync_buttons()
        self._page_changed()

    def set_fit(self, fit: str):
        if not self.doc:
            return
        self._cancel_free()
        self.fit = fit
        self.zoom = 1.0
        self._centre_view()
        self._old_frame = None
        if self.book:
            self.book.fit_mode = fit
            self._save_timer.start()
        self._sync_buttons()
        self._touch()
        self._update_cursor()
        self.update()

    def set_zoom(self, zoom: float, anchor: QPointF | None = None, animate: bool = False,
                 hold: bool = False):
        """Zoom while keeping the point under `anchor` (default: screen centre) still.
        animate=True glides there over a few frames instead of jumping.
        hold=True keeps zooming toward the spot of the page that was under the anchor
        when this zoom gesture started (so a pinch always lands where it began)."""
        if not self.doc:
            return
        zoom = min(max(zoom, self._min_zoom()), MAX_ZOOM)
        self._hold_next = hold
        if not hold:
            self._zoom_spot = None
        if animate:
            self._zoom_target = zoom
            self._zoom_anchor = anchor
            if not self._zoom_timer.isActive():
                self._zoom_timer.start()
            return
        self._zoom_timer.stop()
        self._apply_zoom(zoom, anchor)

    def _zoom_target_now(self) -> float:
        """Where zooming is heading (so quick pinch steps add up, not reset)."""
        return self._zoom_target if self._zoom_timer.isActive() else self.zoom

    def _zoom_step(self):
        target = self._zoom_target
        z = self.zoom + (target - self.zoom) * 0.35
        if abs(target - z) < 0.003:
            z = target
            self._zoom_timer.stop()
        self._apply_zoom(z, self._zoom_anchor)

    def _apply_zoom(self, zoom: float, anchor: QPointF | None, force: bool = False):
        zoom = min(max(zoom, self._min_zoom()), MAX_ZOOM)
        if abs(zoom - self.zoom) < 1e-4 and not force:
            return
        before = self._metrics(self.page)
        old_zoom = self.zoom
        if anchor is None:
            anchor = QPointF(before.vp.center())
        if self._zoom_spot is not None:
            ux, uy = self._zoom_spot
        else:
            origin = self._origin(self.page)
            ux = (anchor.x() - origin.x()) / before.scale
            uy = (anchor.y() - origin.y()) / before.scale
            if self._hold_next:
                self._zoom_spot = (ux, uy)
        self.zoom = zoom
        after = self._metrics(self.page)
        # choose offsets so the first page's new origin keeps (ux, uy) under the anchor
        self._ox = after.vp.x() + after.pad_x - (anchor.x() - ux * after.scale)
        self._oy = after.vp.y() + after.pad_y - (anchor.y() - uy * after.scale)
        if zoom < old_zoom and old_zoom > 1.0005:
            # zooming out: slide back toward the middle, arriving centred at 100%
            pull = min(1.0, (old_zoom - zoom) / (old_zoom - 1.0))
            for axis, content, view in ((0, after.content_w, after.vp.width()),
                                        (1, after.content_h, after.vp.height())):
                if content < view:
                    centre = -(view - content) / 2
                    if axis == 0:
                        self._ox += (centre - self._ox) * pull
                    else:
                        self._oy += (centre - self._oy) * pull
        self._clamp()
        self._old_frame = None
        self._sync_buttons()
        self._show_toast(f"{round(zoom * 100)}%")
        self._touch()
        self._update_cursor()
        self.update()

    def _can_swipe(self) -> bool:
        """Swipes turn pages only when not zoomed in and the full page width is visible,
        so moving around a zoomed page never flips it."""
        return self.zoom <= 1.001 and self._overflow()[0] <= 0

    def _pan(self, dx: float, dy: float) -> tuple[float, float]:
        """Move the view by (dx, dy) screen pixels; returns how much it really moved."""
        ox, oy = self._ox, self._oy
        self._ox += dx
        self._oy += dy
        self._clamp()
        moved = (self._ox - ox, self._oy - oy)
        if moved != (0.0, 0.0):
            self._touch()
            self.update()
        return moved

    def _page_changed(self):
        if self.book:
            self.book.current_page = self.page
            self.book.last_opened = time.time()
            self._save_timer.start()
        self._sync_slider()
        self._update_cursor()
        self.update()

    def _page_text(self, page: int) -> str:
        pages = self._spread(self._align(page)) if self.doc else []
        if not pages:
            return ""
        shown = f"{pages[0] + 1}" if len(pages) == 1 else f"{pages[0] + 1}–{pages[-1] + 1}"
        return f"{shown} / {self.count}"

    def _sync_slider(self):
        self.slider.blockSignals(True)
        self.slider.setValue(self.page)
        self.slider.blockSignals(False)
        self.page_label.setText(self._page_text(self.page))

    # ---------- geometry ----------
    def _page_size(self, i: int) -> tuple[float, float]:
        if i not in self._sizes:
            r = self.doc.load_page(i).rect
            self._sizes[i] = (max(r.width, 1), max(r.height, 1))
        return self._sizes[i]

    def _viewport(self) -> QRect:
        return self.rect()  # the side panel floats over the book; it never resizes it

    def _fit_scales(self, page: int):
        """The spread's pages (scaled to equal height) and the two fit scales."""
        pages = self._spread(page)
        sizes = [self._page_size(i) for i in pages]
        unit_h = max(h for _, h in sizes)
        units = [(i, w * unit_h / h) for i, (w, h) in zip(pages, sizes)]  # equal heights
        total_w = sum(u for _, u in units)
        vp = self._viewport()
        fit_page = max(min((vp.width() - 2 * MARGIN) / total_w, (vp.height() - 2 * MARGIN) / unit_h), 0.01)
        fit_width = max(vp.width() / total_w, 0.01)
        return units, unit_h, total_w, vp, fit_page, fit_width

    def _min_zoom(self) -> float:
        """Zooming out stops at the fit you chose: the whole page in "Whole page",
        the full screen width in "Full width"."""
        return 1.0

    def _metrics(self, page: int) -> Metrics:
        units, unit_h, total_w, vp, fit_page, fit_width = self._fit_scales(page)
        # "Whole page" leaves a margin by making the page a bit smaller (fit_page); no extra
        # padding is added around it, so a zoomed page scrolls right up to its edges
        pad_x = pad_y = 0.0
        base = fit_width if self.fit == "width" else fit_page
        scale = base * self.zoom
        return Metrics(units, unit_h, scale, pad_x, pad_y,
                       total_w * scale + 2 * pad_x, unit_h * scale + 2 * pad_y, vp)

    def _overflow(self) -> tuple[float, float]:
        """How far the pages can be scrolled sideways / up-down."""
        if not self.doc:
            return 0.0, 0.0
        m = self._metrics(self.page)
        return max(0.0, m.content_w - m.vp.width()), max(0.0, m.content_h - m.vp.height())

    def _bounds(self, m: Metrics) -> list[tuple[float, float]]:
        """Allowed (lowest, highest) scroll offset on each axis.

        Bigger than the screen: scroll edge to edge, never past the page's edge (no empty
        gaps). Smaller than the screen: centred at 100%; zoomed in, it may sit anywhere it
        still fits on screen, so it stays where you pinched instead of jumping to the middle."""
        out = []
        for content, view in ((m.content_w, m.vp.width()), (m.content_h, m.vp.height())):
            if content >= view:
                out.append((0.0, content - view))
            elif self.zoom > 1.0005:
                out.append((-(view - content), 0.0))
            else:
                centre = -(view - content) / 2
                out.append((centre, centre))
        return out

    def _clamp(self):
        if self._free or not self.doc:  # mid-pinch the page may sit anywhere; it settles after
            return
        (x0, x1), (y0, y1) = self._bounds(self._metrics(self.page))
        self._ox = min(max(self._ox, x0), x1)
        self._oy = min(max(self._oy, y0), y1)

    def _origin(self, page: int, ox: float | None = None, oy: float | None = None,
                m: Metrics | None = None) -> QPointF:
        """Exact top-left corner of the first visible page on screen."""
        m = m or self._metrics(page)
        ox = self._ox if ox is None else ox
        oy = self._oy if oy is None else oy
        return QPointF(m.vp.x() - ox + m.pad_x, m.vp.y() - oy + m.pad_y)

    # A pinch moves the page freely (even off-centre) so the spot between the fingers
    # stays under them; when the fingers lift it glides back into a tidy position.
    def _enter_free(self):
        if self._free:
            self._free_anim.stop()
            return
        o, m = self._origin(self.page), self._metrics(self.page)
        self._ox = m.vp.x() + m.pad_x - o.x()
        self._oy = m.vp.y() + m.pad_y - o.y()
        self._free = True

    def _leave_free(self):
        if not self._free:
            return
        start = (self._ox, self._oy)
        self._free = False
        self._clamp()
        o, m = self._origin(self.page), self._metrics(self.page)
        target = (m.vp.x() + m.pad_x - o.x(), m.vp.y() + m.pad_y - o.y())
        self._free = True
        self._free_path = (start, target)
        self._free_anim.stop()
        self._free_anim.start()

    def _free_step(self, t):
        (ax, ay), (bx, by) = self._free_path
        t = float(t)
        self._ox, self._oy = ax + (bx - ax) * t, ay + (by - ay) * t
        self._touch()
        self.update()

    def _free_done(self):
        self._free = False
        self._clamp()
        self._update_cursor()
        self.update()

    def _centre_view(self):
        """Centred sideways, at the top."""
        m = self._metrics(self.page)
        self._ox = (m.content_w - m.vp.width()) / 2
        self._oy = 0.0 if m.content_h > m.vp.height() else (m.content_h - m.vp.height()) / 2
        self._clamp()

    def _cancel_free(self):
        self._free_anim.stop()
        self._free = False

    def _start_pinch(self, anchor: QPointF):
        """Remember which spot of the page is under the fingers (or pointer)."""
        self._zoom_timer.stop()
        self._enter_free()
        o, m = self._origin(self.page), self._metrics(self.page)
        self._zoom_spot = ((anchor.x() - o.x()) / m.scale, (anchor.y() - o.y()) / m.scale)
        self._hold_next = True
        self._log("pinch start", anchor, "spot", self._zoom_spot, "zoom", self.zoom)

    def _pinch_to(self, zoom: float, anchor: QPointF):
        """Zoom so the remembered spot sits under `anchor` (the fingers' midpoint)."""
        self._hold_next = True
        self._apply_zoom(zoom, anchor, force=True)

    def _end_pinch(self):
        if self._zoom_timer.isActive():  # let a gliding zoom finish first
            self._pinch_end_timer.start()
            return
        self._log("pinch end", "zoom", self.zoom)
        self._zoom_spot = None
        self._hold_next = False
        self._leave_free()

    def _layout(self, page: int, ox: float | None = None, oy: float | None = None) -> list[tuple[int, QRect]]:
        """Where each visible page goes on screen."""
        m = self._metrics(page)
        o = self._origin(page, ox, oy, m)
        x, y = o.x(), o.y()
        h = m.unit_h * m.scale
        out = []
        for i, u in m.units:
            left, right = round(x), round(x + u * m.scale)
            out.append((i, QRect(left, round(y), right - left, round(h))))
            x += u * m.scale
        return out

    # ---------- rendering ----------
    def _clear_cache(self):
        self._cache.clear()
        self._cache_bytes = 0
        self._thumbs.clear()

    def _cache_put(self, key, pix: QPixmap) -> QPixmap:
        self._cache[key] = pix
        self._cache_bytes += pix.width() * pix.height() * 4
        while self._cache_bytes > CACHE_BYTES and len(self._cache) > 1:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.width() * old.height() * 4
        return pix

    def _cache_get(self, key) -> QPixmap | None:
        pix = self._cache.get(key)
        if pix is not None:
            self._cache.move_to_end(key)
        return pix

    def _best_full(self, i: int) -> QPixmap | None:
        best = None
        for key, pix in self._cache.items():
            if key[0] == "full" and key[1] == i and (best is None or pix.width() > best.width()):
                best = pix
        return best

    def _to_qpixmap(self, pix: "pymupdf.Pixmap") -> QPixmap:
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        qpix = QPixmap.fromImage(img)
        qpix.setDevicePixelRatio(self.devicePixelRatioF())
        return qpix

    def _render_full(self, i: int, width_px: int) -> QPixmap:
        key = ("full", i, width_px)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        page = self.doc.load_page(i)
        zoom = width_px / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        self._tint(pix)
        return self._cache_put(key, self._to_qpixmap(pix))

    def _render_clip(self, i: int, rect: QRect, vis: QRect) -> QPixmap:
        """Sharp render of only the part of a (very zoomed) page that is on screen."""
        dpr = self.devicePixelRatioF()
        key = ("clip", i, round(rect.width() * dpr), vis.x() - rect.x(), vis.y() - rect.y(),
               vis.width(), vis.height())
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        page = self.doc.load_page(i)
        k = page.rect.width / rect.width()  # page points per screen pixel
        clip = pymupdf.Rect((vis.x() - rect.x()) * k, (vis.y() - rect.y()) * k,
                            (vis.right() + 1 - rect.x()) * k, (vis.bottom() + 1 - rect.y()) * k)
        zoom = rect.width() * dpr / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        self._tint(pix)
        return self._cache_put(key, self._to_qpixmap(pix))

    def _draw_page(self, p: QPainter, i: int, rect: QRect, exact: bool):
        try:
            self._draw_page_pixels(p, i, rect, exact)
        except Exception:  # e.g. the book file was moved or deleted while it was open
            p.fillRect(rect, QColor(self._theme.paper))

    def _draw_page_pixels(self, p: QPainter, i: int, rect: QRect, exact: bool):
        dpr = self.devicePixelRatioF()
        fw, fh = round(rect.width() * dpr), round(rect.height() * dpr)
        fits = fw * fh <= MAX_FULL_PIXELS
        full = self._cache_get(("full", i, fw))
        if full is not None:
            p.drawPixmap(rect, full)
            return
        if exact and fits:
            p.drawPixmap(rect, self._render_full(i, fw))
            return
        # mid-gesture, or too big to render whole: stretch what we have
        base = self._best_full(i)
        if base is None:
            cap = fw if fits else max(1, int((MAX_FULL_PIXELS * fw / fh) ** 0.5))
            base = self._render_full(i, cap)
        p.drawPixmap(rect, base)
        if exact and not fits:
            vis = rect.intersected(self.rect())
            if not vis.isEmpty():
                p.drawPixmap(vis, self._render_clip(i, rect, vis))

    @staticmethod
    def _paint_shadow(p: QPainter, r: QRect, size: int = 22):
        """Soft shadow around the pages, drawn as four gradient strips (cheap enough
        to redraw on every frame while zooming)."""
        dark, clear = QColor(0, 0, 0, 26 if theme.MODE == "light" else 110), QColor(0, 0, 0, 0)
        sides = (
            (QRect(r.left(), r.top() - size, r.width(), size), (0, r.top(), 0, r.top() - size)),
            (QRect(r.left(), r.bottom() + 1, r.width(), size + 4), (0, r.bottom() + 1, 0, r.bottom() + size + 5)),
            (QRect(r.left() - size, r.top(), size, r.height()), (r.left(), 0, r.left() - size, 0)),
            (QRect(r.right() + 1, r.top(), size, r.height()), (r.right() + 1, 0, r.right() + size + 1, 0)),
        )
        for rect, (x1, y1, x2, y2) in sides:
            grad = QLinearGradient(x1, y1, x2, y2)
            grad.setColorAt(0.0, dark)
            grad.setColorAt(0.35, QColor(0, 0, 0, 40))
            grad.setColorAt(1.0, clear)
            p.fillRect(rect, grad)

    def _paint_pages(self, p: QPainter, page: int, exact: bool):
        layout = self._layout(page)
        whole = layout[0][1].united(layout[-1][1])
        p.save()
        p.setClipRect(self._viewport())  # never slide under the bars
        self._paint_shadow(p, whole)
        for i, rect in layout:
            self._draw_page(p, i, rect, exact)
            self._paint_annots(p, i, rect)
        if len(layout) == 2:  # spine shading between the two pages
            spine = layout[0][1].right() + 1
            half = max(12, round(layout[0][1].width() * 0.05))
            grad = QLinearGradient(spine - half, 0, spine + half, 0)
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            grad.setColorAt(0.5, QColor(0, 0, 0, 45))
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(QRect(spine - half, whole.top(), 2 * half, whole.height()), grad)
        p.restore()

    def _thumb(self, i: int) -> QPixmap:
        """A tiny copy of a page (in the current page colour), for the glass blur."""
        if i not in self._thumbs:
            full = self._best_full(i)
            if full is not None:  # shrink the page already on screen: no second render
                thumb = full.scaledToWidth(120, Qt.TransformationMode.SmoothTransformation)
            else:
                page = self.doc.load_page(i)
                zoom = 120 / page.rect.width
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                self._tint(pix)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
                thumb = QPixmap.fromImage(img.copy())
            self._thumbs[i] = thumb
            while len(self._thumbs) > MAX_THUMBS:  # only pages near the current one are needed
                del self._thumbs[next(iter(self._thumbs))]
        return self._thumbs[i]

    def _render_blur(self, rect: QRect) -> QPixmap:
        """What's behind `rect`, drawn tiny and softened; stretched back up by the panel
        it looks like frosted glass. Cheap enough for every frame."""
        w = max(1, round(rect.width() * BLUR_SCALE))
        h = max(1, round(rect.height() * BLUR_SCALE))
        small = QImage(w, h, QImage.Format.Format_RGB32)
        small.fill(QColor(self._theme.backdrop or theme.READER_BG))
        p = QPainter(small)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.scale(BLUR_SCALE, BLUR_SCALE)
        p.translate(-rect.x(), -rect.y())
        for i, page_rect in self._layout(self.page):
            if page_rect.intersects(rect):
                p.drawPixmap(page_rect, self._thumb(i))  # a blur needs no detail
        p.end()
        # one more soften pass: shrink a little further and grow back
        soft = small.scaled(max(1, w // 2), max(1, h // 2), Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        soft = soft.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return QPixmap.fromImage(soft)

    def _snapshot(self) -> QPixmap | None:
        if not self.doc or self.width() < 10:
            return None
        dpr = self.devicePixelRatioF()
        frame = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        frame.setDevicePixelRatio(dpr)
        frame.fill(QColor(self._theme.backdrop or theme.READER_BG))
        p = QPainter(frame)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._paint_pages(p, self.page, exact=False)
        p.end()
        return frame

    def _set_fade(self, value):
        self._fade = float(value)
        if self._fade <= 0.0:
            self._old_frame = None
        self.update()

    def _touch(self):
        """Something is moving (zoom, pan, resize): draw quickly now, sharply once it stops."""
        self._interacting = True
        self._settle_timer.start()

    def _settle(self):
        self._interacting = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(self._theme.backdrop or theme.READER_BG))
        if not self.doc:
            return
        self._paint_pages(p, self.page, exact=not self._interacting)
        if self.panel.isVisible():
            self._panel_blur = self._render_blur(self.panel.geometry())
        if self._old_frame is not None and self._fade > 0:
            p.setOpacity(self._fade)
            p.drawPixmap(0, 0, self._old_frame)
            p.setOpacity(1.0)
        st = self._stroke
        if st is not None and st["type"] == "eraser":
            c, r = st["screen"], ERASER_RADIUS
            p.setBrush(QColor(255, 255, 255, 40))
            p.setPen(QPen(QColor(0, 0, 0, 140), 3))
            p.drawEllipse(c, r, r)
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.4))
            p.drawEllipse(c, r, r)
        p.end()
        if not self._interacting:
            self._prefetch_timer.start()

    def _prefetch(self):
        """Render the neighbouring spreads so turning pages is instant. One page per call:
        a scanned page can take a fifth of a second, and a key press or swipe shouldn't
        wait behind several of them."""
        if not self.doc or self._interacting:
            return
        dpr = self.devicePixelRatioF()
        for page in (self.page + self.mode, self.page - self.mode):
            if 0 <= page < self.count:
                for i, rect in self._layout(page, oy=0):
                    fw, fh = round(rect.width() * dpr), round(rect.height() * dpr)
                    if fw * fh <= MAX_FULL_PIXELS and ("full", i, fw) not in self._cache:
                        try:
                            self._render_full(i, fw)
                        except Exception:  # unreadable page: it is shown blank when turned to
                            return
                        QTimer.singleShot(0, self._prefetch)  # the next one, after waiting events
                        return

    def resizeEvent(self, event):
        self._cancel_free()
        self._place_overlays()
        self._old_frame = None
        self._clamp()
        self._touch()
        super().resizeEvent(event)

    # ---------- input ----------
    # Pages only turn on a sideways swipe (touchpad or touch screen), the arrow keys,
    # or the panel buttons. Scrolling, pinching and clicking never turn a page.

    def keyPressEvent(self, event):
        key = event.key()
        K = Qt.Key
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        vh = self._viewport().height()
        if ctrl and event.modifiers() & Qt.KeyboardModifier.ShiftModifier and key == K.Key_G:
            self._toggle_log()
        elif ctrl and key == K.Key_Z:
            self.redo() if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else self.undo()
        elif ctrl and key == K.Key_Y:
            self.redo()
        elif ctrl and key in (K.Key_Plus, K.Key_Equal):
            self.set_zoom(self._zoom_target_now() * 1.25, animate=True)
        elif ctrl and key == K.Key_Minus:
            self.set_zoom(self._zoom_target_now() / 1.25, animate=True)
        elif ctrl and key == K.Key_0:
            self.set_zoom(1.0, animate=True)
        elif key == K.Key_Down:
            self._pan(0, vh * 0.15)
        elif key == K.Key_Up:
            self._pan(0, -vh * 0.15)
        elif key in (K.Key_Right, K.Key_PageDown, K.Key_Space):
            self.next_page()
        elif key in (K.Key_Left, K.Key_PageUp, K.Key_Backspace):
            self.prev_page()
        elif key == K.Key_Home:
            self.go_to(0)
        elif key == K.Key_End:
            self.go_to(self.count - 1)
        elif key == K.Key_1:
            self.set_mode(1)
        elif key == K.Key_2:
            self.set_mode(2)
        elif key == K.Key_W:
            self.set_fit("page" if self.fit == "width" else "width")
        elif key in (K.Key_H, K.Key_Tab):
            self.toggle_controls()
        elif key in (K.Key_F11, K.Key_F):
            self.fullscreen_requested.emit()
        elif key == K.Key_Escape:
            if self.tools.is_open:
                self.tools.close()  # first Esc closes the tools, the next one leaves the book
            else:
                self.close_requested.emit()
        else:
            super().keyPressEvent(event)

    def focusNextPrevChild(self, next):
        return False  # keep Tab for toggling the panel

    def wheelEvent(self, event):
        """Touchpad: two-finger scroll moves the page, pinch zooms (Windows sends it as
        Ctrl + wheel), and a sideways swipe past the page edge turns the page."""
        event.accept()
        self._log("wheel", event.position(), event.angleDelta().x(), event.angleDelta().y(),
                  event.modifiers().value, event.device().type().name)
        self._wake()
        now = time.monotonic()
        delta = event.angleDelta()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._log("ctrl+wheel", event.position(), delta.y(), delta.x(),
                      event.device().type().name if event.device() else "?")
            if now > self._pinch_until or self._pinch_anchor is None or self._zoom_spot is None:
                self._pinch_anchor = event.position()  # a new pinch: lock the zoom point
                self._start_pinch(self._pinch_anchor)
            self._pinch_end_timer.start()
            self._pinch_until = now + PINCH_TAIL
            self._swipe = None
            step = delta.y() or delta.x()
            if abs(step) >= 120:  # a mouse-wheel notch: glide to the next size
                self.set_zoom(self._zoom_target_now() * 1.0015 ** step, self._pinch_anchor,
                              animate=True, hold=True)
            else:  # touchpad pinch: many tiny steps, follow them exactly
                self.set_zoom(self._zoom_target_now() * 1.0015 ** step, self._pinch_anchor, hold=True)
            return
        if now < self._pinch_until:
            return  # leftover motion from a pinch: ignore so nothing jumps
        dx, dy = delta.x(), delta.y()
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and not dx:
            dx, dy = dy, 0
        want_x = -dx / 120 * SCROLL_STEP
        want_y = -dy / 120 * SCROLL_STEP
        # one gesture = a stream of wheel events with no long pause in between.
        # Swipes only turn pages when the whole page width is on screen; when zoomed
        # in they just move around the page, even at its edges.
        self._swipe_timer.start()
        if self._swipe is None:
            self._swipe = {"x": 0.0, "ax": 0.0, "ay": 0.0, "done": False,
                           "can_turn": self._can_swipe()}
        g = self._swipe
        self._pan(want_x, want_y)
        g["x"] += want_x
        g["ax"] += abs(dx)
        g["ay"] += abs(dy)
        if g["can_turn"] and not g["done"] and abs(g["x"]) >= WHEEL_SWIPE and g["ax"] > 2 * g["ay"]:
            g["done"] = True  # one page per swipe, however long the swipe lasts
            if g["x"] > 0:
                self.next_page()
            else:
                self.prev_page()

    def event(self, event):
        t = event.type()
        if t in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate,
                 QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
            device = event.device()
            if device is not None and device.type() == QInputDevice.DeviceType.TouchPad:
                self._log("touchpad-touch ignored", t.name, *[self._local(p) for p in event.points()])
                return super().event(event)
            self._log("touch", t.name, *[(p.id(), p.state().name, self._local(p)) for p in event.points()])
            self._touch_event(event)
            event.accept()
            return True
        if t == QEvent.Type.NativeGesture:
            kind = event.gestureType()
            self._log("native", kind.name, event.position(), event.value())
            if kind == Qt.NativeGestureType.ZoomNativeGesture:
                if self._zoom_spot is None:
                    self._start_pinch(event.position())
                self._pinch_until = time.monotonic() + PINCH_TAIL
                self._pinch_end_timer.start()
                self._pinch_to(self.zoom * (1 + event.value()), event.position())
                return True
            if kind == Qt.NativeGestureType.EndNativeGesture and self._zoom_spot is not None:
                self._end_pinch()
                return True
        return super().event(event)

    def _toggle_log(self):
        self._log_file = None if self._log_file else library.APP_DIR / "gesture_log.txt"
        if self._log_file:
            self._log_file.write_text("", encoding="utf-8")
            self._log("log started", "screen dpr", self.devicePixelRatioF(), "size", self.size())
        self._show_toast("Gesture log on" if self._log_file else "Gesture log saved")

    def _log(self, *parts):
        if not self._log_file:
            return
        def fmt(v):
            if isinstance(v, QPointF):
                return f"({v.x():.1f},{v.y():.1f})"
            if isinstance(v, float):
                return f"{v:.3f}"
            if isinstance(v, tuple):
                return "(" + ",".join(fmt(x) for x in v) + ")"
            return str(v)
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(f"{time.monotonic():.3f} " + " ".join(fmt(p) for p in parts) + "\n")

    def _local(self, point: QEventPoint) -> QPointF:
        """A touch point in this widget's coordinates (from its screen position)."""
        return self.mapFromGlobal(point.globalPosition())

    def _touch_event(self, event):
        """Touch screen: one finger drags the page or swipes sideways to turn it
        (only when the whole page width is visible),
        two fingers pinch to zoom. A tap shows the ☰ button."""
        t = event.type()
        if t == QEvent.Type.TouchCancel:
            if self._touch_state and self._touch_state["n"] >= 2:
                self._end_pinch()
            self._touch_state = None
            return
        pts = [p for p in event.points() if p.state() != QEventPoint.State.Released]
        if t == QEvent.Type.TouchBegin:
            self._touch_state = {"n": 0, "last": None, "dist": None, "dx": 0.0, "dy": 0.0,
                                 "pinched": False, "can_turn": self._can_swipe(), "draw": False}
            if self.tools.tool and len(pts) == 1:
                self._touch_state["draw"] = self._tool_press(self._local(pts[0]))
        s = self._touch_state
        if s is None:
            return
        if s["draw"]:
            if t == QEvent.Type.TouchEnd or not pts:
                self._tool_release()
                self._touch_state = None
                return
            if len(pts) == 1:
                self._tool_move(self._local(pts[0]))
                return
            self._cancel_stroke()  # a second finger: it's a pinch, not a drawing
            s["draw"] = False
        if t == QEvent.Type.TouchEnd or not pts:
            if s["n"] >= 2:
                self._end_pinch()
            if not s["pinched"]:
                if abs(s["dx"]) + abs(s["dy"]) < 12:
                    self._wake()  # a tap
                elif s["can_turn"] and abs(s["dx"]) >= TOUCH_SWIPE and abs(s["dx"]) > 1.5 * abs(s["dy"]):
                    self.next_page() if s["dx"] < 0 else self.prev_page()
            self._touch_state = None
            return

        center = QPointF(sum(self._local(p).x() for p in pts) / len(pts),
                         sum(self._local(p).y() for p in pts) / len(pts))
        dist = None
        if len(pts) >= 2:
            a, b = self._local(pts[0]), self._local(pts[1])
            dist = max(math.hypot(a.x() - b.x(), a.y() - b.y()), 1.0)
        if len(pts) != s["n"]:  # finger added or lifted: restart from here, no jump
            if s["n"] >= 2 and len(pts) < 2:
                self._end_pinch()
            s.update(n=len(pts), last=center, dist=dist)
            if len(pts) >= 2:
                s["pinched"] = True
                self._start_pinch(center)
            return

        move = center - s["last"]
        s["last"] = center
        if dist is not None:  # pinch: the spot between the fingers stays between them
            self._pinch_until = time.monotonic() + PINCH_TAIL
            self._pinch_to(self.zoom * dist / s["dist"], center)
            s["dist"] = dist
            return
        s["dx"] += move.x()
        s["dy"] += move.y()
        self._pan(-move.x(), -move.y())

    @staticmethod
    def _from_touch(event) -> bool:
        """Windows also turns touch-screen fingers into a pretend mouse. The fingers are
        already handled as touches, so that copy must be ignored, or it drags the page
        (and draws) while you pinch. A pen or a real mouse is still used."""
        device = event.device()
        return (event.source() != Qt.MouseEventSource.MouseEventNotSynthesized
                and device is not None and device.type() == QInputDevice.DeviceType.TouchScreen)

    def mouseMoveEvent(self, event):
        self._log("mouse move", event.position(), event.source().name, event.device().type().name,
                  int(event.buttons().value))
        if self._from_touch(event):
            self._wake()
            return
        if self._mouse_drawing:
            self._tool_move(event.position())
            self._wake()
            return
        if self._drag and event.buttons() & Qt.MouseButton.LeftButton:
            start, ox, oy = self._drag
            d = event.position() - start
            if not self._dragging and d.manhattanLength() > 6 and any(self._overflow()):
                self._dragging = True
            if self._dragging:
                self._pan(ox - d.x() - self._ox, oy - d.y() - self._oy)
        self._update_cursor()
        self._wake()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        self._log("mouse press", event.position(), event.source().name, event.device().type().name)
        self.setFocus()
        if self._from_touch(event):
            return
        if event.button() == Qt.MouseButton.LeftButton and self.tools.tool:
            self._mouse_drawing = self._tool_press(event.position())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.position(), self._ox, self._oy)
            self._dragging = False
        self.setFocus()

    def mouseReleaseEvent(self, event):
        if self._from_touch(event):
            return
        if event.button() == Qt.MouseButton.LeftButton and self._mouse_drawing:
            self._mouse_drawing = False
            self._tool_release()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self._dragging = False
            self._update_cursor()

    # ---------- highlights and drawing ----------
    def _on_tool_changed(self, tool):
        self._cancel_stroke()
        self._update_cursor()
        self.setFocus()

    @staticmethod
    def _make_eraser_cursor() -> QCursor:
        size, dpr = ERASER_RADIUS * 2 + 4, 2
        pm = QPixmap(size * dpr, size * dpr)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(0, 0, 0, 160), 3))
        p.drawEllipse(QRectF(2, 2, size - 4, size - 4))
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.4))
        p.drawEllipse(QRectF(2, 2, size - 4, size - 4))
        p.end()
        return QCursor(pm, size // 2, size // 2)

    def _hit_page(self, pos: QPointF):
        """The page under a screen point: (index, rect on screen) or None."""
        pt = pos.toPoint()
        if not self.doc or not self._viewport().contains(pt):
            return None
        for i, rect in self._layout(self.page):
            if rect.contains(pt):
                return i, rect
        return None

    def _to_page(self, i: int, rect: QRect, pos: QPointF) -> tuple[float, float, float]:
        """Screen point -> page coordinates (clamped to the page), plus pixels per point."""
        w, h = self._page_size(i)
        scale = rect.width() / w
        x = min(max((pos.x() - rect.x()) / scale, 0.0), w)
        y = min(max((pos.y() - rect.y()) / scale, 0.0), h)
        return x, y, scale

    def _tool_press(self, pos: QPointF) -> bool:
        hit = self._hit_page(pos)
        if hit is None or self.annots is None:
            return False
        i, rect = hit
        x, y, scale = self._to_page(i, rect, pos)
        tool = self.tools.tool
        if tool == "eraser":
            self._stroke = {"type": "eraser", "page": i, "removed": [], "added": [],
                            "last": (x, y), "screen": pos}
            self._erase_at(i, x, y, scale)
        elif tool == "pen":
            self._stroke = {"type": "ink", "page": i, "color": self.tools.pen_color,
                            "width": self.tools.pen_width, "points": [[x, y]]}
        else:  # highlighter: freehand, with a little help staying straight
            self._stroke = {"type": "marker", "page": i, "color": self.tools.highlight_color,
                            "width": self.tools.highlight_width, "points": [[x, y]],
                            "assist": True, "brush": (x, y), "finger": (x, y), "scale": scale}
        self.update()
        return True

    def _tool_move(self, pos: QPointF):
        st = self._stroke
        if st is None:
            return
        rect = next((r for i, r in self._layout(self.page) if i == st["page"]), None)
        if rect is None:
            return
        x, y, scale = self._to_page(st["page"], rect, pos)
        if st["type"] == "eraser":
            st["screen"] = pos
            self.update()
            # check along the whole way from the last point, so fast swipes don't skip marks
            lx, ly = st.get("last", (x, y))
            step = ERASER_RADIUS / scale
            n = max(1, int(math.hypot(x - lx, y - ly) / step))
            for k in range(1, n + 1):
                self._erase_at(st["page"], lx + (x - lx) * k / n, ly + (y - ly) * k / n, scale)
            st["last"] = (x, y)
            return
        if st.get("assist"):
            # steadying: the line trails the finger on a short "string", so small wobbles
            # are smoothed away while real curves still come through
            st["finger"], st["scale"] = (x, y), scale
            bx, by = st["brush"]
            reach = STEADY_RADIUS / scale
            d = math.hypot(x - bx, y - by)
            if d <= reach:
                return
            bx, by = bx + (x - bx) * (d - reach) / d, by + (y - by) * (d - reach) / d
            st["brush"] = (bx, by)
            lx, ly = st["points"][-1]
            if math.hypot(bx - lx, by - ly) * scale >= 1.5:
                st["points"].append([bx, by])
                self.update()
            return
        lx, ly = st["points"][-1]
        if math.hypot(x - lx, y - ly) * scale < 1.5:  # ignore sub-pixel jitter
            return
        st["points"].append([x, y])
        self.update()

    def _tool_release(self):
        st, self._stroke = self._stroke, None
        if st is None or self.annots is None:
            return
        if st["type"] == "eraser":
            self.annots.record_change(st["removed"], st["added"])  # the whole drag = one undo step
            self._annots_changed()
            return
        if st.pop("assist", False):
            scale = st.pop("scale")
            fx, fy = st.pop("finger")
            st.pop("brush")
            pts = st["points"]
            if math.hypot(fx - pts[-1][0], fy - pts[-1][1]) * scale > 1:
                pts.append([fx, fy])  # finish exactly where the finger lifted
            if len(pts) < 2 or math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) * scale < 3:
                self.update()  # just a tap: nothing to highlight
                return
            st["points"] = self._steady_line(pts, scale)
        self.annots.add(st)
        self._annots_changed()

    @staticmethod
    def _steady_line(pts: list, scale: float) -> list:
        """Nearly straight strokes become perfectly straight (and level if nearly level);
        curvy ones are kept, just smoothed."""
        (ax, ay), (bx, by) = pts[0], pts[-1]
        length = math.hypot(bx - ax, by - ay)
        if length * scale >= 12:
            wobble = max(abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / length for px, py in pts)
            if wobble * scale <= max(STRAIGHT_PX, 0.05 * length * scale):
                if abs(by - ay) <= abs(bx - ax) * STRAIGHT_SNAP:
                    by = ay
                elif abs(bx - ax) <= abs(by - ay) * STRAIGHT_SNAP:
                    bx = ax
                return [[ax, ay], [bx, by]]
        for _ in range(2):  # Chaikin smoothing, ends kept in place
            out = [pts[0]]
            for (px, py), (qx, qy) in zip(pts, pts[1:]):
                out += [[0.75 * px + 0.25 * qx, 0.75 * py + 0.25 * qy],
                        [0.25 * px + 0.75 * qx, 0.25 * py + 0.75 * qy]]
            out.append(pts[-1])
            pts = out
        return pts

    def _cancel_stroke(self):
        if self._stroke and self._stroke["type"] == "eraser" and self.annots is not None:
            self.annots.record_change(self._stroke["removed"], self._stroke["added"])
            self._annots_changed()
        self._stroke = None
        self._mouse_drawing = False
        self.update()

    def _erase_at(self, i: int, x: float, y: float, scale: float):
        st = self._stroke
        changed = False
        for it in self.annots.on_page(i):
            pieces = cut(it, x, y, ERASER_RADIUS / scale)
            if pieces is None:
                continue
            changed = True
            self.annots.discard([it])
            self.annots.insert(pieces)
            if any(it is a for a in st["added"]):  # a piece cut earlier in this same drag
                st["added"] = [a for a in st["added"] if a is not it]
            else:
                st["removed"].append(it)
            st["added"].extend(pieces)
        if changed:
            self.update()

    def _annots_changed(self):
        self.tools.set_history(self.annots.can_undo, self.annots.can_redo)
        self._annot_timer.start()
        self.update()

    def _save_annots(self):
        self._annot_timer.stop()
        if self.annots is not None:
            try:
                self.annots.save()
            except OSError:
                pass

    def _after_history(self, items: list[dict], label: str):
        if not items:
            return
        self._annots_changed()
        page = items[0]["page"]
        if page not in self._spread(self.page):
            self.go_to(page)  # show what just changed
        self._show_toast(label)

    def undo(self):
        if self.annots:
            self._cancel_stroke()
            self._after_history(self.annots.undo(), "Undone")

    def redo(self):
        if self.annots:
            self._cancel_stroke()
            self._after_history(self.annots.redo(), "Redone")

    def clear_spread(self):
        """Remove every highlight and drawing on the pages on screen (undoable)."""
        if not self.annots:
            return
        items = [it for i in self._spread(self.page) for it in self.annots.on_page(i)]
        if items:
            self.annots.remove(items)
            self._annots_changed()
            self._show_toast("Page cleared  ·  Ctrl Z to undo")

    def _path(self, item: dict, live: bool = False) -> QPainterPath:
        if not live and item.get("id") in self._paths:
            return self._paths[item["id"]]
        pts = item["points"]
        path = QPainterPath(QPointF(*pts[0]))
        if len(pts) == 1:
            path.lineTo(pts[0][0] + 0.01, pts[0][1])  # a dot
        for a, b in zip(pts[1:-1], pts[2:]):  # smooth curve through the midpoints
            path.quadTo(QPointF(*a), QPointF((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
        if len(pts) > 1:
            path.lineTo(QPointF(*pts[-1]))
        if not live:
            self._paths[item["id"]] = path
        return path

    def _paint_annots(self, p: QPainter, i: int, rect: QRect):
        items = self.annots.on_page(i) if self.annots else []
        live = self._stroke if self._stroke and self._stroke["page"] == i and self._stroke["type"] != "eraser" else None
        if not items and live is None:
            return
        w, _ = self._page_size(i)
        light = QColor(self._theme.paper).lightnessF() > 0.5
        p.save()
        p.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        p.translate(rect.x(), rect.y())
        p.scale(rect.width() / w, rect.width() / w)
        for it in items:
            self._draw_annot(p, it, light)
        if live is not None:
            self._draw_annot(p, live, light, live=True)
        p.restore()

    def _draw_annot(self, p: QPainter, it: dict, light: bool, live: bool = False):
        color = QColor(it["color"])
        kind = it["type"]
        if kind in ("highlight", "marker"):
            # like a real highlighter on light pages; see-through colour on dark ones
            if light:
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            else:
                color.setAlphaF(0.42)
            if kind == "highlight":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                for x0, y0, x1, y1 in it["rects"]:
                    p.drawRoundedRect(QRectF(x0 - 1, y0, x1 - x0 + 2, y1 - y0), 2, 2)
            else:
                cap = Qt.PenCapStyle.FlatCap if len(it["points"]) == 2 else Qt.PenCapStyle.RoundCap
                p.setPen(QPen(color, it["width"], Qt.PenStyle.SolidLine, cap, Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(self._path(it, live))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        else:
            p.setPen(QPen(color, it["width"], Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._path(it, live))
