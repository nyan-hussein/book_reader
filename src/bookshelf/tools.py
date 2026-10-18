"""The round pen button in the top-right corner of the reader, the tools it opens
(highlighter, pen, eraser, undo, redo) and their colour palettes."""
from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QColorDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from . import theme
from .overlay import OverlayBar
from .theme import Glyph, glyph_icon, themed_icon

HIGHLIGHT_COLORS = [
    ("#ffd43b", "Yellow"), ("#8ce99a", "Green"), ("#74c0fc", "Blue"),
    ("#faa2c1", "Pink"), ("#ffa94d", "Orange"), ("#b197fc", "Purple"),
]
PEN_COLORS = [
    ("#1b1b1f", "Black"), ("#e03131", "Red"), ("#1971c2", "Blue"), ("#2f9e44", "Green"),
    ("#7048e8", "Purple"), ("#f08c00", "Orange"), ("#f1f3f5", "White"),
]
PEN_WIDTHS = [(1.0, "Fine"), (2.2, "Medium"), (4.5, "Bold")]  # in page points
HIGHLIGHT_WIDTHS = [(7.0, "Thin"), (12.0, "Medium"), (18.0, "Thick")]
MAX_COLORS = 9  # per tool, so the palette stays one tidy row
COLOR_NAMES = {c: n for c, n in HIGHLIGHT_COLORS + PEN_COLORS}

EDGE = 16
FAB = 48
TOOL = 44


def _tool_button(glyph: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setObjectName("Tool")
    themed_icon(b, glyph, "TEXT", 22)
    b.setIconSize(QSize(22, 22))
    b.setFixedSize(TOOL, TOOL)
    b.setToolTip(tip)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _dot_icon(diameter: float, color: str | None = None) -> QIcon:
    dpr, size = 2, 28
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color or theme.TEXT))
    p.drawEllipse((size - diameter) / 2, (size - diameter) / 2, diameter, diameter)
    p.end()
    return QIcon(pm)


