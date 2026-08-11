"""Shared 6×4 fill-viewport thumb sizing + preview fetch/cache helpers."""

from __future__ import annotations

import io
import logging
import threading
import urllib.request
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

COLS = 6
ROWS = 4
PAGE_SIZE = COLS * ROWS  # 24
NAME_RESERVE = 22
CELL_PAD = 6
MIN_THUMB = 120
# Comfortable first-open size for a filled 6×4 page (+ chrome).
DEFAULT_GEOMETRY = "1100x900"
USER_AGENT = "HaremLinkBridge/1.3 (+roster preview)"

# Bound memory + download concurrency so long sessions keep showing previews.
_MAX_CACHE_ENTRIES = 120
_MAX_CACHE_BYTES = 64 * 1024 * 1024  # 64 MiB of raw preview bytes
_FETCH_WORKERS = 8

logger = logging.getLogger(__name__)

_fetch_pool = ThreadPoolExecutor(
    max_workers=_FETCH_WORKERS, thread_name_prefix="hlb-thumb"
)
_cache_lock = threading.Lock()
_byte_cache: OrderedDict[str, bytes] = OrderedDict()
_cache_bytes = 0


def compute_thumb(width: int, height: int, *, cols: int = COLS, rows: int = ROWS) -> int:
    """Largest square thumb that fits cols×rows into the given area."""
    if width < 80 or height < 80:
        return 140
    cell_w = max(1, width // cols)
    cell_h = max(1, height // rows)
    tw = cell_w - CELL_PAD * 2
    th = cell_h - NAME_RESERVE - CELL_PAD * 2
    return max(MIN_THUMB, min(tw, th))


def cache_get(url: str) -> bytes | None:
    key = (url or "").strip()
    if not key:
        return None
    with _cache_lock:
        data = _byte_cache.get(key)
        if data is None:
            return None
        _byte_cache.move_to_end(key)
        return data


def cache_put(url: str, data: bytes) -> None:
    key = (url or "").strip()
    if not key or not data:
        return
    global _cache_bytes
    with _cache_lock:
        old = _byte_cache.pop(key, None)
        if old is not None:
            _cache_bytes -= len(old)
        _byte_cache[key] = data
        _cache_bytes += len(data)
        while _byte_cache and (
            len(_byte_cache) > _MAX_CACHE_ENTRIES or _cache_bytes > _MAX_CACHE_BYTES
        ):
            _, evicted = _byte_cache.popitem(last=False)
            _cache_bytes -= len(evicted)


def cache_clear() -> None:
    global _cache_bytes
    with _cache_lock:
        _byte_cache.clear()
        _cache_bytes = 0


def cache_stats() -> tuple[int, int]:
    with _cache_lock:
        return len(_byte_cache), int(_cache_bytes)


def release_photos(photos: list[Any]) -> None:
    """Drop Tk photo handles immediately (GC alone is too slow on long runs)."""
    for photo in photos:
        try:
            inner = getattr(photo, "_PhotoImage__photo", photo)
            name = getattr(inner, "name", None) or getattr(photo, "name", None)
            tk = getattr(inner, "tk", None) or getattr(photo, "tk", None)
            if name and tk is not None:
                tk.call("image", "delete", name)
                try:
                    inner.name = None  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception:
            pass
    photos.clear()


def decode_thumb(data: bytes, size: int) -> Any:
    from PIL import Image, ImageOps, ImageTk

    im = Image.open(io.BytesIO(data)).convert("RGB")
    im = ImageOps.fit(im, (size, size), method=Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(im)


def fetch_url_bytes(url: str, *, timeout: float = 12.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def schedule_thumb_fetch(
    url: str,
    *,
    on_data: Callable[[bytes], None],
    on_err: Callable[[BaseException], None] | None = None,
) -> None:
    """Fetch in the shared pool; invoke callbacks on the worker thread."""

    def worker() -> None:
        try:
            cached = cache_get(url)
            if cached is not None:
                on_data(cached)
                return
            data = fetch_url_bytes(url)
            cache_put(url, data)
            on_data(data)
        except Exception as exc:
            logger.debug("thumb failed %s: %s", url[:60], exc)
            if on_err is not None:
                on_err(exc)

    _fetch_pool.submit(worker)
