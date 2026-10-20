import faulthandler
import os
import sys
import time
import traceback

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QStackedWidget

from . import background, library, theme
from .library import Book, Library, prepare_in_background, read_book_file
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
                self.library_view.remove_book(book)
            return
        try:
            if prepare_in_background(book.path):  # a big EPUB that changed, or was added by an older version
                info = background.run(read_book_file, book.path, parent=self,
                                      text=f"Preparing “{book.title}”…\n"
                                           "Large books take a while the first time.")
                book.page_count = info["page_count"]
            self.reader.open_book(book)
        except background.Cancelled:
            return
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


CRASH_LOG = library.APP_DIR / "crash.log"
_shown_errors: set[str] = set()


def _install_error_handlers():
    """The installed app has no console, so errors would vanish (or close the app) unseen.
    Write them to crash.log in the data folder and tell the user instead."""
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        log = open(CRASH_LOG, "a", encoding="utf-8")
    except OSError:
        return
    faulthandler.enable(log)  # hard crashes inside Qt / MuPDF still leave a trace

    def hook(kind, value, tb):
        log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log.write("".join(traceback.format_exception(kind, value, tb)))
        log.flush()
        message = f"{kind.__name__}: {value}"
        if QApplication.instance() and message not in _shown_errors:
            _shown_errors.add(message)
            # shown after the current event (often a paint) has finished
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                None, "Something went wrong",
                f"{message}\n\nBookshelf kept running. Details were saved to:\n{CRASH_LOG}"))
    sys.excepthook = hook


def run():
    _install_error_handlers()
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
    *book_paths, shot_path = os.environ["BOOKSHELF_SELFTEST"].split("|")
    window.library_view.add_books(book_paths)
    if len(window.library.books) != len(book_paths):  # no screenshot = the build fails
        QApplication.quit()
        return
    window.open_book(next(b for b in window.library.books if b.path.endswith(".pdf")))

    def finish():
        window.grab().save(shot_path)
        QApplication.quit()
    QTimer.singleShot(1500, finish)
