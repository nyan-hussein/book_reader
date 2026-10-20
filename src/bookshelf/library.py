"""Book records, covers and the JSON file that remembers them."""
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf
from pymupdf import mupdf

if os.environ.get("BOOKSHELF_DATA_DIR"):  # used by the build self-test
    APP_DIR = Path(os.environ["BOOKSHELF_DATA_DIR"])
elif sys.platform == "win32":
    APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Bookshelf"
elif sys.platform == "darwin":
    APP_DIR = Path.home() / "Library" / "Application Support" / "Bookshelf"
else:
    APP_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Bookshelf"
COVERS_DIR = APP_DIR / "covers"
LAYOUT_DIR = APP_DIR / "layout"  # saved page layouts of EPUB-style books (rebuilt if deleted)
LIBRARY_FILE = APP_DIR / "library.json"

EXTENSIONS = (".pdf", ".epub", ".mobi", ".fb2", ".xps", ".cbz")
REFLOWABLE = (".epub", ".mobi", ".fb2")  # text that MuPDF splits into pages itself
FILE_FILTER = "Books ({});;All files (*)".format(" ".join(f"*{e}" for e in EXTENSIONS))

COVER_WIDTH = 360
PAGE_LAYOUT = dict(width=400, height=600, fontsize=11)  # how EPUB-style books are paged


def book_id(path: str) -> str:
    key = os.path.normcase(os.path.abspath(path))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class Book:
    path: str
    title: str
    page_count: int = 0
    current_page: int = 0
    view_mode: int = 1  # 1 = single page, 2 = two-page spread
    fit_mode: str = "page"  # "page" = whole page visible, "width" = fill the screen width
    added_at: float = field(default_factory=time.time)
    last_opened: float = 0.0

    @property
    def id(self) -> str:
        return book_id(self.path)

    @property
    def cover_path(self) -> Path:
        return COVERS_DIR / f"{self.id}.png"

    @property
    def exists(self) -> bool:
        return os.path.exists(self.path)

    @property
    def progress(self) -> float:
        if not self.page_count:
            return 0.0
        return min(1.0, (self.current_page + 1) / self.page_count)


def _clean_title(raw: str, path: str) -> str:
    title = (raw or "").strip()
    junk = ("microsoft word", "untitled", ".doc", ".pdf", ".indd")
    if not title or len(title) > 150 or any(j in title.lower() for j in junk):
        return Path(path).stem.replace("_", " ")
    return title


def _make_cover(doc: "pymupdf.Document", target: Path) -> None:
    page = doc.load_page(0)
    zoom = COVER_WIDTH / max(page.rect.width, 1)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    pix.save(str(target))


def _layout_file(path: str) -> Path | None:
    """Where the page layout of this version of the file is saved (None: not an EPUB-style book)."""
    if not path.lower().endswith(REFLOWABLE):
        return None
    st = os.stat(path)
    return LAYOUT_DIR / f"{book_id(path)}-{st.st_size}-{st.st_mtime_ns}.layout"


def forget_layout(path: str) -> None:
    for old in LAYOUT_DIR.glob(f"{book_id(path)}-*.layout"):
        try:
            old.unlink()
        except OSError:
            pass


def needs_layout(path: str) -> bool:
    """Will opening this book take a long time? Large EPUBs are laid out page by page the
    first time (seconds to minutes); after that the saved layout opens them instantly."""
    try:
        layout = _layout_file(path)
    except OSError:
        return False
    return layout is not None and not layout.exists()


def open_document(path: str) -> "pymupdf.Document":
    """Open a book file, reusing the saved page layout of EPUB-style books."""
    doc = pymupdf.open(path, **PAGE_LAYOUT)
    layout = _layout_file(path) if doc.is_reflowable else None
    if layout is None:
        return doc
    if layout.exists():
        try:
            fast = mupdf.fz_open_accelerated_document(path, str(layout))
            mupdf.fz_layout_document(fast, *PAGE_LAYOUT.values())
            doc.this = fast
            return doc
        except Exception:  # unreadable layout file: lay the book out again below
            pass
    doc.page_count  # lays out the whole book: the slow part
    try:
        LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
        forget_layout(path)
        mupdf.fz_save_accelerator(doc.this, str(layout))
    except Exception:
        pass  # it still works, it just stays slow to open
    return doc


def read_book_file(path: str) -> dict:
    """Open a book once: check it, draw its cover, save its layout. May run in a helper
    process (see background.py), so it only takes and returns plain values."""
    with open_document(path) as doc:
        if doc.page_count == 0:
            raise ValueError("The book has no pages")
        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        _make_cover(doc, COVERS_DIR / f"{book_id(path)}.png")
        return {"title": _clean_title((doc.metadata or {}).get("title", ""), path),
                "page_count": doc.page_count}


class Library:
    def __init__(self) -> None:
        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        self.books: list[Book] = []
        self.last_dir: str = str(Path.home())
        self.settings: dict = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError):
            # unreadable (e.g. edited by hand): keep a copy so the next save doesn't lose it
            try:
                shutil.copy2(LIBRARY_FILE, LIBRARY_FILE.with_suffix(f".broken-{int(time.time())}.json"))
            except OSError:
                pass
            data = {}
        if not isinstance(data, dict):
            data = {}
        known = Book.__dataclass_fields__
        self.books = []
        seen = set()
        for entry in data.get("books") or []:
            # skip entries that are damaged or listed twice instead of refusing to start
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            try:
                book = Book(**{k: v for k, v in entry.items() if k in known})
                book.title = str(book.title or Path(book.path).stem)
                book.page_count = max(int(book.page_count), 0)
                book.current_page = max(int(book.current_page), 0)
                book.view_mode = int(book.view_mode)
                book.added_at = float(book.added_at)
                book.last_opened = float(book.last_opened)
            except (TypeError, ValueError):
                continue
            if book.id not in seen:
                seen.add(book.id)
                self.books.append(book)
        last_dir = data.get("last_dir")
        self.last_dir = last_dir if isinstance(last_dir, str) else self.last_dir
        settings = data.get("settings")
        self.settings = settings if isinstance(settings, dict) else {}

    def save(self) -> None:
        data = {
            "books": [asdict(b) for b in self.books],
            "last_dir": self.last_dir,
            "settings": self.settings,
        }
        tmp = LIBRARY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                tmp.replace(LIBRARY_FILE)
                return
            except PermissionError:  # Windows: antivirus / backup tools briefly lock the file
                if attempt == 4:
                    raise
                time.sleep(0.05)

    def sorted_books(self) -> list[Book]:
        """Recently read first, then recently added."""
        return sorted(self.books, key=lambda b: (b.last_opened, b.added_at), reverse=True)

    def contains(self, path: str) -> bool:
        key = os.path.normcase(os.path.abspath(path))
        return any(os.path.normcase(b.path) == key for b in self.books)

    def add(self, path: str, info: dict | None = None) -> Book | None:
        """Add a book file. Returns None if it is already in the library;
        raises if the file can't be opened as a book. `info` is read_book_file()'s
        result when it was already run (in the background)."""
        path = os.path.abspath(path)
        if self.contains(path):
            return None
        info = info or read_book_file(path)
        book = Book(path=path, title=info["title"], page_count=info["page_count"])
        self.books.append(book)
        return book

    def remove(self, book: Book) -> None:
        """Forget a book. The book file itself is never touched."""
        self.books = [b for b in self.books if b is not book]
        # highlights are kept on purpose: adding the same file again brings them back
        try:
            book.cover_path.unlink()
        except OSError:
            pass
        forget_layout(book.path)
