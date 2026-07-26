"""Open URLs in the system browser, optionally forcing focus (Windows)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser

logger = logging.getLogger(__name__)


def open_url(url: str, *, focus: bool = True) -> None:
    """Open ``url``.

    ``focus=True`` (default): try to bring the browser to the foreground.
    ``focus=False``: previous quiet behaviour (``webbrowser.open``, often a
    background tab).
    """
    url = (url or "").strip()
    if not url:
        return
    if not focus:
        webbrowser.open(url)
        return
    if sys.platform == "win32":
        _open_windows_focused(url)
        return
    webbrowser.open(url)


def _open_windows_focused(url: str) -> None:
    try:
        import ctypes

        # ASFW_ANY — let the browser steal focus from our tray/GUI process.
        ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)
    except Exception:
        logger.debug("AllowSetForegroundWindow failed", exc_info=True)
    try:
        os.startfile(url)  # type: ignore[attr-defined]
        return
    except OSError:
        logger.debug("os.startfile failed for %s", url, exc_info=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["cmd.exe", "/c", "start", "", url],
        close_fds=True,
        creationflags=flags,
    )
