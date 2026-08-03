"""Shared 6×4 fill-viewport thumb sizing helpers."""

from __future__ import annotations

COLS = 6
ROWS = 4
PAGE_SIZE = COLS * ROWS  # 24
NAME_RESERVE = 22
CELL_PAD = 6
MIN_THUMB = 120
# Comfortable first-open size for a filled 6×4 page (+ chrome).
DEFAULT_GEOMETRY = "1100x900"
USER_AGENT = "HaremLinkBridge/1.3 (+roster preview)"


def compute_thumb(width: int, height: int, *, cols: int = COLS, rows: int = ROWS) -> int:
    """Largest square thumb that fits cols×rows into the given area."""
    if width < 80 or height < 80:
        return 140
    cell_w = max(1, width // cols)
    cell_h = max(1, height // rows)
    tw = cell_w - CELL_PAD * 2
    th = cell_h - NAME_RESERVE - CELL_PAD * 2
    return max(MIN_THUMB, min(tw, th))
