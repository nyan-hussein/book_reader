"""Colors, stylesheet and icon helpers shared across the app.

The app has two looks, "dark" and "light". The current colours live in this
module's globals (theme.TEXT, theme.BG, ...) and are swapped by apply()."""
import os
import sys
from typing import Callable, NamedTuple

import shiboken6
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap,
)

WINDOWS = sys.platform == "win32"

PALETTES = {
    "dark": {
        "BG": "#1a1b20", "SURFACE": "#24262d", "SURFACE_2": "#2c2f37", "BORDER": "#3a3e4a",
        "TEXT": "#eceef3", "MUTED": "#a0a6b4", "FAINT": "#b3d7d9df", "DISABLED": "#5d6270",
        "ACCENT": "#7aa2ff", "ACCENT_HOVER": "#94b5ff", "ACCENT_PRESSED": "#6a8fe6",
        "ON_ACCENT": "#0b1020", "READER_BG": "#202127",
        "OVERLAY": "rgba(38, 40, 48, 0.97)", "OVERLAY_BORDER": "rgba(255, 255, 255, 0.08)",
        "PANEL": "rgba(38, 40, 48, 0.80)",
        "PANEL_TINT": "#bc24262d", "PANEL_EDGE": "#22ffffff",
        "HOVER": "rgba(255, 255, 255, 0.09)", "PRESSED": "rgba(255, 255, 255, 0.05)",
        "SUBTLE": "rgba(255, 255, 255, 0.06)", "DIVIDER": "rgba(255, 255, 255, 0.10)",
        "GLASS": "rgba(110, 115, 130, 0.30)", "GLASS_HOVER": "rgba(120, 125, 140, 0.65)",
        "GLASS_BORDER": "rgba(255, 255, 255, 0.10)",
        "SCROLL": "#474b58", "SCROLL_HOVER": "#5a5f6e", "MENU_SEL": "#3a3f4e",
        "GROOVE": "rgba(255, 255, 255, 0.14)", "ACCENT_SOFT": "rgba(122, 162, 255, 0.24)",
        "DANGER": "#ff8a8a", "DANGER_BG": "rgba(255, 107, 107, 0.14)",
        "SWATCH_BORDER": "rgba(255, 255, 255, 0.18)", "SWATCH_HOVER": "rgba(255, 255, 255, 0.5)",
        # same idea for QPainter, which wants #AARRGGBB
        "CARD_TRACK": "#24ffffff", "CARD_EDGE": "#18ffffff", "CARD_SHADOW": "#14000000",
        "CARD_BLANK_1": "#3a4466", "CARD_BLANK_2": "#262a38",
        "SCHEME": "dark",
    },
    "light": {
        "BG": "#f4f5f8", "SURFACE": "#ffffff", "SURFACE_2": "#eceef3", "BORDER": "#d5d9e1",
        "TEXT": "#1c1f26", "MUTED": "#626977", "FAINT": "#b3262a33", "DISABLED": "#b4b9c4",
        "ACCENT": "#3d6be0", "ACCENT_HOVER": "#5580ea", "ACCENT_PRESSED": "#2f59c4",
        "ON_ACCENT": "#ffffff", "READER_BG": "#e2e4ea",
        "OVERLAY": "rgba(255, 255, 255, 0.97)", "OVERLAY_BORDER": "rgba(0, 0, 0, 0.09)",
        "PANEL": "rgba(255, 255, 255, 0.78)",
        "PANEL_TINT": "#b8f7f8fb", "PANEL_EDGE": "#1c000000",
        "HOVER": "rgba(0, 0, 0, 0.06)", "PRESSED": "rgba(0, 0, 0, 0.10)",
        "SUBTLE": "rgba(0, 0, 0, 0.05)", "DIVIDER": "rgba(0, 0, 0, 0.10)",
        "GLASS": "rgba(255, 255, 255, 0.45)", "GLASS_HOVER": "rgba(255, 255, 255, 0.90)",
        "GLASS_BORDER": "rgba(0, 0, 0, 0.08)",
        "SCROLL": "#c3c7d0", "SCROLL_HOVER": "#a9aeb9", "MENU_SEL": "#e4e8f2",
        "GROOVE": "rgba(0, 0, 0, 0.12)", "ACCENT_SOFT": "rgba(61, 107, 224, 0.16)",
        "DANGER": "#d64545", "DANGER_BG": "rgba(214, 69, 69, 0.10)",
        "SWATCH_BORDER": "rgba(0, 0, 0, 0.16)", "SWATCH_HOVER": "rgba(0, 0, 0, 0.45)",
        "CARD_TRACK": "#1a000000", "CARD_EDGE": "#14000000", "CARD_SHADOW": "#05000000",
        "CARD_BLANK_1": "#dfe4f2", "CARD_BLANK_2": "#c9d0e3",
        "SCHEME": "light",
    },
}

