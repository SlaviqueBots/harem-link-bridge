"""Admin/editor Themes tab — edit main + secondary pools from the bridge."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk

logger = logging.getLogger(__name__)

OkCb = Callable[[dict[str, Any]], None]
ErrCb = Callable[[BaseException], None]
FetchFn = Callable[[OkCb, ErrCb], None]
SaveFn = Callable[[list[str], list[str], OkCb, ErrCb], None]


def _parse_lines(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ln in (raw or "").splitlines():
        text = ln.strip()
        if not text or text.startswith("#"):
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


class ThemesAdminPanel(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        fetch: FetchFn,
        save: SaveFn,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._fetch = fetch
        self._save = save
        self._on_log = on_log or (lambda _s: None)
        self._busy = False
        self.status_var = tk.StringVar(value="Connect to load themes.")

        top = ttk.Frame(self)
        top.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(top, text="Reload", command=self.reload).pack(side=tk.LEFT)
        ttk.Button(top, text="Save both lists", command=self.save).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Label(top, textvariable=self.status_var).pack(
            side=tk.LEFT, padx=(12, 0)
        )

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        main_fr = ttk.Labelframe(paned, text="Main pool (active)", padding=6)
        sec_fr = ttk.Labelframe(paned, text="Secondary (used)", padding=6)
        paned.add(main_fr, weight=1)
        paned.add(sec_fr, weight=1)

        self.main_text = tk.Text(main_fr, wrap=tk.NONE, undo=True)
        self.sec_text = tk.Text(sec_fr, wrap=tk.NONE, undo=True)
        for box, parent in ((self.main_text, main_fr), (self.sec_text, sec_fr)):
            ys = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=box.yview)
            box.configure(yscrollcommand=ys.set)
            box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            ys.pack(side=tk.RIGHT, fill=tk.Y)

        tip = ttk.Label(
            self,
            text="One theme per line. Save replaces both pools on the server (same as /list_themes).",
            wraplength=640,
        )
        tip.pack(anchor=tk.W, pady=(6, 0))

    def reload(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.status_var.set("Loading…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") != "themes_list_ok":
                self.status_var.set(str(body.get("error") or "load failed"))
                return
            main = body.get("main") or []
            sec = body.get("secondary") or []
            self.main_text.delete("1.0", tk.END)
            self.sec_text.delete("1.0", tk.END)
            if main:
                self.main_text.insert("1.0", "\n".join(str(x) for x in main) + "\n")
            if sec:
                self.sec_text.insert("1.0", "\n".join(str(x) for x in sec) + "\n")
            self.status_var.set(f"Loaded · main {len(main)} · secondary {len(sec)}")
            self._on_log(f"Themes loaded ({len(main)}+{len(sec)})")

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.status_var.set(f"Load failed: {exc}")

        self._fetch(on_ok, on_err)

    def save(self) -> None:
        if self._busy:
            return
        main = _parse_lines(self.main_text.get("1.0", "end-1c"))
        sec = _parse_lines(self.sec_text.get("1.0", "end-1c"))
        if not messagebox.askyesno(
            "Save themes",
            f"Replace server pools?\nMain: {len(main)}\nSecondary: {len(sec)}",
            parent=self,
        ):
            return
        self._busy = True
        self.status_var.set("Saving…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") != "themes_save_ok":
                self.status_var.set(str(body.get("error") or "save failed"))
                return
            self.status_var.set(
                f"Saved · main {body.get('main_count')} · "
                f"secondary {body.get('secondary_count')}"
            )
            self._on_log("Themes saved")

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.status_var.set(f"Save failed: {exc}")

        self._save(main, sec, on_ok, on_err)
