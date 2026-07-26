"""Open URLs in the system browser, optionally forcing focus (Windows)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser

logger = logging.getLogger(__name__)

# Win32 ShowWindow / ShellExecute nShow
_SW_SHOWNORMAL = 1
_SW_SHOWNOACTIVATE = 4


def open_url(url: str, *, focus: bool = True) -> None:
    """Open ``url``.

    ``focus=True`` (default): bring the browser to the foreground when possible.
    ``focus=False``: open without stealing focus (best-effort on Windows).
    """
    url = (url or "").strip()
    if not url:
        return
    if sys.platform == "win32":
        if focus:
            _open_windows_focused(url)
        else:
            _open_windows_quiet(url)
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


def _open_windows_quiet(url: str) -> None:
    """Open without keeping the browser in front.

    ``webbrowser.open`` / ``os.startfile`` on Windows usually activate the
    browser. We ShellExecute with SW_SHOWNOACTIVATE, then restore the previous
    foreground window (Chrome/Edge often still flash-activate asynchronously).
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        prev = user32.GetForegroundWindow()
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "open", url, None, None, _SW_SHOWNOACTIVATE
        )
        if int(rc) <= 32:
            raise OSError(f"ShellExecuteW failed: {rc}")
        if prev:
            threading.Thread(
                target=_restore_foreground, args=(int(prev),), daemon=True
            ).start()
        return
    except Exception:
        logger.debug("quiet ShellExecute failed; falling back", exc_info=True)
    # Last resort — still try to give focus back.
    try:
        import ctypes

        prev = ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        prev = 0
    webbrowser.open(url)
    if prev:
        threading.Thread(
            target=_restore_foreground, args=(int(prev),), daemon=True
        ).start()


def _restore_foreground(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        for _ in range(15):
            time.sleep(0.08)
            cur = user32.GetForegroundWindow()
            if cur != hwnd:
                # Brief attach helps when Windows blocks SetForegroundWindow.
                try:
                    foreground = cur or hwnd
                    pid_fore = ctypes.c_ulong()
                    tid_fore = user32.GetWindowThreadProcessId(
                        foreground, ctypes.byref(pid_fore)
                    )
                    tid_self = user32.GetWindowThreadProcessId(
                        hwnd, ctypes.byref(ctypes.c_ulong())
                    )
                    user32.AttachThreadInput(tid_fore, tid_self, True)
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
                    user32.AttachThreadInput(tid_fore, tid_self, False)
                except Exception:
                    user32.SetForegroundWindow(hwnd)
    except Exception:
        logger.debug("restore foreground failed", exc_info=True)
