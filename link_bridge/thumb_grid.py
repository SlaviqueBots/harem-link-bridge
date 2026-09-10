"""Shared 6×4 fill-viewport thumb sizing + preview fetch/cache helpers."""

from __future__ import annotations

import io
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

COLS = 6
ROWS = 4  # square-mode viewport sizing only
PAGE_SIZE = 96  # enough for a dense scroll without freezing Tk on tab switches
NAME_RESERVE = 22
CELL_PAD = 6
MIN_THUMB = 120
# Comfortable first-open size for a filled 6×4 page (+ chrome).
DEFAULT_GEOMETRY = "1100x900"
USER_AGENT = "HaremLinkBridge/1.3 (+roster preview)"

# Bound memory + download concurrency so long sessions keep showing previews.
_MAX_CACHE_ENTRIES = 800  # ≥ two roster pages + sets headroom
_MAX_CACHE_BYTES = 256 * 1024 * 1024  # 256 MiB
_FETCH_WORKERS = 16

logger = logging.getLogger(__name__)


def enable_default_disk_cache() -> None:
    """In-memory preview cache is always on; disk cache is optional."""
    return

_fetch_pool = ThreadPoolExecutor(
    max_workers=_FETCH_WORKERS, thread_name_prefix="hlb-thumb"
)
_cache_lock = threading.Lock()
_byte_cache: OrderedDict[str, bytes] = OrderedDict()
_cache_bytes = 0
_inflight: dict[str, list[tuple[Callable[[bytes], None], Callable[[BaseException], None] | None]]] = {}
_aspect_cache: OrderedDict[str, float] = OrderedDict()
_MAX_ASPECT_ENTRIES = 2000


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
            try:
                from link_bridge import image_cache

                data = image_cache.get(key)
            except Exception:
                data = None
            if data is None:
                return None
            _byte_cache[key] = data
            global _cache_bytes
            _cache_bytes += len(data)
            return data
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
    # Permanent on-device copy (opt-in toggle; no-op when off).
    try:
        from link_bridge import image_cache

        image_cache.put(key, data)
    except Exception:
        pass


def cache_clear() -> None:
    global _cache_bytes
    with _cache_lock:
        _byte_cache.clear()
        _cache_bytes = 0
        _aspect_cache.clear()
        _inflight.clear()


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


def open_rgb(data: bytes):
    """Decode image bytes with EXIF orientation applied."""
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(data))
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass
    return im.convert("RGB")


def decode_thumb(data: bytes, size: int, *, natural: bool = False) -> Any:
    """Decode preview bytes into a Tk photo.

    ``natural=False`` (default): center-crop to a square (current look).
    ``natural=True``: fit inside the square box, keep aspect (legacy helper).
    """
    from PIL import Image, ImageOps, ImageTk

    im = open_rgb(data)
    box = (max(1, int(size)), max(1, int(size)))
    if natural:
        im = ImageOps.contain(im, box, method=Image.Resampling.LANCZOS)
    else:
        im = ImageOps.fit(im, box, method=Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(im)


def peek_aspect(url: str) -> float | None:
    key = (url or "").strip()
    if not key:
        return None
    with _cache_lock:
        hit = _aspect_cache.get(key)
        if hit is None:
            return None
        _aspect_cache.move_to_end(key)
        return hit


def image_aspect(data: bytes, *, cache_key: str = "") -> float:
    key = (cache_key or "").strip()
    if key:
        with _cache_lock:
            hit = _aspect_cache.get(key)
            if hit is not None:
                _aspect_cache.move_to_end(key)
                return hit
    im = open_rgb(data)
    w, h = im.size
    if h <= 0 or w <= 0:
        return 1.0
    aspect = float(w) / float(h)
    if key:
        with _cache_lock:
            _aspect_cache[key] = aspect
            _aspect_cache.move_to_end(key)
            while len(_aspect_cache) > _MAX_ASPECT_ENTRIES:
                _aspect_cache.popitem(last=False)
    return aspect


def decode_thumb_sized(data: bytes, width: int, height: int) -> Any:
    """Resize to an exact pixel box (aspect already chosen by the layout)."""
    from PIL import Image, ImageTk

    w = max(1, int(width))
    h = max(1, int(height))
    im = open_rgb(data)
    # BILINEAR is much cheaper than LANCZOS for hundreds of gallery tiles.
    resample = Image.Resampling.BILINEAR
    # Never distort: fit inside the box (letterbox if drift left a 1px mismatch).
    src_a = im.size[0] / max(1, im.size[1])
    box_a = w / h
    if abs(src_a - box_a) > 0.02:
        # Prefer contain into the assigned box rather than stretch.
        from PIL import ImageOps

        im = ImageOps.contain(im, (w, h), method=resample)
        return ImageTk.PhotoImage(im)
    im = im.resize((w, h), resample)
    return ImageTk.PhotoImage(im)


def _request_headers(url: str) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "image/*,*/*"}
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("rule34.xxx"):
        headers["Referer"] = "https://rule34.xxx/"
    return headers


