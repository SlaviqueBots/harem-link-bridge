"""Small in-app text editor (flavour / note) — no Telegram round-trip."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk


def _parse_geometry(raw: str) -> tuple[int, int, int, int] | None:
    text = (raw or "").strip().lower().replace(" ", "")
    if not text or "x" not in text or "+" not in text:
        return None
    try:
        size, rest = text.split("+", 1)
        w_s, h_s = size.split("x", 1)
        if "+" in rest:
            x_s, y_s = rest.split("+", 1)
        else:
            return None
        w, h, x, y = int(w_s), int(h_s), int(x_s), int(y_s)
        if w < 280 or h < 120:
            return None
        return w, h, x, y
    except Exception:
        return None


def _center_on_parent(win: tk.Toplevel, parent: tk.Misc, *, width: int, height: int) -> None:
    top = parent.winfo_toplevel()
    top.update_idletasks()
    win.update_idletasks()
    pw = max(int(top.winfo_width()), 1)
    ph = max(int(top.winfo_height()), 1)
    px = int(top.winfo_rootx())
    py = int(top.winfo_rooty())
    x = px + max(0, (pw - width) // 2)
    y = py + max(0, (ph - height) // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")


def ask_text(
    parent: tk.Misc,
    *,
    title: str,
    initial: str = "",
    prompt: str = "",
    max_chars: int = 500,
    geometry: str = "",
    on_geometry: Callable[[str], None] | None = None,
    allow_empty: bool = False,
) -> str | None:
    """Modal multiline editor. Returns stripped text, or None if cancelled."""
    from link_bridge.theme import bind_text_clipboard, dialog_palette, style_tk_text

    result: dict[str, str | None] = {"value": None}
    pal = dialog_palette(parent)
    bg = pal["bg"]
    fg = pal["fg"]
    muted = pal["muted"]

    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent.winfo_toplevel())
    win.configure(bg=bg)
    win.grab_set()
    win.resizable(True, True)
    win.minsize(360, 180)

    parsed = _parse_geometry(geometry)
    if parsed is not None:
        w, h, x, y = parsed
        win.geometry(f"{w}x{h}+{x}+{y}")
    else:
        _center_on_parent(win, parent, width=420, height=220)

    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)
    if prompt:
        ttk.Label(frame, text=prompt, wraplength=420, foreground=muted).pack(
            anchor=tk.W, pady=(0, 6)
        )

    text = tk.Text(frame, height=6, width=52, wrap=tk.WORD, undo=True, font=("Segoe UI", 10))
    style_tk_text(text, pal)
    text.pack(fill=tk.BOTH, expand=True)
    text.insert("1.0", initial or "")
    bind_text_clipboard(text)
    text.focus_set()

    count = tk.StringVar()

    def _refresh_count(*_a) -> None:
        body = text.get("1.0", "end-1c")
        count.set(f"{len(body)} / {max_chars}")

    text.bind("<KeyRelease>", _refresh_count)
    _refresh_count()

    btns = ttk.Frame(frame)
    btns.pack(fill=tk.X, pady=(8, 0))
    ttk.Label(btns, textvariable=count).pack(side=tk.LEFT)

    def _remember_geometry() -> None:
        if on_geometry is None:
            return
        try:
            geo = win.geometry()
        except Exception:
            return
        if geo:
            on_geometry(geo)

    def _cancel() -> None:
        _remember_geometry()
        result["value"] = None
        win.destroy()

    def _save() -> None:
        body = text.get("1.0", "end-1c").strip()[: max(1, int(max_chars))]
        if not body and not allow_empty:
            return
        _remember_geometry()
        result["value"] = body
        win.destroy()

    ttk.Button(btns, text="Cancel", command=_cancel).pack(side=tk.RIGHT)
    save_label = "Save" if not allow_empty else "Save / clear"
    ttk.Button(btns, text=save_label, command=_save).pack(side=tk.RIGHT, padx=(0, 8))
    win.bind("<Escape>", lambda _e: _cancel())
    win.protocol("WM_DELETE_WINDOW", _cancel)

    win.wait_window()
    return result["value"]


def ask_name(
    parent: tk.Misc,
    *,
    title: str,
    initial: str = "",
    prompt: str = "",
    max_chars: int = 30,
    geometry: str = "",
    on_geometry: Callable[[str], None] | None = None,
) -> str | None:
    """Modal single-line name prompt. Returns stripped text, or None if cancelled."""
    from link_bridge.theme import bind_entry_clipboard, dialog_palette

    result: dict[str, str | None] = {"value": None}
    limit = max(1, int(max_chars))
    pal = dialog_palette(parent)
    bg = pal["bg"]
    muted = pal["muted"]

    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent.winfo_toplevel())
    win.configure(bg=bg)
    win.grab_set()
    win.resizable(True, False)
    win.minsize(360, 120)

    parsed = _parse_geometry(geometry)
    if parsed is not None:
        w, _h, x, y = parsed
        win.geometry(f"{max(w, 360)}x140+{x}+{y}")
    else:
        _center_on_parent(win, parent, width=420, height=140)

    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)
    if prompt:
        ttk.Label(frame, text=prompt, wraplength=420, foreground=muted).pack(
            anchor=tk.W, pady=(0, 6)
        )

    var = tk.StringVar(value=(initial or "")[:limit])
    entry = ttk.Entry(frame, textvariable=var)
    entry.pack(fill=tk.X)
    bind_entry_clipboard(entry)
    entry.focus_set()
    try:
        entry.icursor(tk.END)
        entry.selection_range(0, tk.END)
    except Exception:
        pass

    count = tk.StringVar()

    def _refresh_count(*_a) -> None:
        body = var.get()
        if len(body) > limit:
            var.set(body[:limit])
            body = var.get()
        count.set(f"{len(body)} / {limit}")

    var.trace_add("write", _refresh_count)
    _refresh_count()

    btns = ttk.Frame(frame)
    btns.pack(fill=tk.X, pady=(8, 0))
    ttk.Label(btns, textvariable=count).pack(side=tk.LEFT)

    def _remember_geometry() -> None:
        if on_geometry is None:
            return
        try:
            geo = win.geometry()
        except Exception:
            return
        if geo:
            on_geometry(geo)

    def _cancel() -> None:
        _remember_geometry()
        result["value"] = None
        win.destroy()

    def _save() -> None:
        body = var.get().strip()[:limit]
        if not body:
            return
        _remember_geometry()
        result["value"] = body
        win.destroy()

    ttk.Button(btns, text="Cancel", command=_cancel).pack(side=tk.RIGHT)
    ttk.Button(btns, text="Save", command=_save).pack(side=tk.RIGHT, padx=(0, 8))
    win.bind("<Escape>", lambda _e: _cancel())
    win.bind("<Return>", lambda _e: _save())
    win.protocol("WM_DELETE_WINDOW", _cancel)

    win.wait_window()
    return result["value"]


def ask_set_name(
    parent: tk.Misc,
    *,
    title: str,
    initial: str = "",
    geometry: str = "",
    on_geometry: Callable[[str], None] | None = None,
) -> str | None:
    return ask_name(
        parent,
        title=title,
        initial=initial,
        prompt="Set name (saved quietly — no Telegram post).",
        max_chars=30,
        geometry=geometry,
        on_geometry=on_geometry,
    )