MODE = "dark"
# placeholders so editors know these names exist; set_mode() fills them in
BG = SURFACE = SURFACE_2 = BORDER = TEXT = MUTED = FAINT = DISABLED = ""
ACCENT = ACCENT_HOVER = ACCENT_PRESSED = ON_ACCENT = READER_BG = ""
CARD_TRACK = CARD_EDGE = CARD_SHADOW = CARD_BLANK_1 = CARD_BLANK_2 = ""
PANEL_TINT = PANEL_EDGE = ""


def set_mode(mode: str) -> None:
    global MODE
    MODE = mode if mode in PALETTES else "dark"
    globals().update(PALETTES[MODE])


set_mode("dark")


class PageTheme(NamedTuple):
    name: str
    paper: str      # what white on the page becomes
    ink: str        # what black on the page becomes
    backdrop: str | None = None  # the reader background around the page (None = app's)


# Reading colours. "original" leaves the book untouched.
PAGE_THEMES = {
    "original": PageTheme("Original", "#ffffff", "#000000"),
    "paper": PageTheme("Off-white", "#f8f3e8", "#22201c"),
    "sepia": PageTheme("Beige", "#efe0c2", "#4a3a26"),
    "grey": PageTheme("Grey", "#d2d3d5", "#1d1e20"),
    "sage": PageTheme("Green", "#d5e4cd", "#1f2b1f"),
    "dusk": PageTheme("Off-black", "#1f2024", "#cfd0d4", "#141418"),
    "night": PageTheme("Black", "#000000", "#b8b8b8", "#08080a"),
}


def custom_page_theme(paper: str, n: int) -> PageTheme:
    """A page colour the user picked: text is dark on light paper and light on dark paper."""
    light = QColor(paper).lightnessF() > 0.5
    return PageTheme(f"Custom {n}", paper, "#1e1e22" if light else "#d8d8dc")


# Icons: on Windows the built-in Segoe Fluent Icons font (MDL2 Assets on Windows 10, same
# code points). Elsewhere (Mac) the matching Phosphor icons that ship with QtAwesome.
ICON_FONTS = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]
FONT_ICONS = WINDOWS and os.environ.get("BOOKSHELF_ICONS") != "phosphor"


def ui_family() -> str:
    return "Segoe UI Variable Text" if WINDOWS else QFontDatabase.systemFont(
        QFontDatabase.SystemFont.GeneralFont).family()


def display_family() -> str:
    return "Segoe UI Variable Display" if WINDOWS else ui_family()


def font_size(points: float) -> str:
    """A stylesheet font size that looks the same everywhere (a Mac "point" is smaller)."""
    return f"{points}pt" if WINDOWS else f"{round(points * 4 / 3)}px"


