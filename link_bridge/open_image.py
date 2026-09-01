"""Open a full-size preview in the OS image viewer (Honeyview, Photos, …)."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

USER_AGENT = "HaremLinkBridge/1.3 (+full image)"
_TEMP_DIR = Path(tempfile.gettempdir()) / "HaremLinkBridgeImages"
_lock = threading.Lock()


def _ext_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm"):
        if path.endswith(ext):
            return ext
    return ".jpg"


def _download(url: str) -> Path | None:
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    key = abs(hash(url)) % (10**12)
    dest = _TEMP_DIR / f"img_{key}{_ext_from_url(url)}"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    if not data:
        return None
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def _open_path(path: Path) -> None:
    # Windows: respects the user's default association (Honeyview, etc.).
    os.startfile(str(path))  # type: ignore[attr-defined]


def _show_internal(path: Path) -> None:
    """Fallback Tk viewer when no OS association works."""
    import tkinter as tk

    from PIL import Image, ImageTk

    root = tk.Toplevel()
    root.title(path.name)
    im = Image.open(path)
    # Fit loosely into a large window without upscaling tiny thumbs endlessly.
    max_w, max_h = 1400, 1000
    w, h = im.size
    scale = min(max_w / max(w, 1), max_h / max(h, 1), 1.0)
    if scale < 0.999:
        im = im.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.BILINEAR,
        )
    photo = ImageTk.PhotoImage(im)
    lbl = tk.Label(root, image=photo)
    lbl.image = photo  # type: ignore[attr-defined]
    lbl.pack()
    root.bind("<Escape>", lambda _e: root.destroy())
    from link_bridge.window_keys import bind_q_close

    bind_q_close(root)


def open_full_image(url: str, *, on_err: Callable[[BaseException], None] | None = None) -> None:
    """Download ``url`` off-thread and open it in the default image viewer."""
    target = (url or "").strip()
    if not target:
        if on_err:
            on_err(ValueError("no image url"))
        return

    def worker() -> None:
        try:
            with _lock:
                path = _download(target)
            if path is None:
                raise RuntimeError("empty download")
            try:
                _open_path(path)
            except Exception:
                logger.debug("os image open failed — internal viewer", exc_info=True)
                # Schedule Tk work on main thread if possible.
                try:
                    import tkinter as tk

                    root = tk._default_root  # type: ignore[attr-defined]
                    if root is not None:
                        root.after(0, lambda p=path: _show_internal(p))
                    else:
                        _show_internal(path)
                except Exception as exc:
                    if on_err:
                        on_err(exc)
        except Exception as exc:
            logger.debug("open_full_image failed: %s", exc, exc_info=True)
            if on_err:
                on_err(exc)

    threading.Thread(target=worker, name="hlb-open-img", daemon=True).start()
