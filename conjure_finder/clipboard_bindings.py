"""Clipboard shortcuts that work under non-Latin keyboard layouts (e.g. Russian).

Tk's default Control-c / Control-v bindings use keysyms, which become
Control-с / Control-м on ЙЦУКЕН. Bind by hardware keycode instead.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

# US/RU Windows physical keys
_KEYCODE_A = 65
_KEYCODE_C = 67
_KEYCODE_X = 88
_KEYCODE_V = 86


def _is_text(widget: Any) -> bool:
    return isinstance(widget, tk.Text)


def _is_entry(widget: Any) -> bool:
    return isinstance(widget, (tk.Entry, ttk.Entry))


def _editable(widget: Any) -> bool:
    try:
        return str(widget.cget("state")) not in ("disabled", "readonly")
    except tk.TclError:
        return True


def _selection_text(widget: Any) -> str | None:
    if _is_text(widget):
        try:
            return widget.get("sel.first", "sel.last")
        except tk.TclError:
            return None
    if _is_entry(widget):
        try:
            if widget.selection_present():
                return widget.selection_get()
        except tk.TclError:
            return None
    return None


def _set_clipboard(widget: Any, text: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(text)
    # Keep clipboard after the window closes (Windows).
    try:
        widget.update_idletasks()
    except tk.TclError:
        pass


def _copy(event: tk.Event) -> str:
    text = _selection_text(event.widget)
    if text is not None:
        _set_clipboard(event.widget, text)
    return "break"


def _cut(event: tk.Event) -> str:
    widget = event.widget
    if not _editable(widget):
        return "break"
    text = _selection_text(widget)
    if text is None:
        return "break"
    _set_clipboard(widget, text)
    if _is_text(widget):
        widget.delete("sel.first", "sel.last")
    elif _is_entry(widget):
        widget.delete("sel.first", "sel.last")
    return "break"


def _paste(event: tk.Event) -> str:
    widget = event.widget
    if not _editable(widget):
        return "break"
    try:
        data = widget.clipboard_get()
    except tk.TclError:
        return "break"
    if _is_text(widget):
        try:
            if widget.tag_ranges("sel"):
                widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        widget.insert("insert", data)
    elif _is_entry(widget):
        try:
            if widget.selection_present():
                widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        widget.insert("insert", data)
    return "break"


def _select_all(event: tk.Event) -> str:
    widget = event.widget
    if _is_text(widget):
        # Works even when state=disabled.
        widget.tag_add("sel", "1.0", "end-1c")
        return "break"
    if _is_entry(widget):
        widget.selection_range(0, tk.END)
        widget.icursor(tk.END)
        return "break"
    return "break"


def _on_ctrl(event: tk.Event) -> str | None:
    code = getattr(event, "keycode", None)
    if code == _KEYCODE_C:
        return _copy(event)
    if code == _KEYCODE_X:
        return _cut(event)
    if code == _KEYCODE_V:
        return _paste(event)
    if code == _KEYCODE_A:
        return _select_all(event)
    return None


def install_clipboard_bindings(*widgets: Any) -> None:
    """Enable Ctrl+C/X/V/A (and <<Copy>>/<<Paste>>/<<Cut>>) on the given widgets."""
    for w in widgets:
        w.bind("<Control-KeyPress>", _on_ctrl, add="+")
        w.bind("<<Copy>>", _copy, add="+")
        w.bind("<<Cut>>", _cut, add="+")
        w.bind("<<Paste>>", _paste, add="+")
        # Select-all via virtual event where available
        w.bind("<<SelectAll>>", _select_all, add="+")