def ui_font(points: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(ui_family())
    if WINDOWS:
        font.setPointSizeF(points)
    else:
        font.setPixelSize(round(points * 4 / 3))
    font.setWeight(weight)
    return font


class Glyph:
    BACK = ""
    ADD = ""
    LIBRARY = ""
    SINGLE_PAGE = ""
    TWO_PAGE = ""
    FULLSCREEN = ""
    EXIT_FULLSCREEN = ""
    FOLDER = ""
    DELETE = ""
    READ = ""
    SEARCH = ""
    HIDE = ""
    MENU = ""
    ZOOM_IN = ""
    ZOOM_OUT = ""
    CLOSE = ""
    ANNOTATE = ""
    HIGHLIGHT = ""
    PEN = ""
    ERASER = ""
    UNDO = ""
    REDO = ""
    PREV = ""
    NEXT = ""
    SUN = ""
    MOON = ""


PHOSPHOR = {  # the same icons for Mac
    Glyph.BACK: "arrow-left", Glyph.ADD: "plus", Glyph.LIBRARY: "books",
    Glyph.SINGLE_PAGE: "file", Glyph.TWO_PAGE: "book-open", Glyph.FULLSCREEN: "arrows-out",
    Glyph.EXIT_FULLSCREEN: "arrows-in", Glyph.FOLDER: "folder-open", Glyph.DELETE: "trash",
    Glyph.READ: "book-open", Glyph.SEARCH: "magnifying-glass", Glyph.HIDE: "caret-up",
    Glyph.MENU: "list", Glyph.ZOOM_IN: "magnifying-glass-plus", Glyph.ZOOM_OUT: "magnifying-glass-minus",
    Glyph.CLOSE: "x", Glyph.ANNOTATE: "pencil-line", Glyph.HIGHLIGHT: "highlighter-circle",
    Glyph.PEN: "pen-nib", Glyph.ERASER: "eraser", Glyph.UNDO: "arrow-counter-clockwise",
    Glyph.REDO: "arrow-clockwise", Glyph.PREV: "caret-left", Glyph.NEXT: "caret-right",
    Glyph.SUN: "sun", Glyph.MOON: "moon",
}


def _phosphor_pixmap(glyph: str, color: str, px: int) -> QPixmap:
    import qtawesome
    return qtawesome.icon(f"ph.{PHOSPHOR[glyph]}", color=QColor(color)).pixmap(px, px)


_icon_cache: dict[tuple, QIcon] = {}


def glyph_icon(glyph: str, color: str | None = None, size: int = 20) -> QIcon:
    """An icon from the Windows icon font, centred on its actual shape (the font's own
    metrics sit glyphs a little high)."""
    color = color or TEXT
    key = (glyph, color, size)
    if key in _icon_cache:
        return _icon_cache[key]
    if not FONT_ICONS:
        icon = QIcon()
        for scale in (1, 2):
            pm = _phosphor_pixmap(glyph, color, size * scale)
            pm.setDevicePixelRatio(scale)
            icon.addPixmap(pm)
        _icon_cache[key] = icon
        return icon
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    font = QFont()
    font.setFamilies(ICON_FONTS)
    font.setPixelSize(int(size * 0.8))
    path = QPainterPath()
    path.addText(QPointF(0, 0), font, glyph)
    box = path.boundingRect()
    path.translate(size / 2 - box.center().x(), size / 2 - box.center().y())
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillPath(path, QColor(color))
    p.end()
    icon = QIcon(pm)
    _icon_cache[key] = icon
    return icon


def app_icon_pixmap(size: int) -> QPixmap:
    """The Bookshelf app icon: a white bookshelf on a rounded blue tile."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QLinearGradient
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    tile = QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92)
    grad = QLinearGradient(tile.topLeft(), tile.bottomRight())
    grad.setColorAt(0, QColor("#8fb2ff"))
    grad.setColorAt(1, QColor("#4a6fe0"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(tile, size * 0.22, size * 0.22)
    if not FONT_ICONS:
        art = _phosphor_pixmap(Glyph.LIBRARY, "#ffffff", round(size * 0.64))
        p.drawPixmap(round((size - art.width()) / 2), round((size - art.height()) / 2), art)
        p.end()
        return pm
    font = QFont()
    font.setFamilies(ICON_FONTS)
    font.setPixelSize(int(size * 0.56))
    path = QPainterPath()
    path.addText(QPointF(0, 0), font, Glyph.LIBRARY)
    box = path.boundingRect()
    path.translate(size / 2 - box.center().x(), size / 2 - box.center().y())
    p.fillPath(path, QColor("#ffffff"))
    p.strokePath(path, QPen(QColor("#ffffff"), max(1.0, size * 0.035)))  # bolder at small sizes
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(s))
    return icon


# ---- icons and widgets that follow the light/dark switch ----
_themed: list[tuple] = []
_hooks: list[Callable[[], None]] = []


def themed_icon(widget, glyph: str, role: str = "TEXT", size: int = 20) -> None:
    """Give a button an icon in one of the theme colours (role = "TEXT", "MUTED", ...),
    and recolour it whenever the app switches between light and dark."""
    widget.setIcon(glyph_icon(glyph, globals()[role], size))
    _themed.append((widget, glyph, role, size))


def on_change(fn: Callable[[], None]) -> None:
    """Call fn after every light/dark switch (for anything painted with theme colours)."""
    _hooks.append(fn)


def apply(app, mode: str) -> None:
    set_mode(mode)
    try:
        app.styleHints().setColorScheme(
            Qt.ColorScheme.Dark if SCHEME == "dark" else Qt.ColorScheme.Light)  # title bar
    except AttributeError:
        pass
    app.setPalette(app_palette())
    app.setStyleSheet(build_stylesheet())
    alive = []
    for entry in _themed:
        widget, glyph, role, size = entry
        if shiboken6.isValid(widget):
            widget.setIcon(glyph_icon(glyph, globals()[role], size))
            alive.append(entry)
    _themed[:] = alive
    for fn in list(_hooks):
        try:
            fn()
        except RuntimeError:  # its widget is gone
            _hooks.remove(fn)


def app_palette() -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: SURFACE_2,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE_2,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: ON_ACCENT,
        QPalette.ColorRole.ToolTipBase: SURFACE_2,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.PlaceholderText: MUTED,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    return pal


def build_stylesheet() -> str:
    c = dict(PALETTES[MODE], UI=ui_family(), DISPLAY=display_family())
    for pts in (8.5, 9, 10, 10.5, 11, 13, 18, 26):
        c[f"F{pts}".replace(".", "_")] = font_size(pts)
    return """
* {
    font-family: "%(UI)s";
    font-size: %(F10_5)s;
    color: %(TEXT)s;
}
QMainWindow, QDialog, QMessageBox { background: %(BG)s; }
#LibraryView { background: %(BG)s; }

QLabel#Title {
    font-family: "%(DISPLAY)s";
    font-size: %(F26)s;
    font-weight: 600;
}
QLabel#Subtitle, QLabel#Muted { color: %(MUTED)s; }
QLabel#EmptyTitle {
    font-family: "%(DISPLAY)s";
    font-size: %(F18)s;
    font-weight: 600;
}

