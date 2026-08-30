"""App-wide light/dark chrome for Harem Link Bridge (Discord/Cursor-ish)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

# Discord/Cursor-adjacent dark surface.
DARK: dict[str, str] = {
    "mode": "dark",
    "bg": "#1e1f22",
    "bg2": "#2b2d31",
    "bg3": "#313338",
    "fg": "#f2f3f5",
    "muted": "#b5bac1",
    "border": "#1e1f22",
    "accent": "#5865f2",
    "entry": "#1e1f22",
    "select": "#404249",
    "trough": "#1e1f22",
    "tab": "#2b2d31",
    "tab_sel": "#313338",
    "button": "#2b2d31",
    "button_active": "#383a40",
    "log_bg": "#111214",
    "canvas": "#1e1f22",
    "hover": "#35373c",
}

# Previous default-ish light look (preserved via toggle).
LIGHT: dict[str, str] = {
    "mode": "light",
    "bg": "#f0f0f0",
    "bg2": "#ffffff",
    "bg3": "#e8e8e8",
    "fg": "#1a1a1a",
    "muted": "#555555",
    "border": "#d0d0d0",
    "accent": "#2b6cb0",
    "entry": "#ffffff",
    "select": "#cce0ff",
    "trough": "#dcdcdc",
    "tab": "#e8e8e8",
    "tab_sel": "#ffffff",
    "button": "#e8e8e8",
    "button_active": "#d8d8d8",
    "log_bg": "#ffffff",
    "canvas": "#f5f5f5",
    "hover": "#e0e0e0",
}


def normalize_theme(raw: object) -> str:
    text = str(raw or "dark").strip().lower()
    return "light" if text == "light" else "dark"


def palette(mode: str) -> dict[str, str]:
    return LIGHT if normalize_theme(mode) == "light" else DARK


def surface_for(widget: tk.Misc | None) -> dict[str, str]:
    """Active palette attached to the toplevel (falls back to dark)."""
    if widget is None:
        return dict(DARK)
    try:
        root = widget.winfo_toplevel()
        stored = getattr(root, "_bridge_palette", None)
        if isinstance(stored, dict) and stored.get("bg"):
            return stored
        mode = getattr(root, "_bridge_ui_theme", "dark")
        return palette(str(mode))
    except Exception:
        return dict(DARK)


def is_dark(widget: tk.Misc | None) -> bool:
    return surface_for(widget).get("mode") != "light"


def gallery_gap(widget: tk.Misc | None) -> int:
    """Small gutters between thumbs (surface-colored — blackish in dark mode)."""
    return 3


def apply_app_theme(root: tk.Misc, mode: str = "dark") -> dict[str, str]:
    """Style default ttk widgets + tint common tk leaves. Returns active palette."""
    mode = normalize_theme(mode)
    c = palette(mode)
    try:
        root._bridge_ui_theme = mode  # type: ignore[attr-defined]
        root._bridge_palette = c  # type: ignore[attr-defined]
    except Exception:
        pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # Clam draws 3D edges from lightcolor/darkcolor — force them to the surface
    # so we never get screaming-white borders.
    edge = {
        "background": c["bg"],
        "foreground": c["fg"],
        "borderwidth": 0,
        "relief": "flat",
        "lightcolor": c["bg"],
        "darkcolor": c["bg"],
        "bordercolor": c["bg"],
        "focuscolor": c["bg"],
    }
    style.configure(".", **edge)
    style.configure("TFrame", **edge)
    style.configure("TLabel", background=c["bg"], foreground=c["fg"], borderwidth=0)
    style.configure(
        "TCheckbutton",
        background=c["bg"],
        foreground=c["fg"],
        focuscolor=c["bg"],
        borderwidth=0,
        lightcolor=c["bg"],
        darkcolor=c["bg"],
    )
    style.map(
        "TCheckbutton",
        background=[("active", c["bg"]), ("selected", c["bg"])],
        foreground=[("disabled", c["muted"]), ("active", c["fg"])],
    )
    style.configure(
        "TRadiobutton",
        background=c["bg"],
        foreground=c["fg"],
        focuscolor=c["bg"],
        borderwidth=0,
        lightcolor=c["bg"],
        darkcolor=c["bg"],
    )
    style.map(
        "TRadiobutton",
        background=[("active", c["bg"]), ("selected", c["bg"])],
        foreground=[("active", c["fg"]), ("selected", c["fg"])],
    )
    # Mode strip (Undone/Done/…) uses Toolbutton — clam defaults flash white.
    style.configure(
        "Toolbutton",
        background=c["bg"],
        foreground=c["muted"],
        borderwidth=0,
        relief="flat",
        padding=(8, 3),
        lightcolor=c["bg"],
        darkcolor=c["bg"],
        bordercolor=c["bg"],
        focuscolor=c["hover"],
        focusthickness=0,
    )
    style.map(
        "Toolbutton",
        background=[
            ("selected", c["bg3"]),
            ("pressed", c["bg3"]),
            ("active", c["hover"]),
        ],
        foreground=[
            ("selected", c["fg"]),
            ("pressed", c["fg"]),
            ("active", c["fg"]),
            ("disabled", c["muted"]),
        ],
        relief=[
            ("selected", "flat"),
            ("pressed", "flat"),
            ("active", "flat"),
            ("!selected", "flat"),
        ],
        lightcolor=[
            ("selected", c["bg3"]),
            ("active", c["hover"]),
            ("pressed", c["bg3"]),
        ],
        darkcolor=[
            ("selected", c["bg3"]),
            ("active", c["hover"]),
            ("pressed", c["bg3"]),
        ],
        bordercolor=[
            ("selected", c["bg3"]),
            ("active", c["hover"]),
            ("pressed", c["bg3"]),
        ],
    )
    style.configure(
        "TButton",
        background=c["button"],
        foreground=c["fg"],
        borderwidth=0,
        focusthickness=0,
        focuscolor=c["button"],
        padding=(10, 4),
        relief="flat",
        lightcolor=c["button"],
        darkcolor=c["button"],
        bordercolor=c["button"],
    )
    style.map(
        "TButton",
        background=[("active", c["button_active"]), ("disabled", c["bg2"])],
        foreground=[("disabled", c["muted"])],
        relief=[("pressed", "flat"), ("!pressed", "flat")],
        lightcolor=[("active", c["button_active"])],
        darkcolor=[("active", c["button_active"])],
    )
    style.configure(
        "TEntry",
        fieldbackground=c["entry"],
        foreground=c["fg"],
        insertcolor=c["fg"],
        bordercolor=c["bg2"],
        lightcolor=c["bg2"],
        darkcolor=c["bg2"],
        padding=4,
        borderwidth=0,
        relief="flat",
        font="TkTextFont",
    )
    style.configure(
        "TLabelframe",
        background=c["bg"],
        foreground=c["muted"],
        bordercolor=c["bg"],
        borderwidth=0,
        relief="flat",
        lightcolor=c["bg"],
        darkcolor=c["bg"],
    )
    style.configure(
        "TLabelframe.Label",
        background=c["bg"],
        foreground=c["muted"],
        font="TkSmallCaptionFont",
    )
    style.configure(
        "TNotebook",
        background=c["bg"],
        borderwidth=0,
        relief="flat",
        tabmargins=(2, 2, 2, 0),
        lightcolor=c["bg"],
        darkcolor=c["bg"],
        bordercolor=c["bg"],
    )
    style.configure(
        "TNotebook.Tab",
        background=c["tab"],
        foreground=c["muted"],
        padding=(12, 4),
        borderwidth=0,
        relief="flat",
        lightcolor=c["tab"],
        darkcolor=c["tab"],
        bordercolor=c["tab"],
        focuscolor=c["tab"],
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", c["tab_sel"]), ("active", c["hover"])],
        foreground=[("selected", c["fg"]), ("active", c["fg"])],
        lightcolor=[("selected", c["tab_sel"]), ("active", c["hover"])],
        darkcolor=[("selected", c["tab_sel"]), ("active", c["hover"])],
        bordercolor=[("selected", c["tab_sel"]), ("active", c["hover"])],
        expand=[("selected", [1, 1, 1, 0])],
    )
    # Visible track + accent thumb (dark trough must not match bg).
    trough = "#4e5058" if mode == "dark" else "#c8c8c8"
    style.configure(
        "Horizontal.TScale",
        background=c["accent"],
        troughcolor=trough,
        bordercolor=trough,
        lightcolor=c["accent"],
        darkcolor=c["accent"],
        sliderthickness=16,
        borderwidth=0,
    )
    style.map(
        "Horizontal.TScale",
        background=[("active", c["accent"])],
        lightcolor=[("active", c["accent"])],
        darkcolor=[("active", c["accent"])],
    )
    style.configure(
        "Horizontal.TProgressbar",
        background=c["accent"],
        troughcolor=c["trough"],
        bordercolor=c["bg"],
        lightcolor=c["accent"],
        darkcolor=c["accent"],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=c["bg2"],
        troughcolor=c["bg"],
        bordercolor=c["bg"],
        arrowcolor=c["fg"],
        relief="flat",
        lightcolor=c["bg2"],
        darkcolor=c["bg2"],
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=c["bg2"],
        troughcolor=c["bg"],
        bordercolor=c["bg"],
        arrowcolor=c["fg"],
        relief="flat",
        lightcolor=c["bg2"],
        darkcolor=c["bg2"],
    )
    style.configure("TSeparator", background=c["bg2"])

    try:
        root.configure(bg=c["bg"])
    except Exception:
        pass

    _tint_tree(root, c)
    return c


def _tint_tree(widget: tk.Misc, c: dict[str, str]) -> None:
    """Recolor plain tk leaves that ignore ttk Style."""
    try:
        cls = widget.winfo_class()
    except Exception:
        return
    try:
        if cls == "Canvas":
            widget.configure(  # type: ignore[call-arg]
                bg=c["canvas"], highlightthickness=0, bd=0, highlightbackground=c["canvas"]
            )
        elif cls == "Text":
            if getattr(widget, "_bridge_preserve_text_style", False):
                pass
            else:
                widget.configure(  # type: ignore[call-arg]
                    bg=c["log_bg"],
                    fg=c["fg"],
                    insertbackground=c["fg"],
                    selectbackground=c["select"],
                    selectforeground=c["fg"],
                    highlightthickness=0,
                    bd=0,
                    relief=tk.FLAT,
                )
        elif cls == "Listbox":
            widget.configure(  # type: ignore[call-arg]
                bg=c["entry"],
                fg=c["fg"],
                selectbackground=c["select"],
                selectforeground=c["fg"],
                highlightthickness=0,
                bd=0,
                relief=tk.FLAT,
                font="TkTextFont",
            )
        elif cls == "Entry":
            widget.configure(  # type: ignore[call-arg]
                bg=c["entry"],
                fg=c["fg"],
                insertbackground=c["fg"],
                relief=tk.FLAT,
                highlightthickness=0,
                bd=0,
                font="TkTextFont",
            )
        elif cls in ("Frame", "Labelframe", "Toplevel", "Tk"):
            try:
                widget.configure(  # type: ignore[call-arg]
                    bg=c["bg"],
                    highlightthickness=0,
                    highlightbackground=c["bg"],
                    bd=0,
                )
            except Exception:
                try:
                    widget.configure(bg=c["bg"])  # type: ignore[call-arg]
                except Exception:
                    pass
        elif cls == "Label":
            # Always match canvas so thumb gutters never flash white.
            try:
                widget.configure(  # type: ignore[call-arg]
                    bg=c["canvas"],
                    fg=c["fg"],
                    highlightthickness=0,
                    highlightbackground=c["canvas"],
                    bd=0,
                    relief=tk.FLAT,
                )
            except Exception:
                pass
        elif cls == "Button":
            try:
                # Leave intentionally colored craft buttons alone.
                bg = str(widget.cget("bg") or "").lower()
            except Exception:
                bg = ""
            craftish = bg in {
                "#3a2f4a",
                "#2f3d4a",
                "#3a3a3a",
                "#1e4a5c",
                "#1b8f4a",
                "#2a2a2a",
                "#5a3d2a",
                "#24a656",
            }
            if not craftish:
                try:
                    widget.configure(  # type: ignore[call-arg]
                        bg=c["button"],
                        fg=c["fg"],
                        activebackground=c["button_active"],
                        activeforeground=c["fg"],
                        relief=tk.FLAT,
                        bd=0,
                        highlightthickness=0,
                    )
                except Exception:
                    pass
    except Exception:
        pass

    try:
        kids = widget.winfo_children()
    except Exception:
        return
    for child in kids:
        _tint_tree(child, c)


def schedule_theme_refresh(root: tk.Misc, mode: str, *, delay_ms: int = 80) -> None:
    """Re-tint after new panels mount (roster galleries, etc.)."""

    def _go() -> None:
        apply_app_theme(root, mode)
        try:
            from link_bridge.dpi import apply_ui_scale

            extra = float(getattr(root, "_bridge_ui_scale", 1.0) or 1.0)
            apply_ui_scale(root, extra)
        except Exception:
            pass

    try:
        root.after(delay_ms, _go)
    except Exception:
        pass


# Windows virtual-key codes for physical A/C/V/X (layout-independent).
_CLIP_VK = {0x41: "a", 0x43: "c", 0x56: "v", 0x58: "x"}
_CLIP_KEYSYMS = {
    "c": "c",
    "v": "v",
    "x": "x",
    "a": "a",
    "cyrillic_es": "c",
    "cyrillic_em": "v",
    "cyrillic_che": "x",
    "cyrillic_ef": "a",
}


def clipboard_control_letter(event: Any) -> str | None:
    """Map Ctrl+letter using keysym or physical Windows keycode (any keyboard language)."""
    keysym = str(getattr(event, "keysym", "") or "").lower()
    letter = _CLIP_KEYSYMS.get(keysym)
    if letter:
        return letter
    try:
        code = int(getattr(event, "keycode", 0) or 0)
    except (TypeError, ValueError):
        code = 0
    return _CLIP_VK.get(code)


def _bind_clipboard_sequences(widget: tk.Misc, handlers: dict[str, Any]) -> None:
    for seq, letter in (
        ("<Control-x>", "x"),
        ("<Control-X>", "x"),
        ("<Control-c>", "c"),
        ("<Control-C>", "c"),
        ("<Control-v>", "v"),
        ("<Control-V>", "v"),
        ("<Control-a>", "a"),
        ("<Control-A>", "a"),
        ("<Control-Key-x>", "x"),
        ("<Control-Key-c>", "c"),
        ("<Control-Key-v>", "v"),
        ("<Control-Key-a>", "a"),
    ):
        widget.bind(seq, handlers[letter])

    def _on_ctrl_key(event=None):
        keysym = str(getattr(event, "keysym", "") or "").lower()
        if keysym in ("c", "v", "x", "a"):
            return None
        letter = clipboard_control_letter(event)
        if not letter:
            return None
        fn = handlers.get(letter)
        if fn is None:
            return None
        return fn(event)

    widget.bind("<Control-KeyPress>", _on_ctrl_key, add="+")


def bind_entry_clipboard(entry: ttk.Entry | tk.Entry) -> None:
    """Ensure Ctrl+X/C/V/A work on Windows ttk.Entry for any active keyboard language."""

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

    _bind_clipboard_sequences(
        entry, {"x": _cut, "c": _copy, "v": _paste, "a": _select_all}
    )


def bind_text_clipboard(text: tk.Text) -> None:
    """Ensure Ctrl+X/C/V/A work in multiline editors for any active keyboard language."""

    def _cut(_event=None):
        try:
            if text.tag_ranges(tk.SEL):
                text.event_generate("<<Cut>>")
        except Exception:
            pass
        return "break"

    def _copy(_event=None):
        try:
            if text.tag_ranges(tk.SEL):
                try:
                    text.event_generate("<<Copy>>")
                except Exception:
                    chunk = text.get(tk.SEL_FIRST, tk.SEL_LAST)
                    text.clipboard_clear()
                    text.clipboard_append(chunk)
        except Exception:
            pass
        return "break"

    def _paste(_event=None):
        try:
            text.event_generate("<<Paste>>")
        except Exception:
            pass
        return "break"

    def _select_all(_event=None):
        try:
            text.tag_add(tk.SEL, "1.0", tk.END)
            text.mark_set(tk.INSERT, tk.END)
            text.see(tk.INSERT)
        except Exception:
            pass
        return "break"

    _bind_clipboard_sequences(
        text, {"x": _cut, "c": _copy, "v": _paste, "a": _select_all}
    )
