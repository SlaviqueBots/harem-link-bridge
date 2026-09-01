"""Shared keyboard shortcuts for secondary Bridge windows."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

# Physical Q key (US QWERTY position) — same keycode on Russian (Й), DE, etc.
_PHYSICAL_Q_KEYCODES = frozenset({81})
# Fallback keysyms when keycode is missing (some Tk builds / platforms).
_PHYSICAL_Q_KEYSYMS = frozenset(
    {
        "q",
        "Q",
        "cyrillic_short i",  # й / Й on Russian layout (QWERTY position)
        "cyrillic_ie",
        "Cyrillic_short i",
        "Cyrillic_IE",
    }
)

# Physical W key (US QWERTY position) — same keycode on Russian (Ц), DE, etc.
_PHYSICAL_W_KEYCODES = frozenset({87})
_PHYSICAL_W_KEYSYMS = frozenset(
    {
        "w",
        "W",
        "cyrillic_es",  # ц / Ц on Russian layout (QWERTY W position)
        "Cyrillic_es",
    }
)


def is_physical_q_key(event: tk.Event) -> bool:
    """True when the key in the QWERTY Q slot was pressed (layout-independent)."""
    try:
        kc = int(getattr(event, "keycode", 0) or 0)
        if kc in _PHYSICAL_Q_KEYCODES:
            return True
    except (TypeError, ValueError):
        pass
    keysym = str(getattr(event, "keysym", "") or "")
    return keysym in _PHYSICAL_Q_KEYSYMS


def is_physical_w_key(event: tk.Event) -> bool:
    """True when the key in the QWERTY W slot was pressed (layout-independent)."""
    try:
        kc = int(getattr(event, "keycode", 0) or 0)
        if kc in _PHYSICAL_W_KEYCODES:
            return True
    except (TypeError, ValueError):
        pass
    keysym = str(getattr(event, "keysym", "") or "")
    return keysym in _PHYSICAL_W_KEYSYMS


def _control_down(event: tk.Event) -> bool:
    try:
        state = int(getattr(event, "state", 0) or 0)
    except (TypeError, ValueError):
        state = 0
    return bool(state & 0x4)


def _widget_accepts_typing(widget: tk.Misc) -> bool:
    w = widget
    while w is not None:
        try:
            cls = w.winfo_class()
        except Exception:
            cls = ""
        if cls in ("Entry", "TEntry", "TCombobox"):
            try:
                st = str(w.cget("state") or "").lower()
                if st not in ("disabled", "readonly"):
                    return True
            except Exception:
                return True
        if cls == "Text":
            try:
                if str(w.cget("state") or "").lower() == "normal":
                    return True
            except Exception:
                return True
        if cls == "Spinbox":
            try:
                if str(w.cget("state") or "").lower() != "disabled":
                    return True
            except Exception:
                return True
        try:
            w = w.master
        except Exception:
            break
    return False


def bind_q_close(
    win: tk.Misc,
    *,
    on_close: Callable[[], None] | None = None,
) -> None:
    """Bind the physical Q key to close ``win``; skip in editable fields."""

    def _close(_event=None) -> str:
        try:
            if on_close is not None:
                on_close()
            else:
                win.destroy()
        except Exception:
            pass
        return "break"

    def _on_key(event: tk.Event) -> str | None:
        if not is_physical_q_key(event):
            return None
        if _widget_accepts_typing(event.widget):
            return None
        try:
            if event.widget.winfo_toplevel() is not win:
                return None
        except Exception:
            return None
        return _close()

    win.bind("<KeyPress>", _on_key, add="+")