QPushButton#Primary {
    background: %(ACCENT)s;
    color: %(ON_ACCENT)s;
    border: none;
    border-radius: 10px;
    padding: 10px 20px;
    font-weight: 600;
}
QPushButton#Primary:hover { background: %(ACCENT_HOVER)s; }
QPushButton#Primary:pressed { background: %(ACCENT_PRESSED)s; }
QToolButton#RoundButton {
    background: %(SURFACE)s;
    border: 1px solid %(BORDER)s;
    border-radius: 10px;
    padding: 8px;
}
QToolButton#RoundButton:hover { background: %(SURFACE_2)s; }

QLineEdit#Search {
    background: %(SURFACE)s;
    border: 1px solid %(BORDER)s;
    border-radius: 10px;
    padding: 9px 12px;
    selection-background-color: %(ACCENT)s;
}
QLineEdit#Search:focus { border: 1px solid %(ACCENT)s; }

QListView { background: transparent; border: none; outline: 0; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: %(SCROLL)s; border-radius: 3px; min-height: 40px; }
QScrollBar::handle:vertical:hover { background: %(SCROLL_HOVER)s; }
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page { background: none; height: 0; }

QMenu {
    background: %(SURFACE_2)s;
    border: 1px solid %(BORDER)s;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item { padding: 8px 18px 8px 12px; border-radius: 6px; }
QMenu::item:selected { background: %(MENU_SEL)s; }
QMenu::separator { height: 1px; background: %(BORDER)s; margin: 4px 8px; }

QToolTip {
    background: %(SURFACE_2)s;
    color: %(TEXT)s;
    border: 1px solid %(BORDER)s;
    border-radius: 6px;
    padding: 6px 8px;
}

/* ---- Reader overlays ---- */
#SidePanel { background: transparent; border: none; }  /* painted as frosted glass */
QLabel#Section {
    color: %(MUTED)s;
    font-size: %(F8_5)s;
    font-weight: 600;
    letter-spacing: 1px;
    padding-top: 10px;
}
QLabel#Hint { color: %(MUTED)s; font-size: %(F9)s; }
QLabel#ThemeName { color: %(MUTED)s; font-size: %(F9)s; }
QPushButton#LinkButton {
    background: transparent; border: none; color: %(DANGER)s; font-size: %(F9)s; padding: 2px 4px;
}
QPushButton#LinkButton:hover { text-decoration: underline; }

