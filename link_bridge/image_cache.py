"""Permanent on-device picture cache (opt-in via Setup toggle).

Every preview and full image the user looks at is stored under a URL-keyed
filename and never evicted, so reopening is instant with no re-download.
Off by default (memory-only + temp-dir behaviour stays until enabled).

Thread-safe; reads are small-file loads, writes are atomic (.part + replace).
No Tk imports - safe in worker threads and unit tests.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_enabled = False
_cache_dir: Path | None = None


def _default_dir() -> Path:
    return Path.home() / "HaremLinkBridgeCache"


def configure(*, enabled: bool | None = None, directory: Path | str | None = None) -> None:
    """Set the toggle and/or override the directory (tests use a tmp dir)."""
    global _enabled, _cache_dir
    with _lock:
        if enabled is not None:
            _enabled = bool(enabled)
        if directory is not None:
            _cache_dir = Path(directory)


def is_enabled() -> bool:
    with _lock:
        return _enabled


def cache_dir() -> Path:
    with _lock:
        base = _cache_dir if _cache_dir is not None else _default_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _ext_for(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4"):
        if path.endswith(ext):
            return ext
    return ".jpg"


def _path_for(url: str) -> Path | None:
    key = (url or "").strip()
    if not key.startswith("http"):
        return None
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return cache_dir() / f"{digest}{_ext_for(key)}"


def get(url: str) -> bytes | None:
    """Stored bytes for a previously seen URL, or None (toggle off = miss)."""
    if not is_enabled():
        return None
    try:
        path = _path_for(url)
        if path is None or not path.is_file():
            return None
        data = path.read_bytes()
        return data or None
    except Exception:
        logger.debug("image cache read failed", exc_info=True)
        return None


def put(url: str, data: bytes) -> bool:
    """Store bytes for a URL. No-op when the toggle is off."""
    if not is_enabled():
        return False
    if not data:
        return False
    try:
        path = _path_for(url)
        if path is None:
            return False
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(bytes(data))
        tmp.replace(path)
        return True
    except Exception:
        logger.debug("image cache write failed", exc_info=True)
        return False


def cache_size_bytes() -> int:
    try:
        base = cache_dir()
    except Exception:
        return 0
    total = 0
    try:
        for child in base.iterdir():
            if child.is_file() and not child.suffix == ".part":
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def file_count() -> int:
    try:
        base = cache_dir()
    except Exception:
        return 0
    try:
        return sum(1 for c in base.iterdir() if c.is_file() and c.suffix != ".part")
    except OSError:
        return 0


def clear() -> int:
    """Delete all stored pictures. Returns files removed."""
    try:
        base = cache_dir()
    except Exception:
        return 0
    removed = 0
    try:
        for child in list(base.iterdir()):
            if child.is_file():
                try:
                    child.unlink()
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    return removed


def format_size(nbytes: int) -> str:
    n = max(0, int(nbytes))
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"
