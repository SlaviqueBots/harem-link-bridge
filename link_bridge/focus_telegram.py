"""Bring Telegram Desktop to the foreground (optional; no deep links)."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_TELEGRAM_EXES = ("telegram.exe", "telegram desktop.exe")


def focus_telegram() -> bool:
    """Focus a visible Telegram.exe window. Returns True if one was raised."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[int] = []

    def _exe_name(pid: int) -> str:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            if not ok:
                return ""
            path = buf.value.replace("/", "\\")
            return path.rsplit("\\", 1)[-1].lower()
        finally:
            kernel32.CloseHandle(handle)

    @WNDENUMPROC
    def _enum(hwnd, _lp):  # type: ignore[misc]
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _exe_name(int(pid.value))
        if name in _TELEGRAM_EXES:
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(_enum, 0)
    if not found:
        return False
    hwnd = found[0]
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        return True
    except Exception:
        logger.debug("focus_telegram failed", exc_info=True)
        return False
