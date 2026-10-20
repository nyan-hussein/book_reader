"""Highlights and pen drawings for one book, with undo/redo.

They are saved in the app's data folder (never inside the book file), in page
coordinates, so they stay in place at any zoom, fit or page layout.

Item shapes:
  {"type": "highlight", "page": 3, "color": "#ffe066", "rects": [[x0, y0, x1, y1], ...], "text": "..."}
  {"type": "marker" | "ink", "page": 3, "color": "#1971c2", "width": 2.4, "points": [[x, y], ...]}
"""
import json
import math
import uuid
from pathlib import Path

from . import library


def annotations_file(book_id: str) -> Path:
    return library.APP_DIR / "annotations" / f"{book_id}.json"


def _valid(item) -> bool:
    """Can this saved item be drawn? Damaged ones are skipped rather than crashing the reader."""
    try:
        int(item["page"])
        str(item["color"])
        if item["type"] == "highlight":
            return all(len(r) == 4 for r in item["rects"])
        if item["type"] in ("marker", "ink"):
            float(item["width"])
            return len(item["points"]) > 0 and all(len(pt) == 2 for pt in item["points"])
    except (KeyError, TypeError, ValueError):
        pass
    return False


class Annotations:
    """Every change is one undo step: (items removed, items added)."""

    def __init__(self, book_id: str):
        self.path = annotations_file(book_id)
        self.items: list[dict] = []
        self._undo: list[tuple[list[dict], list[dict]]] = []
        self._redo: list[tuple[list[dict], list[dict]]] = []
        try:
            items = json.loads(self.path.read_text(encoding="utf-8")).get("items", [])
        except (OSError, ValueError, AttributeError):
            items = []
        self.items = [it for it in items if _valid(it)] if isinstance(items, list) else []
        for it in self.items:
            it.setdefault("id", uuid.uuid4().hex)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "items": self.items}), encoding="utf-8")
        tmp.replace(self.path)

    def on_page(self, page: int) -> list[dict]:
        return [it for it in self.items if it["page"] == page]

    # ---- changes that become one undo step ----
    def add(self, item: dict) -> None:
        self.change([], [item])

    def remove(self, items: list[dict]) -> None:
        self.change(items, [])

    def change(self, removed: list[dict], added: list[dict]) -> None:
        if not removed and not added:
            return
        self.discard(removed)
        self.insert(added)
        self.record_change(removed, added)

    # ---- live editing (e.g. while the eraser moves); record_change() makes it one step ----
    def insert(self, items: list[dict]) -> None:
        for it in items:
            it.setdefault("id", uuid.uuid4().hex)
        self.items.extend(items)

    def discard(self, items: list[dict]) -> None:
        ids = {it["id"] for it in items}
        self.items = [it for it in self.items if it["id"] not in ids]

    def record_change(self, removed: list[dict], added: list[dict]) -> None:
        if removed or added:
            self._undo.append((list(removed), list(added)))
            self._redo.clear()

    # ---- history: one step at a time ----
    def undo(self) -> list[dict]:
        """Undo the last step. Returns the items involved (empty if nothing to undo)."""
        if not self._undo:
            return []
        removed, added = self._undo.pop()
        self.discard(added)
        self.items.extend(removed)
        self._redo.append((removed, added))
        return removed + added

    def redo(self) -> list[dict]:
        if not self._redo:
            return []
        removed, added = self._redo.pop()
        self.discard(removed)
        self.items.extend(added)
        self._undo.append((removed, added))
        return removed + added

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)


def hits(item: dict, x: float, y: float, radius: float) -> bool:
    """Is (x, y) within `radius` of this annotation? (page coordinates)"""
    if item["type"] == "highlight":
        return any(x0 - radius <= x <= x1 + radius and y0 - radius <= y <= y1 + radius
                   for x0, y0, x1, y1 in item["rects"])
    reach = radius + item.get("width", 1) / 2
    pts = item["points"]
    if len(pts) == 1:
        return math.hypot(x - pts[0][0], y - pts[0][1]) <= reach
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        if math.hypot(x - (ax + t * dx), y - (ay + t * dy)) <= reach:
            return True
    return False


def _densify(points: list, step: float) -> list:
    out = [points[0]]
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        n = max(1, int(math.hypot(bx - ax, by - ay) / step))
        out.extend([ax + (bx - ax) * k / n, ay + (by - ay) * k / n] for k in range(1, n + 1))
    return out


def cut(item: dict, x: float, y: float, radius: float) -> list[dict] | None:
    """Rub out the part of `item` inside a circle. Returns None if the circle misses it,
    otherwise the pieces that are left (possibly none). Pieces have no id yet."""
    if not hits(item, x, y, radius):
        return None
    base = {k: v for k, v in item.items() if k != "id"}
    if item["type"] == "highlight":
        rects, touched = [], False
        for x0, y0, x1, y1 in item["rects"]:
            dy = max(y0 - y, 0.0, y - y1)
            if dy >= radius:
                rects.append([x0, y0, x1, y1])
                continue
            half = math.sqrt(radius * radius - dy * dy)  # circle's half-width on this line
            a, b = x - half, x + half
            if b <= x0 or a >= x1:
                rects.append([x0, y0, x1, y1])
                continue
            touched = True
            if a - x0 > 0.5:
                rects.append([x0, y0, a, y1])
            if x1 - b > 0.5:
                rects.append([b, y0, x1, y1])
        if not touched:
            return None
        return [dict(base, rects=rects)] if rects else []
    # pen / marker: drop the points inside the circle, splitting the line where it was cut
    reach = radius + item.get("width", 1) / 2
    pieces, current = [], []
    for px, py in _densify(item["points"], max(radius / 3, 0.3)):
        if math.hypot(px - x, py - y) <= reach:
            if current:
                pieces.append(current)
            current = []
        else:
            current.append([px, py])
    if current:
        pieces.append(current)
    return [dict(base, points=p) for p in pieces if len(p) >= 2]
