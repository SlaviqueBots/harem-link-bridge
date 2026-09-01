"""Modal editor for a card's local crafting plan."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from link_bridge.crafting_plans import (
    PRESET_SECTIONS,
    PlanSection,
    editor_shows_section,
    new_custom_id,
    parse_url_lines,
    urls_to_text,
)
from link_bridge.text_edit_dialog import _center_on_parent, _parse_geometry, ask_name


def edit_crafting_plan(
    parent: tk.Misc,
    *,
    char_id: int,
    sections: list[PlanSection],
    is_r34: bool = False,
    geometry: str = "",
    on_geometry: Callable[[str], None] | None = None,
) -> list[PlanSection] | None:
    """Return updated sections, or None if cancelled."""
    result: dict[str, list[PlanSection] | None] = {"value": None}

    from link_bridge.theme import bind_text_clipboard, palette, surface_for

    pal = surface_for(parent)
    if not pal.get("bg"):
        pal = palette("dark")
    bg = pal.get("bg") or "#1e1f22"
    bg2 = pal.get("bg2") or "#2b2d31"
    fg = pal.get("fg") or "#f2f3f5"
    muted = pal.get("muted") or "#b5bac1"
    entry_bg = pal.get("log_bg") or pal.get("entry") or "#111214"
    select = pal.get("select") or "#404249"
    accent = pal.get("accent") or "#5865f2"

    win = tk.Toplevel(parent)
    win.title(f"Crafting plan #{int(char_id)}")
    win.transient(parent.winfo_toplevel())
    win.configure(bg=bg)
    win.grab_set()
    win.resizable(True, True)
    win.minsize(520, 420)

    parsed = _parse_geometry(geometry)
    if parsed is not None:
        w, h, x, y = parsed
        win.geometry(f"{max(w, 520)}x{max(h, 420)}+{x}+{y}")
    else:
        _center_on_parent(win, parent, width=640, height=560)

    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        frame,
        text="One booru post URL per line. Reached posts drop from every section.",
        wraplength=600,
        foreground=muted,
    ).pack(anchor=tk.W, pady=(0, 6))

    canvas = tk.Canvas(
        frame,
        highlightthickness=0,
        borderwidth=0,
        bg=bg,
        insertbackground=fg,
    )
    scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=canvas.yview)
    inner = ttk.Frame(canvas)
    inner.bind(
        "<Configure>",
        lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas_win = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scroll.set)

    def _on_canvas_width(event) -> None:
        canvas.itemconfigure(canvas_win, width=event.width)

    canvas.bind("<Configure>", _on_canvas_width)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _wheel(event) -> str | None:
        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            canvas.yview_scroll(int(-delta / 120), "units")
        return "break"

    canvas.bind("<MouseWheel>", _wheel)
    inner.bind("<MouseWheel>", _wheel)
    win.bind("<MouseWheel>", _wheel)

    rows: list[dict[str, object]] = []

    def _bind_clip(text: tk.Text) -> None:
        try:
            bind_text_clipboard(text)
        except Exception:
            pass

    def _add_row(sec: PlanSection) -> None:
        box = ttk.LabelFrame(inner, text=sec.title, padding=4)
        box.pack(fill=tk.X, pady=(0, 8), padx=2)
        head = ttk.Frame(box)
        head.pack(fill=tk.X)
        text = tk.Text(
            box,
            height=4,
            wrap=tk.NONE,
            undo=True,
            font="TkTextFont",
            bg=entry_bg,
            fg=fg,
            insertbackground=fg,
            selectbackground=select,
            selectforeground=fg,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=bg2,
            highlightcolor=accent,
            padx=6,
            pady=4,
        )
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", urls_to_text(sec.urls))
        _bind_clip(text)
        rec: dict[str, object] = {
            "id": sec.id,
            "title": sec.title,
            "preset": sec.preset,
            "text": text,
            "box": box,
        }
        if not sec.preset:
            ttk.Button(
                head,
                text="Rename",
                width=8,
                command=lambda r=rec: _rename_row(r),
            ).pack(side=tk.RIGHT)
            ttk.Button(
                head,
                text="Remove",
                width=8,
                command=lambda r=rec: _remove_row(r),
            ).pack(side=tk.RIGHT, padx=(0, 4))
        rows.append(rec)

    def _rename_row(rec: dict[str, object]) -> None:
        name = ask_name(
            win,
            title="Rename section",
            initial=str(rec.get("title") or ""),
            prompt="Section name",
            max_chars=48,
        )
        if not name:
            return
        rec["title"] = name
        box = rec.get("box")
        if isinstance(box, ttk.LabelFrame):
            box.configure(text=name)

    def _remove_row(rec: dict[str, object]) -> None:
        box = rec.get("box")
        if isinstance(box, tk.Misc):
            box.destroy()
        if rec in rows:
            rows.remove(rec)

    for sec in sections:
        if editor_shows_section(sec, is_r34=bool(is_r34)):
            _add_row(sec)

    def _add_custom() -> None:
        name = ask_name(
            win,
            title="New section",
            initial="",
            prompt="Name this list (e.g. extra G, outfit, …)",
            max_chars=48,
        )
        if not name:
            return
        used = {str(r.get("id") or "") for r in rows}
        sid = new_custom_id(name, used)
        _add_row(PlanSection(id=sid, title=name, preset=False, urls=[]))
        try:
            canvas.yview_moveto(1.0)
        except Exception:
            pass

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
        edited: dict[str, PlanSection] = {}
        custom_out: list[PlanSection] = []
        used = {pid for pid, _t in PRESET_SECTIONS}
        for rec in rows:
            widget = rec.get("text")
            if not isinstance(widget, tk.Text):
                continue
            body = widget.get("1.0", "end-1c")
            urls = parse_url_lines(body)
            preset = bool(rec.get("preset"))
            title = str(rec.get("title") or "").strip()[:48]
            sid = str(rec.get("id") or "")
            if preset:
                edited[sid] = PlanSection(id=sid, title=title, preset=True, urls=urls)
                continue
            if not title:
                continue
            if not sid.startswith("custom:"):
                sid = new_custom_id(title, used)
            while sid in used:
                sid = new_custom_id(f"{title}-{len(used)}", used)
            used.add(sid)
            custom_out.append(PlanSection(id=sid, title=title, preset=False, urls=urls))
        out: list[PlanSection] = []
        seen_custom: set[str] = set()
        for sec in sections:
            if sec.preset:
                out.append(edited.get(sec.id) or sec)
                continue
            rec = next((c for c in custom_out if c.id == sec.id), None)
            if rec is not None:
                out.append(rec)
                seen_custom.add(rec.id)
            elif any(str(r.get("id") or "") == sec.id for r in rows):
                continue
            else:
                out.append(sec)
        for extra in custom_out:
            if extra.id not in seen_custom:
                out.append(extra)
        _remember_geometry()
        result["value"] = out
        win.destroy()

    btns = ttk.Frame(win, padding=(10, 0, 10, 10))
    btns.pack(fill=tk.X)
    ttk.Button(btns, text="Add section", command=_add_custom).pack(side=tk.LEFT)
    ttk.Button(btns, text="Cancel", command=_cancel).pack(side=tk.RIGHT)
    ttk.Button(btns, text="Save", command=_save).pack(side=tk.RIGHT, padx=(0, 8))
    win.bind("<Escape>", lambda _e: _cancel())
    win.protocol("WM_DELETE_WINDOW", _cancel)
    from link_bridge.window_keys import bind_q_close

    bind_q_close(win, on_close=_cancel)
    win.wait_window()
    return result["value"]