def _color_button(color: str, name: str) -> QPushButton:
    b = QPushButton()
    b.setCheckable(True)
    b.setFixedSize(30, 30)
    b.setToolTip(name)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton {{ background: {color}; border-radius: 15px;"
        f" border: 2px solid {theme.PALETTES[theme.MODE]['SWATCH_BORDER']}; }}"
        f"QPushButton:hover {{ border: 2px solid {theme.PALETTES[theme.MODE]['SWATCH_HOVER']}; }}"
        f"QPushButton:checked {{ border: 3px solid {theme.TEXT}; }}")
    return b


def _divider(vertical: bool) -> QFrame:
    d = QFrame()
    d.setObjectName("Divider")
    if vertical:
        d.setFixedSize(1, 24)
    else:
        d.setFixedSize(28, 1)
    return d


class ToolDock(QObject):
    tool_changed = Signal(object)  # "highlight" | "pen" | "eraser" | None (reading)
    undo_requested = Signal()
    redo_requested = Signal()
    clear_requested = Signal()

    def __init__(self, settings: dict, parent: QWidget):
        super().__init__(parent)
        self.settings = settings
        self.host = parent
        self.tool: str | None = None   # the selected tool; None = just reading
        self.is_open = False           # tool column showing
        self._last_tool = settings.get("last_tool", "highlight")
        self.highlight_color = settings.get("highlight_color", HIGHLIGHT_COLORS[0][0])
        self.pen_color = settings.get("pen_color", PEN_COLORS[2][0])
        if self.highlight_color not in self.colors("highlight"):
            self.highlight_color = self.colors("highlight")[0]
        if self.pen_color not in self.colors("pen"):
            self.pen_color = self.colors("pen")[0]
        self.pen_width = float(settings.get("pen_width", PEN_WIDTHS[1][0]))
        self.highlight_width = float(settings.get("highlight_width", HIGHLIGHT_WIDTHS[1][0]))
        self._history = (False, False)
        self._groups: list[QButtonGroup] = []

        self.fab = QToolButton(parent)
        self.fab.setObjectName("Fab")
        self.fab.setCheckable(True)
        self.fab.setFixedSize(FAB, FAB)
        self.fab.setIconSize(QSize(22, 22))
        self.fab.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fab.clicked.connect(self.toggle)

        self.column = OverlayBar("ToolColumn", parent)
        col = QVBoxLayout(self.column)
        col.setContentsMargins(6, 8, 6, 8)
        col.setSpacing(4)
        col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.buttons: dict[str, QToolButton] = {}
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(False)  # so tapping the selected tool again unselects it
        for key, glyph, tip in (("highlight", Glyph.HIGHLIGHT, "Highlighter"),
                                ("pen", Glyph.PEN, "Pen"),
                                ("eraser", Glyph.ERASER, "Eraser")):
            b = _tool_button(glyph, tip)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.select(k))
            self.tool_group.addButton(b)
            self.buttons[key] = b
            col.addWidget(b)
        col.addSpacing(4)
        col.addWidget(_divider(False), 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(4)
        self.undo_button = _tool_button(Glyph.UNDO, "Undo (Ctrl Z)")
        self.undo_button.clicked.connect(self.undo_requested)
        self.redo_button = _tool_button(Glyph.REDO, "Redo (Ctrl Y)")
        self.redo_button.clicked.connect(self.redo_requested)
        col.addWidget(self.undo_button)
        col.addWidget(self.redo_button)

        self.palette = OverlayBar("Palette", parent)
        self._palette_layout = QHBoxLayout(self.palette)
        self._palette_layout.setContentsMargins(10, 7, 10, 7)
        self._palette_layout.setSpacing(6)

        self._sync_fab()
        self.set_history(False, False)
        self.fab.show()
        theme.on_change(self._retheme)

    def _retheme(self):
        self.set_history(*self._history)
        self._sync_fab()
        if self.tool:
            self._build_palette()
            self.place()

    # ---- state ----
    def toggle(self):
        if self.is_open:
            self.close()
        else:
            self.select("highlight")

    def close(self):
        if not self.is_open:
            self._sync_fab()
            return
        self.is_open = False
        self._unselect()
        self.column.conceal()
        self._sync_fab()

    def select(self, key: str):
        if key == self.tool:  # tapping the selected tool again unselects it
            self._unselect()
            return
        self.tool = key
        self.is_open = True
        self._last_tool = key
        self.settings["last_tool"] = key
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        self._build_palette()
        self.column.reveal()
        self.palette.reveal()
        self._sync_fab()
        self.place()
        self.tool_changed.emit(key)

    def _unselect(self):
        for b in self.buttons.values():
            b.setChecked(False)
        self.palette.conceal()
        if self.tool is not None:
            self.tool = None
            self.tool_changed.emit(None)

    def set_history(self, can_undo: bool, can_redo: bool):
        self._history = (can_undo, can_redo)
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)
        self.undo_button.setIcon(glyph_icon(Glyph.UNDO, theme.TEXT if can_undo else theme.DISABLED, 22))
        self.redo_button.setIcon(glyph_icon(Glyph.REDO, theme.TEXT if can_redo else theme.DISABLED, 22))

    def _sync_fab(self):
        active = self.is_open
        self.fab.setChecked(active)
        self.fab.setIcon(glyph_icon(Glyph.CLOSE if active else Glyph.ANNOTATE,
                                    theme.ON_ACCENT if active else theme.FAINT, 22))
        self.fab.setToolTip("Done (Esc)" if active else "Highlight and draw")

    # ---- palette ----
    def _build_palette(self):
        lay = self._palette_layout
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().hide()  # hide now; deletion only happens later
                item.widget().deleteLater()
        self._groups.clear()
        if self.tool == "highlight":
            self._add_colors("highlight")
            self._add_widths(HIGHLIGHT_WIDTHS, self.highlight_width, "highlight_width", 0.9)
        elif self.tool == "pen":
            self._add_colors("pen")
            self._add_widths(PEN_WIDTHS, self.pen_width, "pen_width", 2.6)
        elif self.tool == "eraser":
            hint = QLabel("Drag over a mark to erase it")
            hint.setObjectName("Hint")
            lay.addSpacing(6)
            lay.addWidget(hint)
            lay.addSpacing(4)
            clear = QPushButton("  Clear page")
            clear.setObjectName("ClearButton")
            clear.setIcon(glyph_icon(Glyph.DELETE, theme.DANGER, 16))
            clear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            clear.setCursor(Qt.CursorShape.PointingHandCursor)
            clear.setToolTip("Remove every highlight and drawing on the pages on screen (can be undone)")
            clear.clicked.connect(self.clear_requested)
            lay.addWidget(clear)
        # new children of an already visible palette only show up later; show them
        # now so the palette measures its real size
        for k in range(lay.count()):
            if lay.itemAt(k).widget():
                lay.itemAt(k).widget().show()
        lay.activate()
        self.palette.adjustSize()

    # ---- colours you can add and remove (per tool) ----
    def colors(self, kind: str) -> list[str]:
        saved = self.settings.get(f"{kind}_colors")
        if saved:
            valid = [c for c in saved if isinstance(c, str) and QColor(c).isValid()]
            if valid:
                return valid
        return [c for c, _ in (HIGHLIGHT_COLORS if kind == "highlight" else PEN_COLORS)]

    def _add_colors(self, kind: str):
        attr = f"{kind}_color"
        current = getattr(self, attr)
        colors = self.colors(kind)
        group = QButtonGroup(self)
        for color in colors:
            b = _color_button(color, COLOR_NAMES.get(color, color))
            b.setChecked(color == current)
            b.clicked.connect(lambda _=False, c=color: self._set(attr, c))
            group.addButton(b)
            self._palette_layout.addWidget(b)
        self._groups.append(group)
        if len(colors) < MAX_COLORS:
            add = QPushButton("+")
            add.setObjectName("AddSwatch")
            add.setFixedSize(30, 30)
            add.setToolTip("Add a color")
            add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            add.setCursor(Qt.CursorShape.PointingHandCursor)
            add.clicked.connect(lambda: self._add_color(kind))
            self._palette_layout.addWidget(add)
        if len(colors) > 1:
            remove = QPushButton("\u00d7")
            remove.setObjectName("RemoveSwatch")
            remove.setFixedSize(26, 26)
            remove.setToolTip("Remove the selected color")
            remove.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            remove.clicked.connect(lambda: self._remove_color(kind))
            self._palette_layout.addWidget(remove)

    def _add_color(self, kind: str):
        attr = f"{kind}_color"
        title = "Add a highlighter color" if kind == "highlight" else "Add a pen color"
        color = QColorDialog.getColor(QColor(getattr(self, attr)), self.host, title)
        if not color.isValid():
            return
        name = color.name()
        colors = [c for c in self.colors(kind) if c != name] + [name]
        self.settings[f"{kind}_colors"] = colors[-MAX_COLORS:]
        self._set(attr, name)
        self._refresh_palette()

    def _remove_color(self, kind: str):
        attr = f"{kind}_color"
        colors = self.colors(kind)
        current = getattr(self, attr)
        if len(colors) <= 1 or current not in colors:
            return
        i = colors.index(current)
        colors.remove(current)
        self.settings[f"{kind}_colors"] = colors
        self._set(attr, colors[min(i, len(colors) - 1)])
        self._refresh_palette()

    def _refresh_palette(self):
        self._build_palette()
        self.place()

    def _add_widths(self, widths, current: float, attr: str, dot_scale: float):
        lay = self._palette_layout
        lay.addSpacing(2)
        lay.addWidget(_divider(True))
        lay.addSpacing(2)
        group = QButtonGroup(self)
        for width, name in widths:
            b = QToolButton()
            b.setObjectName("Width")
            b.setCheckable(True)
            b.setFixedSize(32, 32)
            b.setIcon(_dot_icon(min(22.0, 3 + width * dot_scale)))
            b.setIconSize(QSize(28, 28))
            b.setToolTip(name)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(abs(width - current) < 1e-6)
            b.clicked.connect(lambda _=False, w=width: self._set(attr, w))
            group.addButton(b)
            lay.addWidget(b)
        self._groups.append(group)

    def _set(self, attr: str, value):
        setattr(self, attr, value)
        self.settings[attr] = value

    # ---- layout ----
    def place(self):
        """Pen button in the top-right corner; the tools drop down beneath it and the
        palette opens to the left of the selected tool."""
        x = self.host.width() - EDGE - FAB
        self.fab.move(x, EDGE)
        self.fab.raise_()
        self.column.adjustSize()
        col = self.column.sizeHint()
        col_x = x + (FAB - col.width()) // 2
        col_y = EDGE + FAB + 10
        self.column.setGeometry(col_x, col_y, col.width(), col.height())
        if self.tool:
            self.column.layout().activate()
            btn = self.buttons[self.tool]
            center_y = col_y + btn.y() + btn.height() // 2
            pal = self.palette.sizeHint()
            self.palette.setGeometry(col_x - 12 - pal.width(), center_y - pal.height() // 2,
                                     pal.width(), pal.height())
