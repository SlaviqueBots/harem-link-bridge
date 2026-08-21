"""Admin/editor Themes tab — main + secondary pools."""

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

_BG_EVEN = "#1a1a1a"
_BG_ODD = "#262626"
_FG = "#e6e6e6"
_SEL_BG = "#3d5a80"
_SIDE_BG = "#121212"
_FONT = ("Consolas", 14)
_TEXT_W = 960


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


def _restripe(box: tk.Text) -> None:
    box.tag_remove("odd", "1.0", tk.END)
    box.tag_remove("even", "1.0", tk.END)
    last = box.index("end-1c")
    try:
        end_line = int(float(last.split(".")[0]))
    except Exception:
        return
    for i in range(1, end_line + 1):
        tag = "odd" if (i % 2) else "even"
        box.tag_add(tag, f"{i}.0", f"{i}.0 lineend+1c")


def _style_box(box: tk.Text) -> None:
    box.configure(
        bg=_BG_EVEN,
        fg=_FG,
        insertbackground=_FG,
        selectbackground=_SEL_BG,
        selectforeground="#ffffff",
        relief=tk.FLAT,
        borderwidth=0,
        highlightthickness=0,
        padx=10,
        pady=6,
        font=_FONT,
        wrap=tk.WORD,
        width=88,
    )
    box.tag_configure("even", background=_BG_EVEN, foreground=_FG, justify=tk.LEFT)
    box.tag_configure("odd", background=_BG_ODD, foreground=_FG, justify=tk.LEFT)


class _PoolEditor(tk.Frame):
    """Full dark field; theme text centered — no light side gutters."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=_SIDE_BG, highlightthickness=0)

        mid = tk.Frame(self, bg=_SIDE_BG, highlightthickness=0)
        mid.place(relx=0.5, rely=0.0, anchor=tk.N, relheight=1.0, width=_TEXT_W)

        self.text = tk.Text(mid, undo=True, width=88, height=24)
        _style_box(self.text)
        # No scrollbar widget — wheel only (avoids the light Windows trough).
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.bind("<KeyRelease>", lambda _e: _restripe(self.text))
        self.text.bind(
            "<<Paste>>",
            lambda _e: self.text.after_idle(lambda: _restripe(self.text)),
        )
        self.text.bind("<MouseWheel>", self._on_wheel)

        def _fit(_event=None) -> None:
            w = max(420, min(_TEXT_W, int(self.winfo_width() * 0.72) or _TEXT_W))
            mid.place_configure(width=w)

        self.bind("<Configure>", _fit)

    def _on_wheel(self, event) -> str:
        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            self.text.yview_scroll(-1 if delta > 0 else 1, "units")
        return "break"

    def set_lines(self, lines: list[str]) -> None:
        self.text.delete("1.0", tk.END)
        if lines:
            self.text.insert("1.0", "\n".join(str(x) for x in lines) + "\n")
        _restripe(self.text)

    def get_lines(self) -> list[str]:
        return _parse_lines(self.text.get("1.0", "end-1c"))


class ThemesAdminPanel(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        fetch: FetchFn,
        save: SaveFn,
        on_log: Callable[[str], None] | None = None,
        **_extra: Any,
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

        self._nb = ttk.Notebook(self)
        self._nb.pack(fill=tk.BOTH, expand=True)

        self.main_pool = _PoolEditor(self._nb)
        self.sec_pool = _PoolEditor(self._nb)
        self._nb.add(self.main_pool, text="Main pool")
        self._nb.add(self.sec_pool, text="Secondary pool")

        self.main_text = self.main_pool.text
        self.sec_text = self.sec_pool.text

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
            self.main_pool.set_lines([str(x) for x in main])
            self.sec_pool.set_lines([str(x) for x in sec])
            self.status_var.set(f"Loaded · main {len(main)} · secondary {len(sec)}")
            self._on_log(f"Themes loaded ({len(main)}+{len(sec)})")

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.status_var.set(f"Load failed: {exc}")

        self._fetch(on_ok, on_err)

    def save(self) -> None:
        if self._busy:
            return
        main = self.main_pool.get_lines()
        sec = self.sec_pool.get_lines()
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
