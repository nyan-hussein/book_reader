"""Slow book work (laying out a large EPUB) in a helper process, so the window keeps
responding and shows a "Preparing…" box instead of freezing.

MuPDF holds Python's lock while it works, so a thread would still freeze the window;
a separate process doesn't. The app's main.py calls multiprocessing.freeze_support()
so this also works in the installed app."""
import multiprocessing
import queue
import time

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QApplication, QProgressDialog

SHOW_AFTER = 0.4  # seconds before the box appears (quick jobs never show it)


class Cancelled(Exception):
    pass


def _call(fn, args, results):
    try:
        results.put(("ok", fn(*args)))
    except Exception as exc:
        results.put(("error", str(exc) or type(exc).__name__))


def run(fn, *args, parent=None, text="Preparing the book…"):
    """fn(*args) in a helper process; returns its result. Raises Cancelled if the user
    cancels, RuntimeError if it fails. fn must be a top-level function of a module."""
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    proc = ctx.Process(target=_call, args=(fn, args, results), daemon=True)
    proc.start()
    box = QProgressDialog(text, "Cancel", 0, 0, parent)
    box.setWindowTitle("Bookshelf")
    box.setWindowModality(Qt.WindowModality.WindowModal)
    box.setMinimumDuration(0)
    box.setAutoClose(False)
    box.hide()
    started = time.monotonic()
    try:
        while True:
            try:
                status, value = results.get(timeout=0.03)
                break
            except queue.Empty:
                pass
            if not proc.is_alive():
                try:  # it may have finished just now
                    status, value = results.get(timeout=0.5)
                    break
                except queue.Empty:
                    raise RuntimeError("The book couldn't be prepared (the helper stopped)")
            if box.wasCanceled():
                raise Cancelled()
            if not box.isVisible() and time.monotonic() - started > SHOW_AFTER:
                box.show()
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 30)
    finally:
        box.close()
        box.deleteLater()
        if proc.is_alive():
            proc.terminate()
        proc.join(5)
    if status == "error":
        raise RuntimeError(value)
    return value
