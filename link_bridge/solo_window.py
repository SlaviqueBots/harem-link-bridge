"""Standalone alert root for windows that must never map the main window.

Tournament alarm, update ask/progress/error boxes: parenting them to the
(possibly minimized or tray-hidden) main window restores it and drops its
zoomed state. A dedicated hidden Tk root parents them instead — the main
window stays exactly as it was. All calls are UI-thread only.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

_root: tk.Tk | None = None


def root() -> tk.Tk:
    """Hidden Tk root owning standalone alerts. Created on demand."""
    global _root
    try:
        alive = _root is not None and bool(_root.winfo_exists())
    except Exception:
        alive = False
    if not alive:
        _root = tk.Tk()
        try:
            _root.withdraw()
        except Exception:
            pass
    assert _root is not None
    return _root


def ask_yes_no(title: str, message: str) -> bool:
    return bool(messagebox.askyesno(title, message, parent=root()))


def show_info(title: str, message: str) -> None:
    messagebox.showinfo(title, message, parent=root())


def show_error(title: str, message: str) -> None:
    messagebox.showerror(title, message, parent=root())
