"""Windows DPI + Tk scaling so 4K screens don't get a bitmap-stretched blur."""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont

_DPI_SET = False


def enable_dpi_awareness() -> None:
    """Must run before the first Tk() window. No-op on non-Windows."""
    global _DPI_SET
    if _DPI_SET or sys.platform != "win32":
        return
    try:
        import ctypes

        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        _DPI_SET = True
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
        _DPI_SET = True
    except Exception:
        pass


def clamp_ui_scale(raw: object) -> float:
    try:
        value = float(raw)
    except Exception:
        value = 1.0
    return max(0.75, min(2.0, round(value, 2)))


def apply_ui_scale(root: tk.Misc, extra: float = 1.0) -> float:
    """Map physical DPI + user extra scale onto Tk (and default fonts)."""
    extra = clamp_ui_scale(extra)
    try:
        dpi = float(root.winfo_fpixels("1i") or 96.0)
    except Exception:
        dpi = 96.0
    if dpi <= 1:
        dpi = 96.0
    scaling = (dpi / 72.0) * extra
    try:
        root.tk.call("tk", "scaling", scaling)
    except Exception:
        pass
    # Segoe UI stays crisp on Windows ClearType; size follows 96dpi-relative extra.
    size = max(8, int(round(9 * extra * (dpi / 96.0))))
    for name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkMenuFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkSmallCaptionFont",
        "TkIconFont",
    ):
        try:
            font = tkfont.nametofont(name)
            font.configure(family="Segoe UI", size=size)
        except Exception:
            continue
    try:
        from tkinter import ttk

        ttk.Style(root).configure(".", font=("Segoe UI", size))
    except Exception:
        pass
    try:
        root._bridge_ui_scale = extra  # type: ignore[attr-defined]
    except Exception:
        pass
    return extra
