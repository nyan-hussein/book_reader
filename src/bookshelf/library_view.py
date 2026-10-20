"""Home screen: the grid of your books plus the 'Add books' action."""
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QStandardItem, QStandardItemModel,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListView, QMenu, QToolButton,
    QMessageBox, QPushButton, QStackedWidget, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from . import theme
from .library import EXTENSIONS, FILE_FILTER, Book, Library
from .theme import Glyph, glyph_icon, themed_icon

BOOK_ROLE = Qt.ItemDataRole.UserRole + 1


def _two_lines(fm: QFontMetrics, text: str, width: int) -> list[str]:
    words = text.split()
    line = ""
    for i, word in enumerate(words):
        candidate = f"{line} {word}".strip()
        if fm.horizontalAdvance(candidate) > width:
            if not line:
                return [fm.elidedText(text, Qt.TextElideMode.ElideRight, width)]
            rest = " ".join(words[i:])
            return [line, fm.elidedText(rest, Qt.TextElideMode.ElideRight, width)]
        line = candidate
    return [line]


def _show_in_folder(path: str) -> None:
    """Open the file's folder with the file selected (Explorer / Finder)."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


class BookDelegate(QStyledItemDelegate):
    PAD = 14
    COVER_W, COVER_H = 168, 240
    RADIUS = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._covers: dict[str, tuple[float, QPixmap]] = {}
        self.title_font = theme.ui_font(10.5, QFont.Weight.DemiBold)
        self.meta_font = theme.ui_font(9)

    def sizeHint(self, option, index):
        return QSize(self.COVER_W + 2 * self.PAD, self.COVER_H + 112)

    def _cover(self, book: Book) -> QPixmap | None:
        path = book.cover_path
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self._covers.get(book.id)
        if cached and cached[0] == mtime:
            return cached[1]
        pix = QPixmap(str(path))
        self._covers[book.id] = (mtime, pix)
        return pix

    def paint(self, p: QPainter, option, index):
        book: Book = index.data(BOOK_ROLE)
        p.save()
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        r = QRectF(option.rect).adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        cover = QRectF(r.left(), r.top() + (0 if hovered else 5), self.COVER_W, self.COVER_H)

        # soft drop shadow
        p.setPen(Qt.PenStyle.NoPen)
        shadow = QColor(theme.CARD_SHADOW)
        if hovered:
            shadow.setAlpha(shadow.alpha() + 8)
        for i in range(1, 8):
            p.setBrush(shadow)
            grow = i * 1.6
            p.drawRoundedRect(cover.adjusted(-grow, -grow + 6, grow, grow + 8),
                              self.RADIUS + grow, self.RADIUS + grow)

        clip = QPainterPath()
        clip.addRoundedRect(cover, self.RADIUS, self.RADIUS)
        p.setClipPath(clip)
        pix = self._cover(book)
        if pix and not pix.isNull():
            src = QRectF(pix.rect())
            ratio = cover.width() / cover.height()
            if src.width() / src.height() > ratio:  # too wide: crop the sides
                w = src.height() * ratio
                src = QRectF(src.x() + (src.width() - w) / 2, src.y(), w, src.height())
            else:  # too tall: keep the top of the page
                src = QRectF(src.x(), src.y(), src.width(), src.width() / ratio)
            p.drawPixmap(cover, pix, src)
        else:
            grad = QLinearGradient(cover.topLeft(), cover.bottomRight())
            grad.setColorAt(0, QColor(theme.CARD_BLANK_1))
            grad.setColorAt(1, QColor(theme.CARD_BLANK_2))
            p.fillRect(cover, grad)
            p.setPen(QColor(theme.TEXT))
            p.setFont(self.title_font)
            p.drawText(cover.adjusted(14, 14, -14, -14),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, book.title)
        if not book.exists:
            p.fillRect(cover, QColor(0, 0, 0, 170))
            p.setPen(QColor(theme.DANGER))
            p.setFont(self.meta_font)
            p.drawText(cover, Qt.AlignmentFlag.AlignCenter, "File not found")
        p.setClipping(False)

        if hovered:
            p.setPen(QPen(QColor(theme.ACCENT), 2))
        else:
            p.setPen(QPen(QColor(theme.CARD_EDGE), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(cover, self.RADIUS, self.RADIUS)

        # reading progress
        y = r.top() + 5 + self.COVER_H + 14
        track = QRectF(r.left(), y, self.COVER_W, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.CARD_TRACK))
        p.drawRoundedRect(track, 2, 2)
        if book.last_opened:
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(QRectF(track.x(), track.y(), max(4, track.width() * book.progress), 4), 2, 2)

        # title (up to two lines)
        p.setFont(self.title_font)
        p.setPen(QColor(theme.TEXT))
        fm = QFontMetrics(self.title_font)
        y += 14
        for line in _two_lines(fm, book.title, self.COVER_W):
            p.drawText(QRectF(r.left(), y, self.COVER_W, fm.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
            y += fm.height()

        p.setFont(self.meta_font)
        p.setPen(QColor(theme.MUTED))
        if book.last_opened:
            meta = f"{round(book.progress * 100)}%  ·  page {book.current_page + 1} of {book.page_count}"
        else:
            meta = f"New  ·  {book.page_count} pages"
        p.drawText(QRectF(r.left(), y + 2, self.COVER_W, 20), Qt.AlignmentFlag.AlignLeft, meta)
        p.restore()


class EmptyState(QWidget):
    def __init__(self, on_add, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)

        icon = QLabel()
        icon.setPixmap(glyph_icon(Glyph.LIBRARY, theme.ACCENT, 64).pixmap(64, 64))
        theme.on_change(lambda: icon.setPixmap(glyph_icon(Glyph.LIBRARY, theme.ACCENT, 64).pixmap(64, 64)))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Your library is empty")
        title.setObjectName("EmptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel("Add PDF or EPUB books from your computer to start reading.")
        text.setObjectName("Muted")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("  Add books")
        button.setObjectName("Primary")
        themed_icon(button, Glyph.ADD, "ON_ACCENT", 16)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(on_add)
        hint = QLabel("or drag and drop book files anywhere in this window")
        hint.setObjectName("Muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay.addWidget(icon)
        lay.addSpacing(6)
        lay.addWidget(title)
        lay.addWidget(text)
        lay.addSpacing(14)
        lay.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(4)
        lay.addWidget(hint)


class LibraryView(QWidget):
    open_requested = Signal(object)
    app_theme_requested = Signal(str)  # "dark" | "light"

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setObjectName("LibraryView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(48, 40, 34, 16)
        root.setSpacing(26)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("My Library")
        title.setObjectName("Title")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("Subtitle")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch()

        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search your books")
        self.search.setFixedWidth(280)
        search_icon = self.search.addAction(glyph_icon(Glyph.SEARCH, theme.MUTED, 16),
                                            QLineEdit.ActionPosition.LeadingPosition)
        themed_icon(search_icon, Glyph.SEARCH, "MUTED", 16)
        self.search.setClearButtonEnabled(True)
        add = QPushButton("  Add books")
        add.setObjectName("Primary")
        themed_icon(add, Glyph.ADD, "ON_ACCENT", 16)
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.clicked.connect(lambda: self.add_books())
        header.addWidget(self.search, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addSpacing(10)
        header.addWidget(add, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addSpacing(10)
        self.look_button = QToolButton()
        self.look_button.setObjectName("RoundButton")
        self.look_button.setIconSize(QSize(20, 20))
        self.look_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.look_button.clicked.connect(
            lambda: self.app_theme_requested.emit("light" if theme.MODE == "dark" else "dark"))
        header.addWidget(self.look_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._sync_look()
        theme.on_change(self._sync_look)
        header.addSpacing(14)
        root.addLayout(header)

        self.model = QStandardItemModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)

        self.grid = QListView()
        self.grid.setModel(self.proxy)
        self.grid.setItemDelegate(BookDelegate(self.grid))
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.grid.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.grid.verticalScrollBar().setSingleStep(24)
        self.grid.setMouseTracking(True)
        self.grid.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.grid.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.clicked.connect(self._clicked)
        self.grid.customContextMenuRequested.connect(self._context_menu)

        self.pages = QStackedWidget()
        self.empty = EmptyState(lambda: self.add_books())
        self.pages.addWidget(self.empty)
        self.pages.addWidget(self.grid)
        root.addWidget(self.pages, 1)

        self.refresh()

    def _clicked(self, index) -> None:
        book = index.data(BOOK_ROLE)
        if book is not None and any(b is book for b in self.library.books):
            self.open_requested.emit(book)

    def _sync_look(self):
        dark = theme.MODE == "dark"
        self.look_button.setIcon(glyph_icon(Glyph.SUN if dark else Glyph.MOON))
        self.look_button.setToolTip("Switch to light look" if dark else "Switch to dark look")
        self.grid.viewport().update() if hasattr(self, "grid") else None

    def refresh(self) -> None:
        self.model.clear()
        for book in self.library.sorted_books():
            item = QStandardItem(book.title)
            item.setData(book, BOOK_ROLE)
            item.setEditable(False)
            self.model.appendRow(item)
        n = len(self.library.books)
        self.subtitle.setText("No books yet" if n == 0 else f"{n} book{'s' if n != 1 else ''}")
        self.pages.setCurrentWidget(self.grid if n else self.empty)
        self.search.setVisible(n > 0)

    def add_books(self, paths: list[str] | None = None) -> None:
        if paths is None:
            paths, _ = QFileDialog.getOpenFileNames(self, "Add books", self.library.last_dir, FILE_FILTER)
        if not paths:
            return
        self.library.last_dir = str(Path(paths[0]).parent)
        failed = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for path in paths:
                try:
                    self.library.add(path)
                except Exception:
                    failed.append(Path(path).name)
                QApplication.processEvents()
        finally:
            QApplication.restoreOverrideCursor()
        self.library.save()
        self.refresh()
        if failed:
            QMessageBox.warning(self, "Some books couldn't be added",
                                "These files couldn't be opened as books:\n\n" + "\n".join(failed))

    def _context_menu(self, pos) -> None:
        index = self.grid.indexAt(pos)
        if not index.isValid():
            return
        book: Book = index.data(BOOK_ROLE)
        if book is None or not any(b is book for b in self.library.books):
            return
        menu = QMenu(self)
        menu.addAction(glyph_icon(Glyph.READ), "Read", lambda: self.open_requested.emit(book))
        reveal = menu.addAction(glyph_icon(Glyph.FOLDER), "Show in folder",
                                lambda: _show_in_folder(book.path))
        reveal.setEnabled(book.exists)
        menu.addSeparator()
        menu.addAction(glyph_icon(Glyph.DELETE, theme.DANGER), "Remove from library",
                       lambda: self._confirm_remove(book))
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def _confirm_remove(self, book: Book) -> None:
        answer = QMessageBox.question(
            self, "Remove book",
            f"Remove “{book.title}” from your library?\n\n"
            "The book file on your computer is not deleted.")
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_book(book)

    def remove_book(self, book: Book) -> None:
        self.library.remove(book)
        self.library.save()
        # rebuild the grid after the current click / menu has finished: clearing the model
        # while the list view is still handling the event that led here can crash Qt
        QTimer.singleShot(0, self.refresh)

    # drag and drop book files onto the window
    @staticmethod
    def _book_paths(event) -> list[str]:
        if not event.mimeData().hasUrls():
            return []
        return [u.toLocalFile() for u in event.mimeData().urls()
                if u.isLocalFile() and u.toLocalFile().lower().endswith(EXTENSIONS)]

    def dragEnterEvent(self, event):
        if self._book_paths(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = self._book_paths(event)
        if paths:
            event.acceptProposedAction()
            self.add_books(paths)
