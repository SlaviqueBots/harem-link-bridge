"""Collapsible harem-member picker with per-context counts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import ttk

logger = logging.getLogger(__name__)

OkCb = Callable[[dict[str, Any]], None]
ErrCb = Callable[[BaseException], None]
FetchUsersFn = Callable[[OkCb, ErrCb], None]
PickUserFn = Callable[[str], None]

_PICK_DEBOUNCE_MS = 280


class MemberBrowsePanel(ttk.Frame):
    """Compact Members control: small button + dropdown list (no layout warp)."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        unit: str,
        fetch_users: FetchUsersFn,
        on_pick: PickUserFn,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._unit = (unit or "items").strip()
        self._title = (title or self._unit).strip()
        self._fetch_users = fetch_users
        self._on_pick = on_pick
        self._on_log = on_log or (lambda _s: None)
        self._expanded = False
        self._busy = False
        self._gen = 0
        self._rows: list[dict[str, Any]] = []
        self._pick_after: str | None = None
        self._last_picked: str | None = None
        self._popup: tk.Toplevel | None = None
        self._list: tk.Listbox | None = None

        self._toggle_btn = ttk.Button(
            self,
            text="Members ▾",
            width=11,
            command=self._toggle,
        )
        self._toggle_btn.pack(side=tk.LEFT)
        ttk.Button(self, text="↻", width=3, command=self._reload_click).pack(
            side=tk.LEFT, padx=(2, 0)
        )

        self.bind("<Destroy>", self._on_destroy)

    def set_labels(self, *, title: str | None = None, unit: str | None = None) -> None:
        if title is not None:
            self._title = (title or self._unit).strip()
        if unit is not None:
            self._unit = (unit or "items").strip()
        if self._rows and self._list is not None:
            self._list.delete(0, tk.END)
            for row in self._rows:
                self._list.insert(tk.END, self._format_row(row))

    def clear_cache(self) -> None:
        self._rows = []
        self._gen += 1

    def close(self) -> None:
        self._close_popup()

    def _on_destroy(self, _event=None) -> None:
        self._close_popup()

    def _reload_click(self) -> None:
        if not self._expanded:
            self._toggle()
        else:
            self.reload()

    def _toggle(self) -> None:
        if self._expanded:
            self._close_popup()
            return
        self._open_popup()
        if not self._rows:
            self.reload()

    def _open_popup(self) -> None:
        self._close_popup()
        self._expanded = True
        self._toggle_btn.configure(text="Members ▴")

        pop = tk.Toplevel(self)
        pop.withdraw()
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        try:
            pop.transient(self.winfo_toplevel())
        except Exception:
            pass

        fr = ttk.Frame(pop, relief=tk.SOLID, borderwidth=1)
        fr.pack(fill=tk.BOTH, expand=True)
        lst = tk.Listbox(
            fr, height=8, exportselection=False, activestyle="dotbox", width=28
        )
        ys = ttk.Scrollbar(fr, orient=tk.VERTICAL, command=lst.yview)
        lst.configure(yscrollcommand=ys.set)
        lst.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        lst.bind("<ButtonRelease-1>", self._on_click)
        lst.bind("<Return>", self._on_click)
        lst.bind("<Escape>", lambda _e: self._close_popup())

        self._popup = pop
        self._list = lst
        if self._rows:
            for row in self._rows:
                lst.insert(tk.END, self._format_row(row))
        elif self._busy:
            lst.insert(tk.END, "Loading…")

        self.update_idletasks()
        try:
            bx = self._toggle_btn.winfo_rootx()
            by = self._toggle_btn.winfo_rooty() + self._toggle_btn.winfo_height()
            pop.geometry(f"+{bx}+{by}")
        except Exception:
            pass
        pop.deiconify()
        pop.focus_set()
        pop.bind("<FocusOut>", self._on_popup_focus_out)

    def _on_popup_focus_out(self, _event=None) -> None:
        # Delay so listbox click can register before close.
        self.after(120, self._maybe_close_on_focus)

    def _maybe_close_on_focus(self) -> None:
        if not self._expanded or self._popup is None:
            return
        try:
            focus = self.focus_get()
            if focus is None:
                return
            if focus is self._list or focus is self._popup:
                return
            if str(focus).startswith(str(self._popup)):
                return
        except Exception:
            pass
        self._close_popup()

    def _close_popup(self) -> None:
        self._expanded = False
        try:
            self._toggle_btn.configure(text="Members ▾")
        except Exception:
            pass
        pop = self._popup
        self._popup = None
        self._list = None
        if pop is not None:
            try:
                pop.destroy()
            except Exception:
                pass

    def reload(self) -> None:
        """Fetch members. Safe to call while a prior fetch is in flight."""
        self._gen += 1
        gen = self._gen
        self._busy = True
        self._rows = []
        if self._list is not None:
            self._list.delete(0, tk.END)
            self._list.insert(tk.END, "Loading…")

        def on_ok(body: dict) -> None:
            if gen != self._gen:
                return
            self._busy = False
            if body.get("op") != "browse_users_ok":
                err = str(body.get("error") or "failed")
                if self._list is not None:
                    self._list.delete(0, tk.END)
                    self._list.insert(tk.END, f"Error: {err}")
                return
            self._rows = list(body.get("users") or [])
            if self._list is None:
                return
            self._list.delete(0, tk.END)
            if not self._rows:
                self._list.insert(tk.END, "No members with items.")
                return
            for row in self._rows:
                self._list.insert(tk.END, self._format_row(row))
            self._on_log(f"Members loaded ({len(self._rows)})")

        def on_err(exc: BaseException) -> None:
            if gen != self._gen:
                return
            self._busy = False
            if str(exc) == "superseded":
                return
            if self._list is not None:
                self._list.delete(0, tk.END)
                self._list.insert(tk.END, f"Error: {exc}")

        self._fetch_users(on_ok, on_err)

    def _format_row(self, row: dict[str, Any]) -> str:
        n = int(row.get("count") or 0)
        if bool(row.get("mine")):
            name = "Me"
        else:
            name = (
                str(row.get("username") or row.get("display") or "").strip()
                or f"id{row.get('user_id')}"
            )
        return f"{name} · {n} {self._unit}"

    def _on_click(self, _event=None) -> None:
        if self._list is None:
            return
        sel = self._list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(self._rows):
            return
        row = self._rows[idx]
        if bool(row.get("mine")):
            key = ""
        else:
            key = str(row.get("username") or "").strip().lstrip("@")
            if not key:
                return
        self._schedule_pick(key)

    def _schedule_pick(self, username: str) -> None:
        """Debounce rapid member clicks — only the last pick loads."""
        self._last_picked = username
        if self._pick_after is not None:
            try:
                self.after_cancel(self._pick_after)
            except Exception:
                pass
            self._pick_after = None

        def fire() -> None:
            self._pick_after = None
            picked = self._last_picked
            if picked is None:
                return
            try:
                self._on_pick(picked)
            except Exception:
                logger.debug("member pick failed", exc_info=True)
            self._close_popup()

        self._pick_after = self.after(_PICK_DEBOUNCE_MS, fire)
