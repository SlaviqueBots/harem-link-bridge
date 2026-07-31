"""Windows session lock/unlock (Win+L) notifications via WTS APIs."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0
DESKTOP_SWITCHDESKTOP = 0x0100
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002


class LockPauseController:
    """Decide pause/resume for lock events without fighting a manual Pause."""

    def __init__(self) -> None:
        self.auto_paused = False

    def on_lock(self, currently_paused: bool) -> bool | None:
        """Return new paused value, or None if unchanged."""
        if currently_paused:
            self.auto_paused = False
            return None
        self.auto_paused = True
        return True

    def on_unlock(self) -> bool | None:
        if not self.auto_paused:
            return None
        self.auto_paused = False
        return False

    def on_manual_change(self) -> None:
        """User toggled Pause — do not auto-resume/pause for this lock cycle."""
        self.auto_paused = False


def is_session_locked() -> bool:
    """Best-effort: True when the interactive desktop looks locked."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if handle:
            user32.CloseDesktop(handle)
            return False
        return True
    except Exception:
        logger.debug("is_session_locked probe failed", exc_info=True)
        return False


class SessionLockWatcher:
    """Background message window that fires callbacks on lock / unlock."""

    def __init__(
        self,
        *,
        on_lock: Callable[[], None],
        on_unlock: Callable[[], None],
    ) -> None:
        self._on_lock = on_lock
        self._on_unlock = on_unlock
        self._thread: threading.Thread | None = None
        self._hwnd: int = 0
        self._stop = threading.Event()
        self._wnd_proc_ref: Any = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        if self.running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="session-lock-watcher", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        hwnd = self._hwnd
        if hwnd and sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                logger.debug("PostMessage WM_CLOSE failed", exc_info=True)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None
        self._hwnd = 0

    def _run(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            logger.warning("ctypes unavailable — session lock watch disabled")
            return

        user32 = ctypes.windll.user32
        wtsapi32 = ctypes.windll.wtsapi32
        kernel32 = ctypes.windll.kernel32

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = LRESULT

        def _wnd_proc(hwnd, msg, wparam, lparam):  # type: ignore[no-untyped-def]
            if msg == WM_WTSSESSION_CHANGE:
                if int(wparam) == WTS_SESSION_LOCK:
                    try:
                        self._on_lock()
                    except Exception:
                        logger.exception("on_lock callback failed")
                elif int(wparam) == WTS_SESSION_UNLOCK:
                    try:
                        self._on_unlock()
                    except Exception:
                        logger.exception("on_unlock callback failed")
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wnd_proc = WNDPROC(_wnd_proc)
        self._wnd_proc_ref = wnd_proc

        class_name = f"HaremLinkBridgeSessionLock_{id(self)}"
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASS()
        wc.style = 0
        wc.lpfnWndProc = wnd_proc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = class_name
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            err = int(kernel32.GetLastError() or 0)
            if err not in (0, 1410):  # ERROR_CLASS_ALREADY_EXISTS
                logger.warning("RegisterClassW failed (%s)", err)

        hwnd = user32.CreateWindowExW(
            0,
            class_name,
            "HaremLinkBridgeSessionLock",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinst,
            None,
        )
        if not hwnd:
            err = int(kernel32.GetLastError() or 0)
            logger.warning("CreateWindowExW failed (%s) — session lock watch disabled", err)
            return
        self._hwnd = int(hwnd)
        if not wtsapi32.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION):
            logger.warning("WTSRegisterSessionNotification failed")
            user32.DestroyWindow(hwnd)
            self._hwnd = 0
            return

        msg = wintypes.MSG()
        while not self._stop.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        try:
            wtsapi32.WTSUnRegisterSessionNotification(hwnd)
        except Exception:
            pass
        self._hwnd = 0
