"""Paged roster: Undone / Done / Sets tabs + fill-viewport thumb grid."""

from __future__ import annotations

import io
import logging
import threading
import urllib.request
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import ttk

from link_bridge.thumb_grid import (
    COLS,
    DEFAULT_GEOMETRY,
    PAGE_SIZE,
    ROWS,
    USER_AGENT,
    compute_thumb,
)

logger = logging.getLogger(__name__)

SEARCH_DEBOUNCE_MS = 400
RESIZE_DEBOUNCE_MS = 120

# Re-export for gui / callers.
__all__ = ["RosterPanel", "DEFAULT_GEOMETRY", "PAGE_SIZE", "COLS", "ROWS"]

OkCb = Callable[[dict[str, Any]], None]
ErrCb = Callable[[BaseException], None]
FetchPageFn = Callable[[int, str, int, str, OkCb, ErrCb], None]
OpenOmniFn = Callable[[int, OkCb, ErrCb], None]
PostGridFn = Callable[[int, OkCb, ErrCb], None]
FocusPrefFn = Callable[[], bool]
ListSetsFn = Callable[[OkCb, ErrCb], None]


class RosterPanel(ttk.Frame):
    """Undone | Done | Sets — sets sits with done/undone, not in the top bar."""

    def __init__(
        self,
        master,
        *,
        fetch_page: FetchPageFn,
        open_omni: OpenOmniFn,
        post_grid: PostGridFn | None = None,
        list_sets: ListSetsFn | None = None,
        should_focus_telegram: FocusPrefFn | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._fetch_page = fetch_page
        self._open_omni = open_omni
        self._post_grid = post_grid
        self._list_sets = list_sets
        self._should_focus = should_focus_telegram or (lambda: False)
        self._on_log = on_log or (lambda _s: None)
        self._page = 0
        self._total = 0
        self._page_size = PAGE_SIZE
        self._query = ""
        self._done = 0
        self._mode = "undone"  # undone | done | sets
        self._items: list[dict[str, Any]] = []
        self._photos: list[Any] = []
        self._img_bytes: dict[str, bytes] = {}
        self._thumb = 140
        self._busy = False
        self._gen = 0
        self._search_after: str | None = None
        self._resize_after: str | None = None

        self._tab_nb = ttk.Notebook(self)
        self._tab_nb.pack(fill=tk.X)
        self._tab_undone = ttk.Frame(self._tab_nb)
        self._tab_done = ttk.Frame(self._tab_nb)
        self._tab_sets = ttk.Frame(self._tab_nb)
        self._tab_nb.add(self._tab_undone, text="Undone")
        self._tab_nb.add(self._tab_done, text="Done")
        self._tab_nb.add(self._tab_sets, text="Sets")
        self._tab_nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._tab_nb.configure(height=1)
        self._tab_nb.pack_propagate(False)
        self.after_idle(self._shrink_tab_bar)

        self._roster_body = ttk.Frame(self)
        self._roster_body.pack(fill=tk.BOTH, expand=True)
        self._build_roster_chrome(self._roster_body)

        self._sets_body = ttk.Frame(self)
        self._sets_panel = None
        if list_sets is not None and post_grid is not None:
            from link_bridge.sets import SetsPanel

            self._sets_panel = SetsPanel(
                self._sets_body,
                list_sets=list_sets,
                fetch_page=fetch_page,
                post_grid=post_grid,
                should_focus_telegram=should_focus_telegram,
                on_log=on_log,
            )
            self._sets_panel.pack(fill=tk.BOTH, expand=True)

        self._set_nav(False)

    def _build_roster_chrome(self, parent: ttk.Frame) -> None:
        search_row = ttk.Frame(parent)
        search_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(search_row, text="Search").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.search_entry.bind("<Return>", lambda _e: self._search_now())
        self.search_var.trace_add("write", self._on_search_typed)
        from link_bridge.theme import bind_entry_clipboard

        bind_entry_clipboard(self.search_entry)
        ttk.Button(search_row, text="Search", command=self._search_now).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(search_row, text="Clear", command=self._clear_search).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=(6, 0))
        self.prev_btn = ttk.Button(bar, text="◀ Prev", command=self.prev_page)
        self.prev_btn.pack(side=tk.LEFT)
        self.next_btn = ttk.Button(bar, text="Next ▶", command=self.next_page)
        self.next_btn.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        self.meta_var = tk.StringVar(value="Connect to load roster.")
        ttk.Label(bar, textvariable=self.meta_var).pack(side=tk.LEFT, padx=(12, 0))

        self.grid_fr = ttk.Frame(parent)
        self.grid_fr.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        for c in range(COLS):
            self.grid_fr.columnconfigure(c, weight=1, uniform="col")
        for r in range(ROWS):
            self.grid_fr.rowconfigure(r, weight=1, uniform="row")
        self.grid_fr.bind("<Configure>", self._on_grid_resize)

    def _shrink_tab_bar(self) -> None:
        try:
            h = max(28, self._tab_nb.winfo_reqheight())
            self._tab_nb.configure(height=h)
        except Exception:
            self._tab_nb.configure(height=32)

    def _show_roster_mode(self) -> None:
        self._sets_body.pack_forget()
        if not self._roster_body.winfo_ismapped():
            self._roster_body.pack(fill=tk.BOTH, expand=True)

    def _show_sets_mode(self) -> None:
        self._roster_body.pack_forget()
        if not self._sets_body.winfo_ismapped():
            self._sets_body.pack(fill=tk.BOTH, expand=True)
        if self._sets_panel is not None:
            self._sets_panel.refresh_sets()

    def _on_tab_changed(self, _event=None) -> None:
        try:
            idx = int(self._tab_nb.index(self._tab_nb.select()))
        except Exception:
            return
        if idx == 2:
            self._mode = "sets"
            self._show_sets_mode()
            return
        self._mode = "done" if idx == 1 else "undone"
        self._done = 1 if idx == 1 else 0
        self._show_roster_mode()
        self.load_page(0)

    def _on_grid_resize(self, _event=None) -> None:
        if self._resize_after is not None:
            try:
                self.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.after(RESIZE_DEBOUNCE_MS, self._apply_resize)

    def _apply_resize(self) -> None:
        self._resize_after = None
        if self._mode == "sets" or not self._items:
            return
        w = max(1, self.grid_fr.winfo_width())
        h = max(1, self.grid_fr.winfo_height())
        new = compute_thumb(w, h)
        if abs(new - self._thumb) < 8:
            return
        self._thumb = new
        self._render_grid(reuse_bytes=True)

    def _cancel_search_timer(self) -> None:
        if self._search_after is not None:
            try:
                self.after_cancel(self._search_after)
            except Exception:
                pass
            self._search_after = None

    def _on_search_typed(self, *_args) -> None:
        self._cancel_search_timer()
        self._search_after = self.after(SEARCH_DEBOUNCE_MS, self._search_now)

    def _search_now(self) -> None:
        self._cancel_search_timer()
        self._query = (self.search_var.get() or "").strip()
        self.load_page(0)

    def _clear_search(self) -> None:
        self.search_var.set("")
        self._query = ""
        self.load_page(0)

    def _set_nav(self, enabled: bool) -> None:
        pages = (
            max(1, (self._total + self._page_size - 1) // self._page_size)
            if self._total
            else 1
        )
        self.prev_btn.configure(
            state=tk.NORMAL if enabled and self._page > 0 else tk.DISABLED
        )
        self.next_btn.configure(
            state=tk.NORMAL if enabled and (self._page + 1) < pages else tk.DISABLED
        )

    def refresh(self) -> None:
        if self._mode == "sets":
            if self._sets_panel is not None:
                self._sets_panel.refresh_sets()
            return
        self._query = (self.search_var.get() or "").strip()
        self.load_page(self._page)

    def prev_page(self) -> None:
        if self._page > 0:
            self.load_page(self._page - 1)

    def next_page(self) -> None:
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        if self._page + 1 < pages:
            self.load_page(self._page + 1)

    def load_page(self, page: int = 0) -> None:
        if self._mode == "sets":
            return
        self._busy = True
        self._gen += 1
        gen = self._gen
        q = self._query
        done = int(self._done)
        kind = "done" if done else "undone"
        hint = f" “{q}”" if q else ""
        self.meta_var.set(f"Loading {kind} page {page + 1}{hint}…")
        self._set_nav(False)

        def on_ok(body: dict) -> None:
            self._busy = False
            if gen != self._gen:
                return
            if body.get("op") != "roster_page_ok":
                err = body.get("error") or "failed"
                self.meta_var.set(f"Roster error: {err}")
                self._on_log(f"Roster error: {err}")
                self._set_nav(True)
                return
            self._page = int(body.get("page") or 0)
            self._page_size = int(body.get("page_size") or PAGE_SIZE)
            self._total = int(body.get("total") or 0)
            self._items = list(body.get("items") or [])
            pages = max(1, (self._total + self._page_size - 1) // self._page_size)
            q_bit = f" · “{q}”" if q else ""
            self.meta_var.set(
                f"{kind.capitalize()} · page {self._page + 1}/{pages} · "
                f"{self._total} cards{q_bit}"
            )
            self._thumb = compute_thumb(
                max(1, self.grid_fr.winfo_width()),
                max(1, self.grid_fr.winfo_height()),
            )
            self._render_grid(reuse_bytes=False)
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            if gen != self._gen:
                return
            self.meta_var.set(f"Roster error: {exc}")
            self._on_log(f"Roster error: {exc}")
            self._set_nav(True)

        self._fetch_page(int(page), q, done, "", on_ok, on_err)

    def clear(self) -> None:
        self._gen += 1
        self._items = []
        self._total = 0
        self._page = 0
        self._busy = False
        self._img_bytes.clear()
        self.meta_var.set("Connect to load roster.")
        self._clear_grid()
        self._set_nav(False)
        if self._sets_panel is not None:
            self._sets_panel.clear()

    def _clear_grid(self) -> None:
        for child in self.grid_fr.winfo_children():
            child.destroy()
        self._photos.clear()

    def _render_grid(self, *, reuse_bytes: bool) -> None:
        self._clear_grid()
        thumb = self._thumb
        for i, item in enumerate(self._items):
            r, c = divmod(i, COLS)
            cell = ttk.Frame(self.grid_fr)
            cell.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
            box = tk.Frame(cell, width=thumb, height=thumb)
            box.pack_propagate(False)
            box.pack(expand=True)
            thumb_lbl = tk.Label(box, text="…", relief=tk.GROOVE, cursor="hand2")
            thumb_lbl.pack(fill=tk.BOTH, expand=True)
            name = (item.get("name") or f"#{item.get('id')}")[:22]
            ttk.Label(cell, text=name, wraplength=max(60, thumb)).pack()
            cid = int(item.get("id") or 0)
            post_url = (item.get("post_url") or "").strip()
            thumb_lbl.bind("<Button-1>", lambda _e, x=cid: self._click_primary(x))
            thumb_lbl.bind("<Button-3>", lambda _e, u=post_url: self._open_post(u))
            url = (item.get("preview_url") or "").strip()
            if url:
                self._load_thumb(
                    thumb_lbl,
                    url,
                    self._gen,
                    post_url=post_url,
                    char_id=cid,
                    reuse_bytes=reuse_bytes,
                )

    def _open_post(self, post_url: str) -> None:
        url = (post_url or "").strip()
        if not url:
            self._on_log("No post link for this card")
            return
        try:
            from link_bridge.browser_open import open_url

            open_url(url)
            self._on_log(f"Open post: {url}")
        except Exception as exc:
            self._on_log(f"Open post failed: {exc}")

    def _load_thumb(
        self,
        label: tk.Label,
        url: str,
        gen: int,
        *,
        post_url: str = "",
        char_id: int = 0,
        reuse_bytes: bool = False,
    ) -> None:
        thumb = self._thumb

        def apply_bytes(data: bytes) -> None:
            if gen != self._gen or not label.winfo_exists():
                return
            try:
                from PIL import Image, ImageOps, ImageTk

                im = Image.open(io.BytesIO(data)).convert("RGB")
                im = ImageOps.fit(im, (thumb, thumb), method=Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(im)
                self._photos.append(photo)
                label.configure(image=photo, text="")
                label.bind("<Button-1>", lambda _e, x=char_id: self._click_primary(x))
                label.bind("<Button-3>", lambda _e, u=post_url: self._open_post(u))
            except Exception as exc:
                logger.debug("thumb decode failed: %s", exc)
                label.configure(text="no preview")

        cached = self._img_bytes.get(url)
        if cached is not None:
            apply_bytes(cached)
            return

        def worker() -> None:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = resp.read()
                self._img_bytes[url] = data
                label.after(0, lambda: apply_bytes(data))
            except Exception as exc:
                logger.debug("thumb failed %s: %s", url[:60], exc)

                def fail() -> None:
                    if gen != self._gen or not label.winfo_exists():
                        return
                    label.configure(text="no preview")

                label.after(0, fail)

        threading.Thread(target=worker, daemon=True).start()

    def _click_primary(self, char_id: int) -> None:
        # Done tab posts into the main group (same as Sets). Undone keeps omni.
        if self._mode == "done" and self._post_grid is not None:
            self._click_post(char_id)
            return
        self._click_omni(char_id)

    def _click_post(self, char_id: int) -> None:
        if char_id <= 0 or self._busy or self._post_grid is None:
            return
        self._busy = True
        self.meta_var.set(f"Posting #{char_id} to main group…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "post_grid_ok":
                self.meta_var.set(f"Posted #{char_id} → main group")
                self._on_log(f"Grid post sent for char {char_id}")
                if self._should_focus():
                    try:
                        from link_bridge.focus_telegram import focus_telegram

                        if focus_telegram():
                            self._on_log("Focused Telegram window")
                        else:
                            self._on_log("Telegram window not found")
                    except Exception:
                        logger.debug("focus telegram failed", exc_info=True)
            else:
                self.meta_var.set(f"Post failed: {body.get('error') or 'failed'}")
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.meta_var.set(f"Post failed: {exc}")
            self._set_nav(True)

        self._post_grid(int(char_id), on_ok, on_err)

    def _click_omni(self, char_id: int) -> None:
        if char_id <= 0 or self._busy:
            return
        self._busy = True
        self.meta_var.set(f"Opening #{char_id} in Telegram DM…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "open_omni_ok":
                self.meta_var.set(f"Sent #{char_id} → Telegram DM")
                self._on_log(f"Omnicraft sent for char {char_id}")
                if self._should_focus():
                    try:
                        from link_bridge.focus_telegram import focus_telegram

                        if focus_telegram():
                            self._on_log("Focused Telegram window")
                        else:
                            self._on_log("Telegram window not found")
                    except Exception:
                        logger.debug("focus telegram failed", exc_info=True)
            else:
                self.meta_var.set(f"Open failed: {body.get('error') or 'failed'}")
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.meta_var.set(f"Open failed: {exc}")
            self._set_nav(True)

        self._open_omni(int(char_id), on_ok, on_err)
