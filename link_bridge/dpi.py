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


# Modest extra on top of display DPI. 2.0 used to double-count DPI and freeze Setup.
UI_SCALE_MIN = 0.90
UI_SCALE_MAX = 1.50
UI_SCALE_STEP = 0.05
# Buttons/labels. Typed fields + set names use body (Tk rounds 9pt so 95???105 looked identical).
UI_FONT_PT = 9
BODY_FONT_PT = 12


def clamp_ui_scale(raw: object) -> float:
    try:
        value = float(raw)
    except Exception:
        value = 1.0
    steps = round(value / UI_SCALE_STEP)
    value = round(steps * UI_SCALE_STEP, 2)
    return max(UI_SCALE_MIN, min(UI_SCALE_MAX, value))


def windows_effective_dpi(root: tk.Misc) -> float:
    """Windows *display* scale (96 at 100%). Never use winfo_fpixels ??? that is panel DPI."""
    if sys.platform == "win32":
        try:
            import ctypes

            hwnd = int(root.winfo_id())
            if hwnd:
                dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
                if dpi >= 96:
                    return float(dpi)
        except Exception:
            pass
        try:
            import ctypes

            dpi = int(ctypes.windll.user32.GetDpiForSystem())
            if dpi >= 96:
                return float(dpi)
        except Exception:
            pass
    return 96.0


def font_px(pt: float, extra: float, dpi: float) -> int:
    """Pixel font size for extra scale. Point sizes stay stuck at 9pt across 95???105%."""
    extra = clamp_ui_scale(extra)
    try:
        dpi_f = float(dpi)
    except Exception:
        dpi_f = 96.0
    if dpi_f < 72:
        dpi_f = 96.0
    return max(7, int(round(float(pt) * extra * dpi_f / 72.0)))


def apply_ui_scale(root: tk.Misc, extra: float = 1.0) -> float:
    """DPI via tk scaling; extra via pixel fonts on ttk chrome (buttons/tabs ignore tk scaling)."""
    extra = clamp_ui_scale(extra)
    dpi = windows_effective_dpi(root)
    # Extra is NOT applied here ??? ttk Labels/Buttons/tabs ignore it on Windows.
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass
    ui_px = font_px(UI_FONT_PT, extra, dpi)
    body_px = font_px(BODY_FONT_PT, extra, dpi)
    small_px = font_px(8, extra, dpi)
    log_px = font_px(10, extra, dpi)
    for name, px, family in (
        ("TkDefaultFont", ui_px, "Segoe UI"),
        ("TkMenuFont", ui_px, "Segoe UI"),
        ("TkHeadingFont", ui_px, "Segoe UI"),
        ("TkCaptionFont", ui_px, "Segoe UI"),
        ("TkSmallCaptionFont", small_px, "Segoe UI"),
        ("TkIconFont", ui_px, "Segoe UI"),
        ("TkTextFont", body_px, "Segoe UI"),
        ("TkFixedFont", log_px, "Consolas"),
    ):
        try:
            font = tkfont.nametofont(name)
            font.configure(family=family, size=-px)
        except Exception:
            continue
    try:
        from tkinter import ttk

        style = ttk.Style(root)
        pad_btn = (max(6, int(round(10 * extra))), max(2, int(round(4 * extra))))
        pad_tab = (max(8, int(round(12 * extra))), max(2, int(round(4 * extra))))
        pad_tool = (max(5, int(round(8 * extra))), max(2, int(round(3 * extra))))
        pad_omni_btn = (max(5, int(round(8 * extra))), max(1, int(round(2 * extra))))
        pad_omni_tab = (max(6, int(round(10 * extra))), max(2, int(round(3 * extra))))
        style.configure("TLabel", font="TkDefaultFont")
        style.configure("TButton", font="TkDefaultFont", padding=pad_btn)
        style.configure("TCheckbutton", font="TkDefaultFont")
        style.configure("TRadiobutton", font="TkDefaultFont")
        style.configure("TMenubutton", font="TkDefaultFont")
        style.configure("Toolbutton", font="TkDefaultFont", padding=pad_tool)
        style.configure("TNotebook.Tab", font="TkDefaultFont", padding=pad_tab)
        style.configure("TLabelframe.Label", font="TkSmallCaptionFont")
        style.configure("TEntry", font="TkTextFont")
        style.configure("TCombobox", font="TkTextFont")
        style.configure("TSpinbox", font="TkTextFont")
        style.configure("Treeview", font="TkDefaultFont")
        style.configure("Treeview.Heading", font="TkDefaultFont")
        style.configure("Omni.TLabel", font="TkDefaultFont")
        style.configure("Omni.Muted.TLabel", font="TkSmallCaptionFont")
        style.configure("Omni.TCheckbutton", font="TkDefaultFont")
        style.configure("Omni.TButton", font="TkDefaultFont", padding=pad_omni_btn)
        style.configure("Omni.TNotebook.Tab", font="TkDefaultFont", padding=pad_omni_tab)
    except Exception:
        pass
    try:
        root._bridge_ui_scale = extra  # type: ignore[attr-defined]
    except Exception:
        pass
    return extra
