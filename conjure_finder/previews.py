"""Fetch and cache low-res post previews for the Bulk wishlist UI."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import httpx

from conjure_finder.bootstrap import ROOT

CACHE_DIR = ROOT / "conjure_finder_preview_cache"
THUMB_SIZE = (110, 110)


def _cache_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    # Keep extension hint for debugging; Pillow doesn't need it.
    return CACHE_DIR / f"{digest}.img"


def fetch_preview_bytes(url: str, *, timeout: float = 20.0) -> bytes | None:
    """Download preview image bytes, using a simple disk cache."""
    url = (url or "").strip()
    if not url.startswith("http"):
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url)
    if path.exists() and path.stat().st_size > 0:
        try:
            return path.read_bytes()
        except OSError:
            pass
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url)
            if r.status_code != 200 or not r.content:
                return None
            data = r.content
        path.write_bytes(data)
        return data
    except Exception:
        return None


def make_photoimage(url: str, *, size: tuple[int, int] = THUMB_SIZE):
    """Return a tkinter PhotoImage (via Pillow), or None on failure.

    Caller must keep a reference to the returned image.
    """
    data = fetch_preview_bytes(url)
    if not data:
        return None
    try:
        from PIL import Image, ImageTk
    except ImportError:
        return None
    try:
        img = Image.open(BytesIO(data))
        img = img.convert("RGB")
        img.thumbnail(size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None
