"""Book records, covers and the JSON file that remembers them."""
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

if os.environ.get("BOOKSHELF_DATA_DIR"):  # used by the build self-test
    APP_DIR = Path(os.environ["BOOKSHELF_DATA_DIR"])
elif sys.platform == "win32":
    APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Bookshelf"
elif sys.platform == "darwin":
    APP_DIR = Path.home() / "Library" / "Application Support" / "Bookshelf"
else:
    APP_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Bookshelf"
COVERS_DIR = APP_DIR / "covers"
LIBRARY_FILE = APP_DIR / "library.json"

EXTENSIONS = (".pdf", ".epub", ".mobi", ".fb2", ".xps", ".cbz")
FILE_FILTER = "Books ({});;All files (*)".format(" ".join(f"*{e}" for e in EXTENSIONS))

COVER_WIDTH = 360


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
        key = os.path.normcase(os.path.abspath(self.path))
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

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
        except (OSError, ValueError):
            data = {}
        known = Book.__dataclass_fields__
        self.books = [
            Book(**{k: v for k, v in entry.items() if k in known})
            for entry in data.get("books", [])
        ]
        self.last_dir = data.get("last_dir", self.last_dir)
        self.settings = data.get("settings", {})

    def save(self) -> None:
        data = {
            "books": [asdict(b) for b in self.books],
            "last_dir": self.last_dir,
            "settings": self.settings,
        }
        tmp = LIBRARY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(LIBRARY_FILE)

    def sorted_books(self) -> list[Book]:
        """Recently read first, then recently added."""
        return sorted(self.books, key=lambda b: (b.last_opened, b.added_at), reverse=True)

    def contains(self, path: str) -> bool:
        key = os.path.normcase(os.path.abspath(path))
        return any(os.path.normcase(b.path) == key for b in self.books)

    def add(self, path: str) -> Book | None:
        """Add a book file. Returns None if it is already in the library;
        raises if the file can't be opened as a book."""
        path = os.path.abspath(path)
        if self.contains(path):
            return None
        with pymupdf.open(path) as doc:
            if doc.page_count == 0:
                raise ValueError("The book has no pages")
            book = Book(
                path=path,
                title=_clean_title((doc.metadata or {}).get("title", ""), path),
                page_count=doc.page_count,
            )
            _make_cover(doc, book.cover_path)
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