def alt_r34_cdn_url(url: str) -> str:
    """Same path on api-cdn when wimg/img is 403 on the user's network."""
    raw = (url or "").strip()
    host = (urlparse(raw).hostname or "").lower()
    if host in ("wimg.rule34.xxx", "img.rule34.xxx"):
        return raw.replace(host, "api-cdn.rule34.xxx", 1)
    return ""


def fetch_url_bytes(
    url: str, *, timeout: float = 18.0, retries: int = 3
) -> bytes:
    """Download preview bytes; retry transient network / HTTP errors.

    C5: blocks + sleeps between retries - NEVER call on the Tk UI thread,
    only via schedule_thumb_fetch / worker threads.
    """
    key = (url or "").strip()
    if not key:
        raise ValueError("empty url")
    last: BaseException | None = None
    attempts = max(1, int(retries))
    candidates = [key]
    alt = alt_r34_cdn_url(key)
    if alt and alt not in candidates:
        candidates.append(alt)
    for target in candidates:
        last = None
        for i in range(attempts):
            try:
                req = urllib.request.Request(target, headers=_request_headers(target))
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    data = resp.read()
                if not data:
                    raise ValueError("empty body")
                if "text/html" in ctype:
                    raise ValueError("html body")
                return data
            except Exception as exc:
                last = exc
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (404, 410):
                    break
                if i + 1 < attempts:
                    time.sleep(0.35 * (i + 1))
        if isinstance(last, urllib.error.HTTPError) and last.code == 403:
            # B15: falling over to the alt CDN host - log it so a persistent
            # block reads as "blocked host", not "slow load".
            logger.info("thumb 403 on %s, trying alt CDN host", target)
            continue
        if alt and target == key:
            continue
        break
    assert last is not None
    raise last


def schedule_thumb_fetch(
    url: str,
    *,
    on_data: Callable[[bytes], None],
    on_err: Callable[[BaseException], None] | None = None,
) -> None:
    """Fetch in the shared pool; invoke callbacks on the worker thread.

    Concurrent requests for the same URL share one download.
    """
    key = (url or "").strip()
    if not key:
        if on_err is not None:
            on_err(ValueError("empty url"))
        return

    cached = cache_get(key)
    if cached is not None:
        _fetch_pool.submit(on_data, cached)
        return

    with _cache_lock:
        waiters = _inflight.get(key)
        if waiters is not None:
            waiters.append((on_data, on_err))
            return
        _inflight[key] = [(on_data, on_err)]

    def worker() -> None:
        err: BaseException | None = None
        data: bytes | None = None
        try:
            cached2 = cache_get(key)
            if cached2 is not None:
                data = cached2
            else:
                data = fetch_url_bytes(key)
                cache_put(key, data)
        except Exception as exc:
            err = exc
            logger.debug("thumb failed %s: %s", key[:60], exc)
        with _cache_lock:
            waiters = _inflight.pop(key, [])
        for ok_cb, err_cb in waiters:
            try:
                if err is not None:
                    if err_cb is not None:
                        err_cb(err)
                elif data is not None:
                    ok_cb(data)
            except Exception:
                logger.debug("thumb callback failed", exc_info=True)

    _fetch_pool.submit(worker)


def schedule_aspect(
    data: bytes,
    *,
    cache_key: str,
    on_done: Callable[[float], None],
) -> None:
    """Compute aspect off the UI thread."""

    def worker() -> None:
        try:
            a = image_aspect(data, cache_key=cache_key)
        except Exception:
            a = 1.0
        try:
            on_done(a)
        except Exception:
            logger.debug("aspect callback failed", exc_info=True)

    _fetch_pool.submit(worker)
