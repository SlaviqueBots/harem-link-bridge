"""Sets browser — list set names + fill-viewport card grid."""

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
    PAGE_SIZE,
    ROWS,
    USER_AGENT,
    compute_thumb,
)

logger = logging.getLogger(__name__)

RESIZE_DEBOUNCE_MS = 120

OkCb = Callable[[dict[str, Any]], None]
ErrCb = Callable[[BaseException], None]
ListSetsFn = Callable[[OkCb, ErrCb], None]
FetchPageFn = Callable[[int, str, int, str, OkCb, ErrCb], None]
PostGridFn = Callable[[int, OkCb, ErrCb], None]
FocusPrefFn = Callable[[], bool]


class SetsPanel(ttk.Frame):
    """Left: set names. Right: all cards in that set (no done/undone tabs)."""

    def __init__(
        self,
        master,
        *,
        list_sets: ListSetsFn,
        fetch_page: FetchPageFn,
        post_grid: PostGridFn,
        should_focus_telegram: FocusPrefFn | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._list_sets = list_sets
        self._fetch_page = fetch_page
        self._post_grid = post_grid
        self._should_focus = should_focus_telegram or (lambda: False)
        self._on_log = on_log or (lambda _s: None)
        self._selected = ""
        self._page = 0
        self._total = 0
        self._page_size = PAGE_SIZE
        self._items: list[dict[str, Any]] = []
        self._photos: list[Any] = []
        self._img_bytes: dict[str, bytes] = {}
        self._thumb = 140
        self._busy = False
        self._gen = 0
        self._resize_after: str | None = None

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, padding=4)
        right = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        paned.add(right, weight=4)

        head = ttk.Frame(left)
        head.pack(fill=tk.X)
        ttk.Label(head, text="Sets").pack(side=tk.LEFT)
        ttk.Button(head, text="Refresh", command=self.refresh_sets).pack(side=tk.RIGHT)

        self._list = tk.Listbox(left, exportselection=False)
        self._list.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self._list.bind("<<ListboxSelect>>", self._on_select)
        self._names: list[str] = []

        bar = ttk.Frame(right)
        bar.pack(fill=tk.X)
        self.prev_btn = ttk.Button(bar, text="◀ Prev", command=self.prev_page)
        self.prev_btn.pack(side=tk.LEFT)
        self.next_btn = ttk.Button(bar, text="Next ▶", command=self.next_page)
        self.next_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.meta_var = tk.StringVar(value="Pick a set on the left.")
        ttk.Label(bar, textvariable=self.meta_var).pack(side=tk.LEFT, padx=(12, 0))

        self.grid_fr = ttk.Frame(right)
        self.grid_fr.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        for c in range(COLS):
            self.grid_fr.columnconfigure(c, weight=1, uniform="col")
        for r in range(ROWS):
            self.grid_fr.rowconfigure(r, weight=1, uniform="row")
        self.grid_fr.bind("<Configure>", self._on_grid_resize)
        self._set_nav(False)

    def refresh_sets(self) -> None:
        self.meta_var.set("Loading sets…")

        def on_ok(body: dict) -> None:
            if body.get("op") != "sets_list_ok":
                err = body.get("error") or "failed"
                self.meta_var.set(f"Sets error: {err}")
                self._on_log(f"Sets error: {err}")
                return
            self._names = [str(x) for x in (body.get("sets") or []) if str(x).strip()]
            self._list.delete(0, tk.END)
            for name in self._names:
                self._list.insert(tk.END, name)
            if not self._names:
                self._selected = ""
                self.clear_grid()
                self.meta_var.set("No sets yet.")
                return
            if self._selected in self._names:
                idx = self._names.index(self._selected)
                self._list.selection_set(idx)
                self._list.see(idx)
                self._open_set(self._selected)
            else:
                self._list.selection_set(0)
                self._open_set(self._names[0])

        def on_err(exc: BaseException) -> None:
            self.meta_var.set(f"Sets error: {exc}")
            self._on_log(f"Sets error: {exc}")

        self._list_sets(on_ok, on_err)

    def clear(self) -> None:
        self._names = []
        self._selected = ""
        self._list.delete(0, tk.END)
        self._img_bytes.clear()
        self.clear_grid()
        self.meta_var.set("Pick a set on the left.")

    def clear_grid(self) -> None:
        self._gen += 1
        self._items = []
        self._total = 0
        self._page = 0
        for child in self.grid_fr.winfo_children():
            child.destroy()
        self._photos.clear()
        self._set_nav(False)

    def _on_select(self, _event=None) -> None:
        sel = self._list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(self._names):
            return
        self._open_set(self._names[idx])

    def _open_set(self, name: str) -> None:
        self._selected = name
        self.load_page(0)

    def prev_page(self) -> None:
        if self._page > 0:
            self.load_page(self._page - 1)

    def next_page(self) -> None:
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        if self._page + 1 < pages:
            self.load_page(self._page + 1)

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

    def _on_grid_resize(self, _event=None) -> None:
        if self._resize_after is not None:
            try:
                self.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.after(RESIZE_DEBOUNCE_MS, self._apply_resize)

    def _apply_resize(self) -> None:
        self._resize_after = None
        if not self._items:
            return
        w = max(1, self.grid_fr.winfo_width())
        h = max(1, self.grid_fr.winfo_height())
        new = compute_thumb(w, h)
        if abs(new - self._thumb) < 8:
            return
        self._thumb = new
        self._render_grid(reuse_bytes=True)

    def load_page(self, page: int = 0) -> None:
        if not self._selected:
            return
        self._busy = True
        self._gen += 1
        gen = self._gen
        set_name = self._selected
        self.meta_var.set(f"Loading “{set_name}” page {page + 1}…")
        self._set_nav(False)

        def on_ok(body: dict) -> None:
            self._busy = False
            if gen != self._gen:
                return
            if body.get("op") != "roster_page_ok":
                err = body.get("error") or "failed"
                self.meta_var.set(f"Set error: {err}")
                self._set_nav(True)
                return
            self._page = int(body.get("page") or 0)
            self._page_size = int(body.get("page_size") or PAGE_SIZE)
            self._total = int(body.get("total") or 0)
            self._items = list(body.get("items") or [])
            pages = max(1, (self._total + self._page_size - 1) // self._page_size)
            self.meta_var.set(
                f"“{set_name}” · page {self._page + 1}/{pages} · {self._total} cards"
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
            self.meta_var.set(f"Set error: {exc}")
            self._set_nav(True)

        self._fetch_page(int(page), "", -1, set_name, on_ok, on_err)

    def _render_grid(self, *, reuse_bytes: bool) -> None:
        for child in self.grid_fr.winfo_children():
            child.destroy()
        self._photos.clear()
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
            thumb_lbl.bind("<Button-1>", lambda _e, x=cid: self._click_post(x))
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
        post_url: str,
        char_id: int,
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
                label.bind("<Button-1>", lambda _e, x=char_id: self._click_post(x))
                label.bind("<Button-3>", lambda _e, u=post_url: self._open_post(u))
            except Exception:
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
            except Exception:
                label.after(
                    0,
                    lambda: label.configure(text="no preview")
                    if label.winfo_exists()
                    else None,
                )

        threading.Thread(target=worker, daemon=True).start()

    def _click_post(self, char_id: int) -> None:
        if char_id <= 0 or self._busy:
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

                        focus_telegram()
                    except Exception:
                        pass
            else:
                self.meta_var.set(f"Post failed: {body.get('error') or 'failed'}")
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.meta_var.set(f"Post failed: {exc}")
            self._set_nav(True)

        self._post_grid(int(char_id), on_ok, on_err)
