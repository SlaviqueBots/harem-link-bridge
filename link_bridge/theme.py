"""Small Tk helpers (no custom skinning)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def bind_entry_clipboard(entry: ttk.Entry | tk.Entry) -> None:
    """Ensure Ctrl+X/C/V/A work on Windows ttk.Entry."""

    def _cut(_event=None):
        try:
            entry.event_generate("<<Cut>>")
        except Exception:
            pass
        return "break"

    def _copy(_event=None):
        try:
            entry.event_generate("<<Copy>>")
        except Exception:
            pass
        return "break"

    def _paste(_event=None):
        try:
            entry.event_generate("<<Paste>>")
        except Exception:
            pass
        return "break"

    def _select_all(_event=None):
        try:
            entry.selection_range(0, tk.END)
            entry.icursor(tk.END)
        except Exception:
            pass
        return "break"

    for seq, handler in (
        ("<Control-x>", _cut),
        ("<Control-X>", _cut),
        ("<Control-c>", _copy),
        ("<Control-C>", _copy),
        ("<Control-v>", _paste),
        ("<Control-V>", _paste),
        ("<Control-a>", _select_all),
        ("<Control-A>", _select_all),
    ):
        entry.bind(seq, handler)
