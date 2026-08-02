"""Ensure only one Harem Link Bridge process runs on this PC."""

from __future__ import annotations

import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\HaremLinkBridgeSingleton"
_handle: Any = None


def acquire_singleton() -> bool:
    """Return True if this process owns the singleton; False if another is running."""
    global _handle
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            logger.warning("CreateMutexW failed — continuing without singleton")
            return True
        err = int(kernel32.GetLastError() or 0)
        _handle = handle
        # ERROR_ALREADY_EXISTS = 183
        if err == 183:
            return False
        return True
    except Exception:
        logger.debug("singleton mutex unavailable", exc_info=True)
        return True


def release_singleton() -> None:
    global _handle
    if _handle is None or sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(_handle)
    except Exception:
        pass
    _handle = None
