"""Bridge Market tab — browse @buy listings with price filters."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk

from link_bridge.thumb_grid import (
    COLS,
    PAGE_SIZE,
    ROWS,
    compute_thumb,
    decode_thumb,
    release_photos,
    schedule_thumb_fetch,
)

logger = logging.getLogger(__name__)

OkCb = Callable[[dict[str, Any]], None]
ErrCb = Callable[[BaseException], None]
FetchMarketFn = Callable[..., None]
BuyMarketFn = Callable[[int, OkCb, ErrCb], None]
PreferOriginalFn = Callable[[], bool]

SEARCH_DEBOUNCE_MS = 400
RESIZE_DEBOUNCE_MS = 120


class MarketPanel(ttk.Frame):
    """Paged market lots with min/max price filter and buy confirm."""

    def __init__(
        self,
        master,
        *,
        fetch_page: FetchMarketFn,
        buy_listing: BuyMarketFn,
        prefer_original_open: PreferOriginalFn | None = None,
        preview_scale: float = 1.5,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._fetch_page = fetch_page
        self._buy_listing = buy_listing
        self._prefer_original = prefer_original_open or (lambda: True)
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.5)))
        self._on_log = on_log or (lambda _s: None)
        self._page = 0
        self._total = 0
        self._page_size = PAGE_SIZE
        self._query = ""
        self._min_price = ""
        self._max_price = ""
        self._items: list[dict[str, Any]] = []
        self._photos: list[Any] = []
        self._thumb = 140
        self._busy = False
        self._gen = 0
        self._search_after: str | None = None
        self._resize_after: str | None = None

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="◀ Prev", command=self.prev_page).pack(side=tk.LEFT)
        self.prev_btn = bar.winfo_children()[-1]
        ttk.Button(bar, text="Next ▶", command=self.next_page).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        self.next_btn = bar.winfo_children()[-1]
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(
            side=tk.LEFT, padx=(12, 0)
        )
        self.meta_var = tk.StringVar(value="Open Market to load listings.")
        ttk.Label(bar, textvariable=self.meta_var).pack(side=tk.LEFT, padx=(12, 0))

        filt = ttk.Frame(self)
        filt.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(filt, text="Search").pack(side=tk.LEFT)
        self.search_var = tk.StringVar(value="")
        ent = ttk.Entry(filt, textvariable=self.search_var, width=28)
        ent.pack(side=tk.LEFT, padx=(6, 0))
        self.search_var.trace_add("write", self._on_search_typed)
        ttk.Label(filt, text="Min").pack(side=tk.LEFT, padx=(12, 0))
        from link_bridge.pig_snout import pack_pig_label

        pack_pig_label(filt, "", size=16, side=tk.LEFT, padx=(2, 0))
        self.min_var = tk.StringVar(value="")
        ttk.Entry(filt, textvariable=self.min_var, width=8).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(filt, text="Max").pack(side=tk.LEFT, padx=(8, 0))
        pack_pig_label(filt, "", size=16, side=tk.LEFT, padx=(2, 0))
        self.max_var = tk.StringVar(value="")
        ttk.Entry(filt, textvariable=self.max_var, width=8).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(filt, text="Apply prices", command=self._apply_prices).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(filt, text="Clear", command=self._clear_filters).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        self.grid_fr = ttk.Frame(self)
        self.grid_fr.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        for c in range(COLS):
            self.grid_fr.columnconfigure(c, weight=1, uniform="mcol")
        for r in range(ROWS):
            self.grid_fr.rowconfigure(r, weight=1, uniform="mrow")
        self.grid_fr.bind("<Configure>", self._on_grid_resize)
        self._set_nav(False)

    def set_preview_scale(self, scale: float) -> None:
        self._preview_scale = max(0.5, min(2.0, float(scale or 1.5)))
        if self._items:
            self._render_grid()

    def refresh(self) -> None:
        self._query = (self.search_var.get() or "").strip()
        self._min_price = (self.min_var.get() or "").strip()
        self._max_price = (self.max_var.get() or "").strip()
        self.load_page(self._page)

    def prev_page(self) -> None:
        if self._page > 0:
            self.load_page(self._page - 1)

    def next_page(self) -> None:
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        if self._page + 1 < pages:
            self.load_page(self._page + 1)

    def _apply_prices(self) -> None:
        self._min_price = (self.min_var.get() or "").strip()
        self._max_price = (self.max_var.get() or "").strip()
        self.load_page(0)

    def _clear_filters(self) -> None:
        self.search_var.set("")
        self.min_var.set("")
        self.max_var.set("")
        self._query = ""
        self._min_price = ""
        self._max_price = ""
        self.load_page(0)

    def _on_search_typed(self, *_args) -> None:
        if self._search_after is not None:
            try:
                self.after_cancel(self._search_after)
            except Exception:
                pass
        self._search_after = self.after(SEARCH_DEBOUNCE_MS, self._search_now)

    def _search_now(self) -> None:
        self._search_after = None
        self._query = (self.search_var.get() or "").strip()
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
        self._render_grid()

    def load_page(self, page: int = 0) -> None:
        self._busy = True
        self._gen += 1
        gen = self._gen
        q = self._query
        self.meta_var.set(f"Loading market page {page + 1}…")
        self._set_nav(False)

        def on_ok(body: dict) -> None:
            self._busy = False
            if gen != self._gen:
                return
            if body.get("op") != "market_page_ok":
                err = body.get("error") or "failed"
                self.meta_var.set(f"Market error: {err}")
                self._on_log(f"Market error: {err}")
                self._set_nav(True)
                return
            self._page = int(body.get("page") or 0)
            self._page_size = int(body.get("page_size") or PAGE_SIZE)
            self._total = int(body.get("total") or 0)
            self._items = list(body.get("items") or [])
            pages = max(1, (self._total + self._page_size - 1) // self._page_size)
            q_bit = f" · “{q}”" if q else ""
            price_bit = ""
            if self._min_price or self._max_price:
                price_bit = f" · 🐷{self._min_price or '0'}–{self._max_price or '∞'}"
            self.meta_var.set(
                f"Market · page {self._page + 1}/{pages} · {self._total} lots"
                f"{q_bit}{price_bit}"
            )
            self._thumb = compute_thumb(
                max(1, self.grid_fr.winfo_width()),
                max(1, self.grid_fr.winfo_height()),
            )
            self._render_grid()
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            if gen != self._gen:
                return
            self.meta_var.set(f"Market error: {exc}")
            self._on_log(f"Market error: {exc}")
            self._set_nav(True)

        self._fetch_page(
            int(page),
            q,
            self._min_price,
            self._max_price,
            on_ok,
            on_err,
        )

    def _clear_grid(self) -> None:
        for child in list(self.grid_fr.winfo_children()):
            child.destroy()
        release_photos(self._photos)

    def _render_grid(self) -> None:
        self._clear_grid()
        items = list(self._items)
        if not items:
            ttk.Label(self.grid_fr, text="No listings match.").grid(
                row=0, column=0, sticky="nsew"
            )
            return
        from link_bridge.theme import surface_for

        c = surface_for(self)
        surf = c.get("canvas", "#1e1f22")
        bg = c.get("bg", surf)
        fg = c.get("fg", "#f2f3f5")
        muted = c.get("muted", "#b5bac1")
        for cidx in range(COLS):
            self.grid_fr.columnconfigure(cidx, weight=1, uniform="mcol")
        for ridx in range(ROWS):
            self.grid_fr.rowconfigure(ridx, weight=1, uniform="mrow")
        thumb = self._thumb
        for i, item in enumerate(items):
            r, ccol = divmod(i, COLS)
            cell = ttk.Frame(self.grid_fr)
            cell.grid(row=r, column=ccol, sticky="nsew", padx=3, pady=3)
            box = tk.Frame(cell, width=thumb, height=thumb, bd=0, highlightthickness=0)
            box.pack_propagate(False)
            box.pack(expand=True)
            box.configure(bg=surf)
            thumb_lbl = tk.Label(
                box,
                text="…",
                relief=tk.FLAT,
                cursor="hand2",
                bd=0,
                highlightthickness=0,
                bg=surf,
                fg=muted,
            )
            thumb_lbl.pack(fill=tk.BOTH, expand=True)
            cid = int(item.get("id") or 0)
            lid = int(item.get("listing_id") or 0)
            price = int(item.get("price") or 0)
            seller = (item.get("seller") or "").strip()
            name = (item.get("name") or f"#{cid}")[:22]
            mine = bool(item.get("mine"))
            buyable = bool(item.get("buyable"))
            kind = str(item.get("kind") or "sell")
            caption = f"{price}"
            if seller:
                caption = f"{caption} · @{seller.lstrip('@')}"
            if mine:
                caption = f"{caption} · yours"
            elif kind == "trash":
                grace = int(item.get("trash_grace_left") or 0)
                if grace > 0:
                    caption = f"{caption} · trash {grace}s"
                else:
                    caption = f"{caption} · trash"
            ttk.Label(
                cell,
                text=f"#{cid} {name}",
                wraplength=max(60, thumb),
                justify=tk.CENTER,
            ).pack()
            price_row = tk.Frame(cell, bd=0, highlightthickness=0, bg=bg)
            price_row.pack()
            from link_bridge.pig_snout import pig_photo

            photo = pig_photo(price_row, size=14)
            if photo is not None:
                sn = tk.Label(
                    price_row,
                    image=photo,
                    bd=0,
                    bg=bg,
                    highlightthickness=0,
                )
                sn.image = photo  # type: ignore[attr-defined]
                sn.pack(side=tk.LEFT, padx=(0, 2))
            else:
                tk.Label(
                    price_row,
                    text="🐷 ",
                    font=("Segoe UI Emoji", 9),
                    bd=0,
                    bg=bg,
                    fg=fg,
                    highlightthickness=0,
                ).pack(side=tk.LEFT)
            tk.Label(
                price_row,
                text=caption,
                wraplength=max(48, thumb - 18),
                justify=tk.LEFT,
                font=("Segoe UI", 9),
                bd=0,
                bg=bg,
                fg=fg,
                highlightthickness=0,
            ).pack(side=tk.LEFT)
            post_url = (item.get("post_url") or "").strip()
            self._bind_thumb(
                thumb_lbl,
                cid,
                lid,
                post_url,
                buyable=buyable,
                price=price,
                name=str(item.get("name") or name),
            )
            url = (item.get("preview_url") or item.get("image_url") or "").strip()
            if url:
                self._load_thumb(
                    thumb_lbl,
                    url,
                    self._gen,
                    cid=cid,
                    lid=lid,
                    post_url=post_url,
                    buyable=buyable,
                    price=price,
                    name=str(item.get("name") or name),
                )

    def _load_thumb(
        self,
        label: tk.Label,
        url: str,
        gen: int,
        *,
        cid: int,
        lid: int,
        post_url: str,
        buyable: bool,
        price: int,
        name: str,
    ) -> None:
        thumb = self._thumb

        def apply_bytes(data: bytes) -> None:
            if gen != self._gen or not label.winfo_exists():
                return
            try:
                photo = decode_thumb(data, thumb, natural=False)
                self._photos.append(photo)
                label.configure(image=photo, text="")
                self._bind_thumb(
                    label,
                    cid,
                    lid,
                    post_url,
                    buyable=buyable,
                    price=price,
                    name=name,
                )
            except Exception:
                label.configure(text="no preview")

        def on_fail(_exc: BaseException) -> None:
            if gen != self._gen or not label.winfo_exists():
                return
            label.configure(text="no preview")

        schedule_thumb_fetch(url, on_data=apply_bytes, on_err=on_fail)

    def _bind_thumb(
        self,
        label: tk.Label,
        char_id: int,
        listing_id: int,
        post_url: str,
        *,
        buyable: bool,
        price: int,
        name: str,
    ) -> None:
        label.bind("<Button-1>", lambda _e, x=char_id: self._click_open_image(x))
        if buyable and listing_id > 0:
            label.bind(
                "<Button-3>",
                lambda e, lid=listing_id, p=price, n=name: self._popup_buy(
                    e, lid, p, n
                ),
            )
        if post_url:
            label.bind(
                "<Control-Button-1>",
                lambda _e, u=post_url: self._open_post(u),
            )

    def _item_by_id(self, char_id: int) -> dict[str, Any] | None:
        for it in self._items:
            try:
                if int(it.get("id") or 0) == int(char_id):
                    return it
            except Exception:
                continue
        return None

    def _click_open_image(self, char_id: int) -> None:
        from link_bridge.open_image import open_full_image

        item = self._item_by_id(char_id) or {}
        prefer = bool(self._prefer_original())
        file_u = (item.get("file_url") or "").strip()
        img_u = (item.get("image_url") or "").strip()
        prev_u = (item.get("preview_url") or "").strip()
        url = (file_u or img_u or prev_u) if prefer else (img_u or file_u or prev_u)
        if not url:
            self.meta_var.set(f"No image URL for #{char_id}")
            return
        self.meta_var.set(f"Opening image #{char_id}…")

        def on_err(exc: BaseException) -> None:
            self.after(0, lambda: self.meta_var.set(f"Open image failed: {exc}"))

        open_full_image(url, on_err=on_err)

    def _open_post(self, post_url: str) -> None:
        from link_bridge.browser_open import open_url

        url = (post_url or "").strip()
        if not url:
            return
        try:
            open_url(url)
        except Exception as exc:
            self._on_log(f"Open post failed: {exc}")

    def _popup_buy(self, event, listing_id: int, price: int, name: str) -> None:
        menu = tk.Menu(self, tearoff=0)
        label = f"Buy {name} for {price}🐷"
        menu.add_command(
            label=label if len(label) <= 48 else label[:45] + "…",
            command=lambda: self._confirm_buy(listing_id, price, name),
        )
        try:
            menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _confirm_buy(self, listing_id: int, price: int, name: str) -> None:
        if self._busy:
            return
        ok = messagebox.askyesno(
            "Buy listing",
            f"Buy “{name}” for {price}🐷?",
            parent=self,
        )
        if not ok:
            return
        self._busy = True
        self.meta_var.set(f"Buying {name}…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "market_buy_ok":
                self.meta_var.set(f"Bought {name}")
                self._on_log(f"Market buy ok listing={listing_id}")
                self.load_page(self._page)
            else:
                err = body.get("error") or "failed"
                self.meta_var.set(f"Buy failed: {err}")
                self._on_log(f"Market buy err: {err}")

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.meta_var.set(f"Buy failed: {exc}")
            self._on_log(f"Market buy failed: {exc}")

        self._buy_listing(int(listing_id), on_ok, on_err)
