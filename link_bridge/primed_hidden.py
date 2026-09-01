"""Client-side hidden primed cards (persisted beside bridge config)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from link_bridge.config import app_dir

_LOCK = threading.Lock()
_PATH = app_dir() / "primed_hidden.json"
_CACHE: set[int] | None = None


def _read() -> set[int]:
    global _CACHE
    if _CACHE is not None:
        return set(_CACHE)
    ids: set[int] = set()
    try:
        if _PATH.is_file():
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
            for x in raw.get("character_ids") or []:
                try:
                    cid = int(x)
                    if cid > 0:
                        ids.add(cid)
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
        json.dumps({"character_ids": clean}, indent=2),
        encoding="utf-8",
    )
    _CACHE = set(clean)


def hidden_character_ids() -> set[int]:
    with _LOCK:
        return _read()


def is_hidden(character_id: int) -> bool:
    try:
        cid = int(character_id)
    except (TypeError, ValueError):
        return False
    if cid <= 0:
        return False
    with _LOCK:
        return cid in _read()


def toggle_hidden(character_id: int) -> bool:
    """Toggle card visibility; returns True if now hidden."""
    cid = int(character_id)
    with _LOCK:
        ids = _read()
        if cid in ids:
            ids.discard(cid)
            _write(ids)
            return False
        ids.add(cid)
        _write(ids)
        return True


def set_hidden(character_id: int, hidden: bool) -> None:
    cid = int(character_id)
    with _LOCK:
        ids = _read()
        if hidden:
            ids.add(cid)
        else:
            ids.discard(cid)
        _write(ids)
