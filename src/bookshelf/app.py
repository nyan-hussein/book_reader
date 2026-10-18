import os
import sys

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QStackedWidget

from . import theme
from .library import Book, Library
from .library_view import LibraryView
from .reader_view import ReaderView


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bookshelf")
        self.setWindowIcon(theme.app_icon())
        self.resize(1280, 820)
        self.setMinimumSize(760, 520)
        self._was_maximized = False

        self.library = Library()
        theme.apply(QApplication.instance(), self.library.settings.get("app_theme", "dark"))
        self.library_view = LibraryView(self.library)
        self.reader = ReaderView(self.library)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.library_view)
        self.stack.addWidget(self.reader)
        self.setCentralWidget(self.stack)

        self.library_view.open_requested.connect(self.open_book)
        self.reader.close_requested.connect(self.show_library)
        self.reader.fullscreen_requested.connect(self.toggle_fullscreen)
        self.reader.app_theme_requested.connect(self.set_app_theme)
        self.library_view.app_theme_requested.connect(self.set_app_theme)

    def set_app_theme(self, mode: str):
        self.library.settings["app_theme"] = mode
        self.library.save()
        theme.apply(QApplication.instance(), mode)

    def open_book(self, book: Book):
        if not book.exists:
            answer = QMessageBox.question(
                self, "Book not found",
                f"“{book.title}” is no longer at:\n{book.path}\n\nRemove it from your library?")
            if answer == QMessageBox.StandardButton.Yes:
                self.library.remove(book)
                self.library.save()
                self.library_view.refresh()
            return
        try:
            self.reader.open_book(book)
        except Exception as exc:
            QMessageBox.warning(self, "Couldn't open book", str(exc))
            return
        self._was_maximized = self.isMaximized()
        self.stack.setCurrentWidget(self.reader)
        self.showFullScreen()

    def show_library(self):
        self.reader.close_book()
        self.stack.setCurrentWidget(self.library_view)
        self._leave_fullscreen()
        self.library_view.refresh()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self._leave_fullscreen()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()

    def _leave_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized() if self._was_maximized else self.showNormal()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            self.reader.set_fullscreen_state(self.isFullScreen())
        super().changeEvent(event)

    def closeEvent(self, event):
        self.reader.close_book()
        super().closeEvent(event)


def run():
    app = QApplication(sys.argv)
    app.setApplicationName("Bookshelf")
    if sys.platform == "win32":  # our own taskbar icon instead of Python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Bookshelf.Reader")
    app.setStyle("Fusion")
    window = MainWindow()
    window.showMaximized()
    if os.environ.get("BOOKSHELF_SELFTEST"):  # used by build_app.py to check a built app
        QTimer.singleShot(500, lambda: _self_test(window))
    sys.exit(app.exec())


def _self_test(window: MainWindow):
    book_path, shot_path = os.environ["BOOKSHELF_SELFTEST"].split("|")
    window.library_view.add_books([book_path])
    window.open_book(window.library.books[0])

    def finish():
        window.grab().save(shot_path)
        QApplication.quit()
    QTimer.singleShot(1500, finish)
