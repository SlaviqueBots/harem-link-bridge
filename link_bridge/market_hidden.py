"""Client-side hidden market lots (persisted beside bridge config)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from link_bridge.config import app_dir

_LOCK = threading.Lock()
_PATH = app_dir() / "market_hidden.json"
_CACHE: set[int] | None = None


def _read() -> set[int]:
    global _CACHE
    if _CACHE is not None:
        return set(_CACHE)
    ids: set[int] = set()
    try:
        if _PATH.is_file():
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
            for x in raw.get("listing_ids") or []:
                try:
                    lid = int(x)
                    if lid > 0:
                        ids.add(lid)
                except (TypeError, ValueError):
                    continue
    except Exception:
        ids = set()
    _CACHE = ids
    return set(ids)


def _write(ids: set[int]) -> None:
    global _CACHE
    clean = sorted({int(x) for x in ids if int(x) > 0})
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(
        json.dumps({"listing_ids": clean}, indent=2),
        encoding="utf-8",
    )
    _CACHE = set(clean)


def hidden_listing_ids() -> set[int]:
    with _LOCK:
        return _read()


def is_hidden(listing_id: int) -> bool:
    try:
        lid = int(listing_id)
    except (TypeError, ValueError):
        return False
    if lid <= 0:
        return False
    with _LOCK:
        return lid in _read()


def toggle_hidden(listing_id: int) -> bool:
    """Toggle lot visibility; returns True if now hidden."""
    lid = int(listing_id)
    with _LOCK:
        ids = _read()
        if lid in ids:
            ids.discard(lid)
            _write(ids)
            return False
        ids.add(lid)
        _write(ids)
        return True


def set_hidden(listing_id: int, hidden: bool) -> None:
    lid = int(listing_id)
    with _LOCK:
        ids = _read()
        if hidden:
            ids.add(lid)
        else:
            ids.discard(lid)
        _write(ids)