/* ---- Annotation tools ---- */
QToolButton#Fab {
    background: %(GLASS)s;
    border: 1px solid %(GLASS_BORDER)s;
    border-radius: 24px;
}
QToolButton#Fab:hover { background: %(GLASS_HOVER)s; }
QToolButton#Fab:checked { background: %(ACCENT)s; border: none; }
#CornerBar {
    background: %(GLASS)s;
    border: 1px solid %(GLASS_BORDER)s;
    border-radius: 24px;
}
#CornerBar QToolButton#BarButton { border-radius: 22px; }
#CornerBar QToolButton#BarButton:hover { background: %(HOVER)s; }
#ToolColumn, #Palette {
    background: %(OVERLAY)s;
    border: 1px solid %(OVERLAY_BORDER)s;
    border-radius: 28px;
}
#Palette { border-radius: 22px; }
QToolButton#Tool { background: transparent; border: none; border-radius: 22px; }
QToolButton#Tool:hover { background: %(HOVER)s; }
QToolButton#Tool:checked { background: %(ACCENT_SOFT)s; }
QToolButton#Tool:disabled { background: transparent; }
QToolButton#Width { background: transparent; border: none; border-radius: 16px; }
QToolButton#Width:hover { background: %(HOVER)s; }
QToolButton#Width:checked { background: %(ACCENT_SOFT)s; }
QFrame#Divider { background: %(DIVIDER)s; border: none; }
QPushButton#ClearButton {
    background: %(DANGER_BG)s;
    color: %(DANGER)s;
    border: none;
    border-radius: 14px;
    padding: 6px 12px;
}
QPushButton#ClearButton:hover { background: %(DANGER_BG)s; text-decoration: underline; }
QPushButton#Swatch {
    border-radius: 15px;
    border: 2px solid %(SWATCH_BORDER)s;
}
QPushButton#Swatch:hover { border: 2px solid %(SWATCH_HOVER)s; }
QPushButton#Swatch:checked { border: 3px solid %(ACCENT)s; }
QPushButton#AddSwatch {
    border-radius: 15px;
    border: 2px dashed %(SWATCH_HOVER)s;
    background: transparent;
    color: %(MUTED)s;
    font-size: %(F13)s;
    padding-bottom: 2px;
}
QPushButton#AddSwatch:hover { border-color: %(ACCENT)s; color: %(ACCENT)s; }
QPushButton#RemoveSwatch {
    border-radius: 13px;
    border: none;
    background: %(DANGER_BG)s;
    color: %(DANGER)s;
    font-size: %(F11)s;
    padding-bottom: 2px;
}
QPushButton#RemoveSwatch:hover { background: %(DANGER)s; color: %(ON_ACCENT)s; }
QPushButton#PanelButton {
    background: %(SUBTLE)s;
    border: none;
    border-radius: 10px;
    padding: 11px 14px;
}
QPushButton#PanelButton:hover { background: %(HOVER)s; }
QPushButton#PanelButton:pressed { background: %(PRESSED)s; }
#Toast {
    background: %(OVERLAY)s;
    border: 1px solid %(OVERLAY_BORDER)s;
    border-radius: 12px;
}
QLabel#ToastLabel { font-weight: 600; padding: 8px 18px; }
QToolButton#ZoomLabel {
    min-width: 48px;
    padding: 7px 4px;
    color: %(MUTED)s;
    background: transparent;
    border: none;
    border-radius: 10px;
}
QToolButton#ZoomLabel:hover { background: %(HOVER)s; color: %(TEXT)s; }
QLabel#BookTitle { font-weight: 600; }
QLabel#PageLabel { color: %(MUTED)s; font-size: %(F10)s; }

QToolButton#BarButton {
    background: transparent;
    border: none;
    border-radius: 10px;
    padding: 7px 12px;
}
QToolButton#BarButton:hover { background: %(HOVER)s; }
QToolButton#BarButton:pressed { background: %(PRESSED)s; }

#Segmented {
    background: %(SUBTLE)s;
    border-radius: 10px;
}
QPushButton#Segment {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 9px 10px;
    color: %(MUTED)s;
}
QPushButton#Segment:hover { color: %(TEXT)s; }
QPushButton#Segment:checked { background: %(ACCENT)s; color: %(ON_ACCENT)s; font-weight: 600; }

QSlider::groove:horizontal { height: 4px; background: %(GROOVE)s; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(ACCENT)s; border-radius: 2px; }
QSlider::handle:horizontal {
    background: %(TEXT)s;
    width: 14px; height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: %(ACCENT)s; }
""" % c
