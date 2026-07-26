"""Open URLs in the system browser (Windows: bring browser to the front)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser

logger = logging.getLogger(__name__)

_SW_SHOWNORMAL = 1


def open_url(url: str) -> None:
    """Open ``url`` and try to bring the browser to the foreground."""
    url = (url or "").strip()
    if not url:
        return
    if sys.platform == "win32":
        _open_windows_focused(url)
        return
    webbrowser.open(url)


def _open_windows_focused(url: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)
    except Exception:
        logger.debug("AllowSetForegroundWindow failed", exc_info=True)
    try:
        import ctypes

        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "open", url, None, None, _SW_SHOWNORMAL
        )
        if int(rc) > 32:
            return
    except Exception:
        logger.debug("ShellExecuteW focus failed", exc_info=True)
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
