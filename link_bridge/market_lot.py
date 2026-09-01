"""Market lot inspector — omni-style window for a single @buy listing."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk

from link_bridge.market_links import lot_link_grid_rows
from link_bridge.omni import omni_display_url
from link_bridge.thumb_grid import decode_thumb, schedule_thumb_fetch

logger = logging.getLogger(__name__)

OkCb = Callable[[dict[str, Any]], None]
ErrCb = Callable[[BaseException], None]
BuyFn = Callable[[int, OkCb, ErrCb], None]
PreferOriginalFn = Callable[[], bool]
FullImageGetFn = Callable[[], bool]
FullImageSetFn = Callable[[bool], None]
HideToggleFn = Callable[[int, bool], None]
GeoGetFn = Callable[[], str]
GeoSetFn = Callable[[str], None]

_VIEW = 520
_LINK_BG = "#1e4a5c"
_LINK_ALT_BG = "#2a4a3a"
_FG = "#f2f3f5"
_MAX_POOL = 16  # separate from roster page cache — recent lot windows kept in RAM


def _place_link_grid(
    parent: tk.Misc,
    rows: list[list[tuple[str, str, list[str]]]],
    *,
    open_url: Callable[[str], None],
    open_urls: Callable[[list[str]], None],
    bg: str,
) -> None:
    """Equal-width link buttons in a tight grid (same idea as Omnicraft link row)."""
    if not rows:
        return
    grid = tk.Frame(parent, bd=0, highlightthickness=0, bg=bg)
    grid.pack(fill=tk.X, pady=(2, 0))
    uniform = f"lot{id(grid)}"
    for r, specs in enumerate(rows):
        n = max(1, len(specs))
        for c in range(n):
            grid.columnconfigure(c, weight=1, uniform=uniform)
        for c, (label, url, urls) in enumerate(specs):
            text = label if len(label) <= 16 else label[:13] + "…"
            if urls:
                cmd = lambda us=list(urls): open_urls(us)
                btn_bg = _LINK_ALT_BG
                active_bg = "#35624a"
            else:
                cmd = lambda u=url: open_url(u)
                btn_bg = _LINK_BG
                active_bg = "#255a70"
            tk.Button(
                grid,
                text=text,
                command=cmd,
                bg=btn_bg,
                fg=_FG,
                activebackground=active_bg,
                activeforeground=_FG,
                relief=tk.FLAT,
                bd=0,
                padx=2,
                pady=1,
                cursor="hand2",
                font=("Segoe UI", 9),
            ).grid(row=r, column=c, sticky="nsew", padx=1, pady=1)


def _center_window(win: tk.Toplevel, width: int, height: int) -> None:
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = max(0, (sw - width) // 2)
    y = max(0, (sh - height) // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")


class MarketLotWindow(tk.Toplevel):
    """Inspect a market lot: image, seller, post/author links, buy."""

    _open: dict[int, "MarketLotWindow"] = {}
    _pool: OrderedDict[int, "MarketLotWindow"] = OrderedDict()

    def __init__(
        self,
        master: tk.Misc,
        item: dict[str, Any],
        *,
        buy_listing: BuyFn,
        prefer_original_open: PreferOriginalFn | None = None,
        full_image_get: FullImageGetFn | None = None,
        full_image_set: FullImageSetFn | None = None,
        on_hide_toggle: HideToggleFn | None = None,
        get_window_geo: GeoGetFn | None = None,
        set_window_geo: GeoSetFn | None = None,
        on_log: Callable[[str], None] | None = None,
        on_bought: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._sync_callbacks(
            buy_listing=buy_listing,
            prefer_original_open=prefer_original_open,
            full_image_get=full_image_get,
            full_image_set=full_image_set,
            on_hide_toggle=on_hide_toggle,
            get_window_geo=get_window_geo,
            set_window_geo=set_window_geo,
            on_log=on_log,
            on_bought=on_bought,
        )
        self._photo = None
        self._busy = False
        self._geo_ready = False
        self._geo_save_after: str | None = None
        self._shown_url: str | None = None
        self._ui_built = False
        self._bg = "#1e1f22"

        self.minsize(480, 520)
        self._build_ui()
        self._apply_item(item)

        lid = int(self._item.get("listing_id") or 0)
        if lid > 0:
            self._open[lid] = self

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        from link_bridge.window_keys import bind_q_close

        bind_q_close(self, on_close=self._close)
        self.after(50, self._mark_geo_ready)
        self._present()

    def _sync_callbacks(
        self,
        *,
        buy_listing: BuyFn,
        prefer_original_open: PreferOriginalFn | None = None,
        full_image_get: FullImageGetFn | None = None,
        full_image_set: FullImageSetFn | None = None,
        on_hide_toggle: HideToggleFn | None = None,
        get_window_geo: GeoGetFn | None = None,
        set_window_geo: GeoSetFn | None = None,
        on_log: Callable[[str], None] | None = None,
        on_bought: Callable[[], None] | None = None,
    ) -> None:
        self._buy_listing = buy_listing
        self._prefer_original = prefer_original_open or (lambda: True)
        self._full_image_get = full_image_get or (lambda: False)
        self._full_image_set = full_image_set or (lambda _v: None)
        self._on_hide_toggle = on_hide_toggle
        self._get_window_geo = get_window_geo or (lambda: "")
        self._set_window_geo = set_window_geo
        self._on_log = on_log or (lambda _s: None)
        self._on_bought = on_bought
        if getattr(self, "_full_var", None) is not None:
            try:
                self._full_var.set(bool(self._full_image_get()))
            except Exception:
                pass

    def _build_ui(self) -> None:
        if self._ui_built:
            return
        self._ui_built = True
        from link_bridge.theme import surface_for

        c = surface_for(self)
        bg = c.get("bg", "#1e1f22")
        surf = c.get("canvas", bg)
        self._bg = bg
        self.configure(bg=bg)

        img_fr = tk.Frame(self, bg=surf, bd=0, highlightthickness=0)
        img_fr.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 2))
        self._img_lbl = tk.Label(
            img_fr,
            text="Loading…",
            bg=surf,
            fg=c.get("muted", "#b5bac1"),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self._img_lbl.pack(fill=tk.BOTH, expand=True)
        self._img_lbl.bind("<Button-1>", self._open_original)

        dock = ttk.Frame(self, padding=(4, 1, 4, 2))
        dock.pack(fill=tk.X, side=tk.BOTTOM)
        row = ttk.Frame(dock)
        row.pack(fill=tk.X)
        self._meta_var = tk.StringVar(value="")
        ttk.Label(row, textvariable=self._meta_var).pack(side=tk.LEFT)
        self._full_var = tk.BooleanVar(value=bool(self._full_image_get()))
        ttk.Checkbutton(
            row,
            text="Full original",
            variable=self._full_var,
            command=self._on_full_image_toggle,
        ).pack(side=tk.LEFT, padx=(8, 0))
        self._actions_right = ttk.Frame(row)
        self._actions_right.pack(side=tk.RIGHT)
        self._flavour_lbl = ttk.Label(dock, text="", wraplength=640)
        self._links_host = tk.Frame(dock, bd=0, highlightthickness=0, bg=bg)

    def _apply_item(self, item: dict[str, Any]) -> None:
        self._item = dict(item)
        lid = int(self._item.get("listing_id") or 0)
        cid = int(self._item.get("id") or 0)
        name = (self._item.get("name") or f"#{cid}").strip()
        self.title(f"Market · #{cid} · {name}")

        price = int(self._item.get("price") or 0)
        seller = (
            (self._item.get("seller_display") or "").strip()
            or (self._item.get("seller") or "").strip().lstrip("@")
            or "?"
        )
        kind = str(self._item.get("kind") or "sell")
        meta = f"{price}🐷 · {seller}"
        if kind == "trash":
            grace = int(self._item.get("trash_grace_left") or 0)
            meta += f" · trash" + (f" ({grace}s)" if grace > 0 else "")
        elif kind == "tsell":
            meta += " · targeted sale"
        if int(self._item.get("mirror_of_id") or 0):
            meta += " · mirrored"
        self._meta_var.set(meta)

        for child in list(self._actions_right.winfo_children()):
            child.destroy()
        buyable = bool(self._item.get("buyable")) and lid > 0
        if buyable:
            ttk.Button(
                self._actions_right,
                text=f"Buy {price}🐷",
                command=self._confirm_buy,
            ).pack(side=tk.RIGHT)
        if self._on_hide_toggle is not None and lid > 0:
            from link_bridge.market_hidden import is_hidden

            hide_label = "Unhide" if is_hidden(lid) else "Hide"
            ttk.Button(
                self._actions_right,
                text=hide_label,
                command=self._toggle_hide,
            ).pack(side=tk.RIGHT, padx=(4, 0))

        flavour = (self._item.get("flavour") or "").strip()
        if flavour:
            self._flavour_lbl.configure(text=flavour)
            if not self._flavour_lbl.winfo_ismapped():
                self._flavour_lbl.pack(anchor=tk.W, pady=(1, 0))
        elif self._flavour_lbl.winfo_ismapped():
            self._flavour_lbl.pack_forget()

        for child in list(self._links_host.winfo_children()):
            child.destroy()
        link_rows = lot_link_grid_rows(self._item)
        if link_rows:
            if not self._links_host.winfo_ismapped():
                self._links_host.pack(fill=tk.X, pady=(2, 0))
            _place_link_grid(
                self._links_host,
                link_rows,
                open_url=self._open_url,
                open_urls=self._open_urls,
                bg=self._bg,
            )
        elif self._links_host.winfo_ismapped():
            self._links_host.pack_forget()

        url = self._display_url()
        if url != self._shown_url:
            self._shown_url = url
            self._load_image()

    @classmethod
    def _stash_visible(cls, *, except_lid: int | None = None) -> None:
        for lid in list(cls._open.keys()):
            if except_lid is not None and lid == except_lid:
                continue
            win = cls._open.pop(lid, None)
            if win is not None:
                cls._stash(win)

    @classmethod
    def _stash(cls, win: "MarketLotWindow") -> None:
        lid = int(win._item.get("listing_id") or 0)
        cls._open.pop(lid, None)
        try:
            win.withdraw()
        except Exception:
            pass
        if lid <= 0:
            try:
                win.destroy()
            except Exception:
                pass
            return
        cls._pool[lid] = win
        cls._pool.move_to_end(lid)
        while len(cls._pool) > _MAX_POOL:
            _, evict = cls._pool.popitem(last=False)
            try:
                if evict.winfo_exists():
                    evict.destroy()
            except Exception:
                pass

    def _present(self) -> None:
        if not self.winfo_viewable():
            self._restore_or_center()
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _mark_geo_ready(self) -> None:
        self._geo_ready = True

    def _restore_or_center(self) -> None:
        saved = (self._get_window_geo() or "").strip()
        if saved:
            try:
                self.geometry(saved)
                return
            except Exception:
                pass
        _center_window(self, 640, 720)

    def _on_configure(self, event=None) -> None:
        if event is not None and getattr(event, "widget", None) is not self:
            return
        if not self._geo_ready or self._set_window_geo is None:
            return
        if self._geo_save_after is not None:
            try:
                self.after_cancel(self._geo_save_after)
            except Exception:
                pass
        self._geo_save_after = self.after(350, self._persist_geometry)

    def _persist_geometry(self) -> None:
        self._geo_save_after = None
        if self._set_window_geo is None:
            return
        try:
            geo = self.geometry()
        except Exception:
            geo = ""
        if geo:
            self._set_window_geo(geo)

    def _on_destroy(self, _event=None) -> None:
        lid = int(self._item.get("listing_id") or 0)
        if self._open.get(lid) is self:
            self._open.pop(lid, None)
        if self._pool.get(lid) is self:
            self._pool.pop(lid, None)

    def _close(self) -> None:
        self._persist_geometry()
        self.__class__._stash(self)

    def _open_url(self, url: str) -> None:
        from link_bridge.browser_open import open_url

        u = (url or "").strip()
        if not u:
            return
        try:
            open_url(u)
        except Exception as exc:
            self._on_log(f"Open URL failed: {exc}")

    def _open_urls(self, urls: list[str]) -> None:
        for u in urls:
            self._open_url(u)

    def _display_url(self) -> str:
        return omni_display_url(
            self._item,
            full=bool(self._full_var.get()),
            prefer_original=bool(self._prefer_original()),
        )

    def _on_full_image_toggle(self) -> None:
        enabled = bool(self._full_var.get())
        try:
            self._full_image_set(enabled)
        except Exception:
            pass
        self._shown_url = None
        self._apply_item(self._item)

    def _open_original(self, _event=None) -> None:
        from link_bridge.open_image import open_full_image

        url = self._display_url()
        if not url:
            messagebox.showwarning("Market", "No image URL.", parent=self)
            return
        open_full_image(url, on_err=lambda e: self._on_log(f"Open image failed: {e}"))

    def _toggle_hide(self) -> None:
        if self._on_hide_toggle is None:
            return
        lid = int(self._item.get("listing_id") or 0)
        if lid <= 0:
            return
        from link_bridge.market_hidden import is_hidden

        self._on_hide_toggle(lid, not is_hidden(lid))
        self._close()

    def _load_image(self) -> None:
        url = self._display_url()
        if not url:
            self._img_lbl.configure(text="No preview", image="")
            self._photo = None
            return
        if self._photo is not None and url == self._shown_url:
            return
        self._img_lbl.configure(image="", text="Loading…")
        view = _VIEW * 2 if bool(self._full_var.get()) else _VIEW

        def apply(data: bytes) -> None:
            if not self.winfo_exists():
                return
            if url != self._shown_url:
                return
            try:
                photo = decode_thumb(data, view, natural=False)
                self._photo = photo
                self._img_lbl.configure(image=photo, text="")
            except Exception:
                self._img_lbl.configure(text="No preview", image="")
                self._photo = None

        schedule_thumb_fetch(url, on_data=apply, on_err=lambda _e: None)

    def _confirm_buy(self) -> None:
        if self._busy:
            return
        lid = int(self._item.get("listing_id") or 0)
        price = int(self._item.get("price") or 0)
        name = (self._item.get("name") or f"#{self._item.get('id')}").strip()
        if lid <= 0:
            return
        ok = messagebox.askyesno(
            "Buy listing",
            f"Buy “{name}” for {price}🐷?",
            parent=self,
        )
        if not ok:
            return
        self._busy = True

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "market_buy_ok":
                self._on_log(f"Market buy ok listing={lid}")
                if self._on_bought:
                    self._on_bought()
                self._close()
            else:
                err = body.get("error") or "failed"
                messagebox.showerror("Buy failed", str(err), parent=self)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            messagebox.showerror("Buy failed", str(exc), parent=self)

        self._buy_listing(lid, on_ok, on_err)


def open_market_lot(
    master: tk.Misc,
    item: dict[str, Any],
    *,
    buy_listing: BuyFn,
    prefer_original_open: PreferOriginalFn | None = None,
    full_image_get: FullImageGetFn | None = None,
    full_image_set: FullImageSetFn | None = None,
    on_hide_toggle: HideToggleFn | None = None,
    get_window_geo: GeoGetFn | None = None,
    set_window_geo: GeoSetFn | None = None,
    on_log: Callable[[str], None] | None = None,
    on_bought: Callable[[], None] | None = None,
) -> None:
    lid = int(item.get("listing_id") or 0)
    kwargs = dict(
        buy_listing=buy_listing,
        prefer_original_open=prefer_original_open,
        full_image_get=full_image_get,
        full_image_set=full_image_set,
        on_hide_toggle=on_hide_toggle,
        get_window_geo=get_window_geo,
        set_window_geo=set_window_geo,
        on_log=on_log,
        on_bought=on_bought,
    )
    MarketLotWindow._stash_visible(except_lid=lid if lid > 0 else None)

    if lid > 0 and lid in MarketLotWindow._open:
        win = MarketLotWindow._open[lid]
        if win.winfo_exists():
            win._sync_callbacks(**kwargs)
            win._apply_item(item)
            win._present()
            return

    if lid > 0 and lid in MarketLotWindow._pool:
        win = MarketLotWindow._pool.pop(lid)
        if win.winfo_exists():
            win._sync_callbacks(**kwargs)
            win._apply_item(item)
            MarketLotWindow._open[lid] = win
            win._present()
            return

    MarketLotWindow(master, item, **kwargs)
