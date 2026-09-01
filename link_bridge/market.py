"""Bridge Market tab — browse @buy listings with price filters."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk

from link_bridge.thumb_grid import (
    CELL_PAD,
    COLS,
    MIN_THUMB,
    PAGE_SIZE,
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
GeoGetFn = Callable[[], str]
GeoSetFn = Callable[[str], None]
FullImageGetFn = Callable[[], bool]
FullImageSetFn = Callable[[bool], None]
SavePricesFn = Callable[[str, str], None]

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
        scroll_speed: float = 3.0,
        grid_view: bool = True,
        min_price: str = "",
        max_price: str = "",
        save_market_prices: SavePricesFn | None = None,
        full_image_get: FullImageGetFn | None = None,
        full_image_set: FullImageSetFn | None = None,
        get_lot_window_geo: GeoGetFn | None = None,
        set_lot_window_geo: GeoSetFn | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._fetch_page = fetch_page
        self._buy_listing = buy_listing
        self._prefer_original = prefer_original_open or (lambda: True)
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.5)))
        self._scroll_speed = max(0.25, min(6.0, float(scroll_speed or 3.0)))
        self._grid_view_flag = bool(grid_view)
        self._save_market_prices = save_market_prices
        self._full_image_get = full_image_get or (lambda: False)
        self._full_image_set = full_image_set or (lambda _v: None)
        self._get_lot_window_geo = get_lot_window_geo or (lambda: "")
        self._set_lot_window_geo = set_lot_window_geo
        self._on_log = on_log or (lambda _s: None)
        init_min = (min_price or "").strip()
        init_max = (max_price or "").strip()
        self._canvas: tk.Canvas | None = None
        self._canvas_win = None
        self._grid_fr: tk.Frame | None = None
        self._gallery = None
        self._gallery_host: ttk.Frame | None = None
        self._wheel_bound = False
        self._smooth_remaining = 0.0
        self._smooth_after: str | None = None
        self._page = 0
        self._total = 0
        self._page_size = PAGE_SIZE
        self._query = ""
        self._min_price = init_min
        self._max_price = init_max
        self._price_save_after: str | None = None
        self._items: list[dict[str, Any]] = []
        self._photos: list[Any] = []
        self._thumb = 140
        self._busy = False
        self._gen = 0
        self._search_after: str | None = None
        self._resize_after: str | None = None
        # Grid view: keep separate gallery instances for normal vs show-hidden.
        self._view_slots: dict[bool, dict[str, Any]] = {}
        self._empty_msg_host: ttk.Frame | None = None

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="◀", width=3, command=self.prev_page).pack(side=tk.LEFT)
        self.prev_btn = bar.winfo_children()[-1]
        ttk.Button(bar, text="▶", width=3, command=self.next_page).pack(
            side=tk.LEFT, padx=(2, 0)
        )
        self.next_btn = bar.winfo_children()[-1]
        ttk.Button(bar, text="↻", width=3, command=self.refresh).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        self.meta_var = tk.StringVar(value="Open Market to load listings.")
        ttk.Label(bar, textvariable=self.meta_var).pack(side=tk.RIGHT, padx=(8, 0))

        filt = ttk.Frame(self)
        filt.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(filt, text="Search").pack(side=tk.LEFT)
        self.search_var = tk.StringVar(value="")
        ent = ttk.Entry(filt, textvariable=self.search_var, width=18)
        ent.pack(side=tk.LEFT, padx=(4, 0))
        self.search_var.trace_add("write", self._on_search_typed)
        ttk.Label(filt, text="Min").pack(side=tk.LEFT, padx=(8, 0))
        from link_bridge.pig_snout import pack_pig_label

        pack_pig_label(filt, "", size=16, side=tk.LEFT, padx=(2, 0))
        self.min_var = tk.StringVar(value=init_min)
        ttk.Entry(filt, textvariable=self.min_var, width=6).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Label(filt, text="Max").pack(side=tk.LEFT, padx=(6, 0))
        pack_pig_label(filt, "", size=16, side=tk.LEFT, padx=(2, 0))
        self.max_var = tk.StringVar(value=init_max)
        ttk.Entry(filt, textvariable=self.max_var, width=6).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(filt, text="Apply", command=self._apply_prices).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(filt, text="Clear", command=self._clear_filters).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        self.min_var.trace_add("write", self._on_price_typed)
        self.max_var.trace_add("write", self._on_price_typed)
        self._show_hidden = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            filt,
            text="Show hidden",
            variable=self._show_hidden,
            command=self._on_show_hidden_toggle,
        ).pack(side=tk.LEFT, padx=(10, 0))
        self._hide_mode = tk.BooleanVar(value=False)
        self._hide_mode_btn = ttk.Checkbutton(
            filt,
            text="Hide mode (LMB)",
            variable=self._hide_mode,
            command=self._sync_hide_mode_style,
        )
        self._hide_mode_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._scroll_host = ttk.Frame(self)
        self._scroll_host.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self._set_nav(False)
        self._sync_hide_mode_style()

    def set_preview_scale(self, scale: float) -> None:
        self._preview_scale = max(0.5, min(2.0, float(scale or 1.5)))
        if self._gallery is not None and hasattr(self._gallery, "set_preview_scale"):
            try:
                self._gallery.set_preview_scale(self._preview_scale)
            except Exception:
                pass
        if self._items:
            self._render_grid()

    def set_scroll_speed(self, speed: float) -> None:
        self._scroll_speed = max(0.25, min(6.0, float(speed or 3.0)))
        if self._canvas is not None:
            self._canvas.configure(yscrollincrement=1)
        if self._gallery is not None and hasattr(self._gallery, "set_scroll_speed"):
            try:
                self._gallery.set_scroll_speed(self._scroll_speed)
            except Exception:
                pass

    def _sync_hide_mode_style(self) -> None:
        try:
            if self._hide_mode.get():
                self._hide_mode_btn.configure(style="Accent.TCheckbutton")
            else:
                self._hide_mode_btn.configure(style="TCheckbutton")
        except Exception:
            pass

    def set_grid_view(self, enabled: bool) -> None:
        flag = bool(enabled)
        if flag == self._grid_view_flag:
            return
        self._grid_view_flag = flag
        self._invalidate_view_slots()
        self._destroy_square_chrome()
        self._clear_empty_message_host()
        self._render_grid()

    @staticmethod
    def _thumb_url(item: dict[str, Any]) -> str:
        return (
            (item.get("preview_url") or "").strip()
            or (item.get("image_url") or "").strip()
        )

    def _persist_prices(self) -> None:
        min_p = (self.min_var.get() or "").strip()
        max_p = (self.max_var.get() or "").strip()
        self._min_price = min_p
        self._max_price = max_p
        if self._save_market_prices is not None:
            try:
                self._save_market_prices(min_p, max_p)
            except Exception:
                pass

    def _on_price_typed(self, *_args) -> None:
        if self._price_save_after is not None:
            try:
                self.after_cancel(self._price_save_after)
            except Exception:
                pass
        self._price_save_after = self.after(SEARCH_DEBOUNCE_MS, self._debounced_save_prices)

    def _debounced_save_prices(self) -> None:
        self._price_save_after = None
        self._persist_prices()

    def _destroy_gallery(self) -> None:
        """Drop the active gallery pointer (slot hosts may still exist)."""
        self._gallery = None
        self._gallery_host = None

    def _filter_mode(self) -> bool:
        return bool(self._show_hidden.get())

    def _page_sig(
        self,
        items: list[dict[str, Any]] | None = None,
        *,
        hidden: bool | None = None,
    ) -> tuple[Any, ...]:
        mode = self._filter_mode() if hidden is None else bool(hidden)
        visible = (
            items
            if items is not None
            else self._visible_items_for_mode(mode)
        )
        return (
            int(self._page),
            (self._query or "").strip().lower(),
            (self._min_price or "").strip(),
            (self._max_price or "").strip(),
            bool(mode),
            tuple(int(it.get("listing_id") or 0) for it in visible),
        )

    def _visible_items_for_mode(self, hidden: bool) -> list[dict[str, Any]]:
        from link_bridge.market_hidden import is_hidden

        items = list(self._items)
        if hidden:
            return [
                it
                for it in items
                if is_hidden(int(it.get("listing_id") or 0))
            ]
        return [
            it
            for it in items
            if not is_hidden(int(it.get("listing_id") or 0))
        ]

    def _invalidate_view_slots(self) -> None:
        for slot in self._view_slots.values():
            try:
                g = slot.get("gallery")
                if g is not None:
                    g.destroy()
            except Exception:
                pass
            try:
                h = slot.get("host")
                if h is not None:
                    h.destroy()
            except Exception:
                pass
        self._view_slots.clear()
        self._destroy_gallery()

    def _pack_all_slots_forget(self) -> None:
        for slot in self._view_slots.values():
            host = slot.get("host")
            if host is None:
                continue
            try:
                host.pack_forget()
            except Exception:
                pass
        self._destroy_gallery()

    def _clear_empty_message_host(self) -> None:
        host = self._empty_msg_host
        self._empty_msg_host = None
        if host is None:
            return
        try:
            host.destroy()
        except Exception:
            pass

    def _show_empty_message(self, msg: str) -> None:
        self._clear_empty_message_host()
        self._pack_all_slots_forget()
        self._empty_msg_host = ttk.Frame(self._scroll_host)
        self._empty_msg_host.pack(fill=tk.BOTH, expand=True)
        ttk.Label(self._empty_msg_host, text=msg).pack(pady=12)

    def _save_view_slot(self, mode: bool) -> None:
        if not self._grid_view_flag or self._gallery is None or self._gallery_host is None:
            return
        self._view_slots[mode] = {
            "host": self._gallery_host,
            "gallery": self._gallery,
            "sig": self._page_sig(hidden=mode),
            "yfrac": self._gallery_yview(),
        }
        try:
            self._gallery_host.pack_forget()
        except Exception:
            pass
        self._destroy_gallery()

    def _activate_view_slot(self, mode: bool) -> None:
        slot = self._view_slots.get(mode) or {}
        host = slot.get("host")
        gallery = slot.get("gallery")
        if host is None or gallery is None:
            return
        self._clear_empty_message_host()
        self._pack_all_slots_forget()
        host.pack(fill=tk.BOTH, expand=True)
        self._gallery_host = host
        self._gallery = gallery

    def _restore_view_slot(self, mode: bool) -> bool:
        if not self._grid_view_flag:
            return False
        slot = self._view_slots.get(mode)
        if not slot or slot.get("gallery") is None:
            return False
        if slot.get("sig") != self._page_sig(hidden=mode):
            return False
        self._activate_view_slot(mode)
        yfrac = float(slot.get("yfrac") or 0.0)
        if yfrac > 0.0:
            self.after(30, lambda y=yfrac: self._restore_gallery_yview(y))
        self._claim_wheel()
        return True

    def _remember_view_slot(self, mode: bool, yfrac: float) -> None:
        if self._gallery is None or self._gallery_host is None:
            return
        self._view_slots[mode] = {
            "host": self._gallery_host,
            "gallery": self._gallery,
            "sig": self._page_sig(hidden=mode),
            "yfrac": float(yfrac or 0.0),
        }

    def _destroy_square_chrome(self) -> None:
        if self._canvas is not None:
            try:
                self._canvas.destroy()
            except Exception:
                pass
        self._canvas = None
        self._canvas_win = None
        self._grid_fr = None
        self._wheel_bound = False

    def _ensure_gallery(self, *, mode: bool | None = None):
        hidden = self._filter_mode() if mode is None else bool(mode)
        slot = self._view_slots.get(hidden)
        if slot and slot.get("gallery") is not None:
            self._activate_view_slot(hidden)
            return slot["gallery"]
        self._destroy_square_chrome()
        self._clear_empty_message_host()
        self._pack_all_slots_forget()
        self._gallery_host = ttk.Frame(self._scroll_host)
        self._gallery_host.pack(fill=tk.BOTH, expand=True)
        from link_bridge.gallery import JustifiedGallery

        self._gallery = JustifiedGallery(
            self._gallery_host,
            photos=self._photos,
            bind_thumb=self._gallery_bind_thumb,
            gen_fn=lambda: self._gen,
            preview_scale=self._preview_scale,
            scroll_speed=self._scroll_speed,
        )
        self._view_slots[hidden] = {
            "host": self._gallery_host,
            "gallery": self._gallery,
            "sig": (),
            "yfrac": 0.0,
        }
        return self._gallery

    def _ensure_scroll_chrome(self) -> tk.Frame:
        if self._grid_fr is not None:
            return self._grid_fr
        from link_bridge.theme import surface_for

        c = surface_for(self)
        surf = c.get("canvas", "#1e1f22")
        bg = c.get("bg", surf)
        self._canvas = tk.Canvas(
            self._scroll_host,
            highlightthickness=0,
            bd=0,
            bg=surf,
            highlightbackground=surf,
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.configure(yscrollincrement=1)
        self._grid_fr = tk.Frame(self._canvas, bg=bg, bd=0, highlightthickness=0)
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._grid_fr, anchor=tk.NW
        )
        for cidx in range(COLS):
            self._grid_fr.columnconfigure(cidx, weight=1, uniform="mcol")

        def _on_inner(_event=None) -> None:
            self._update_scrollregion()

        def _on_canvas(event) -> None:
            if self._canvas is None or self._canvas_win is None:
                return
            self._canvas.itemconfigure(self._canvas_win, width=event.width)
            self._on_grid_resize()

        self._grid_fr.bind("<Configure>", _on_inner)
        self._canvas.bind("<Configure>", _on_canvas)
        self._canvas.bind("<Enter>", self._claim_wheel)
        self._grid_fr.bind("<Enter>", self._claim_wheel)
        try:
            from link_bridge import gallery as _gal

            if not self._wheel_bound:
                self._canvas.bind_all("<MouseWheel>", _gal._gallery_wheel)
                self._wheel_bound = True
        except Exception:
            pass
        return self._grid_fr

    def _claim_wheel(self, _event=None) -> None:
        if self._gallery is not None:
            try:
                self._gallery._claim_wheel()
            except Exception:
                pass
            return
        try:
            from link_bridge import gallery as _gal

            _gal._wheel_target = self  # type: ignore[assignment]
        except Exception:
            pass

    def _queue_smooth_scroll(self, px: float) -> None:
        if self._canvas is None or abs(px) < 0.01:
            return
        self._smooth_remaining += float(px)
        if self._smooth_after is None:
            self._tick_smooth_scroll()

    def _tick_smooth_scroll(self) -> None:
        from link_bridge.gallery import _SMOOTH_EASE, _SMOOTH_FRAME_MS

        self._smooth_after = None
        if self._canvas is None:
            self._smooth_remaining = 0.0
            return
        rem = self._smooth_remaining
        if abs(rem) < 0.8:
            if abs(rem) >= 0.2:
                self._canvas.yview_scroll(1 if rem > 0 else -1, "units")
            self._smooth_remaining = 0.0
            return
        step = rem * _SMOOTH_EASE
        if abs(step) < 1.0:
            step = 1.0 if rem > 0 else -1.0
        moved = int(round(step))
        if moved == 0:
            self._smooth_remaining = 0.0
            return
        self._smooth_remaining -= moved
        self._canvas.yview_scroll(moved, "units")
        self._smooth_after = self._canvas.after(_SMOOTH_FRAME_MS, self._tick_smooth_scroll)

    def _update_scrollregion(self) -> None:
        if self._canvas is None or self._grid_fr is None:
            return
        self._canvas.update_idletasks()
        w = max(1, self._canvas.winfo_width())
        h = max(1, self._grid_fr.winfo_reqheight())
        self._canvas.configure(scrollregion=(0, 0, w, h))

    def _compute_thumb(self, width: int) -> int:
        if width < 80:
            return 140
        cell_w = max(1, width // COLS)
        return max(MIN_THUMB, cell_w - CELL_PAD * 2)

    def _gallery_yview(self) -> float:
        g = self._gallery
        if g is None or getattr(g, "_canvas", None) is None:
            return 0.0
        try:
            return float(g._canvas.yview()[0])
        except Exception:
            return 0.0

    def _restore_gallery_yview(self, yfrac: float) -> None:
        g = self._gallery
        if g is None or getattr(g, "_canvas", None) is None or yfrac <= 0.0:
            return
        try:
            g._canvas.yview_moveto(yfrac)
        except Exception:
            pass

    def _on_show_hidden_toggle(self) -> None:
        if not self._grid_view_flag:
            self._render_grid()
            return
        new_mode = self._filter_mode()
        old_mode = not new_mode
        self._save_view_slot(old_mode)
        if self._restore_view_slot(new_mode):
            return
        self._render_grid(preserve_scroll=True)

    def _toggle_hide_lot(self, listing_id: int, *, event=None) -> None:
        from link_bridge.market_hidden import is_hidden, toggle_hidden

        try:
            if event is not None:
                event.widget.focus_set()
        except Exception:
            pass
        lid = int(listing_id)
        was_hidden = is_hidden(lid)
        now_hidden = toggle_hidden(lid)
        if (
            not self._show_hidden.get()
            and self._grid_view_flag
            and self._gallery is not None
            and now_hidden
            and not was_hidden
        ):
            item = self._item_by_listing_id(lid)
            if item is not None:
                cid = int(item.get("id") or 0)
                if cid > 0 and self._gallery.remove_char(cid):
                    self._note_gallery_char_removed()
                    self._on_log(f"Market lot {lid} hidden")
                    return
        self._render_grid(preserve_scroll=True)
        state = "hidden" if now_hidden else "visible"
        self._on_log(f"Market lot {lid} {state}")

    def _visible_items(self) -> list[dict[str, Any]]:
        return self._visible_items_for_mode(self._filter_mode())

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
        self._persist_prices()
        self.load_page(0)

    def _clear_filters(self) -> None:
        self.search_var.set("")
        self.min_var.set("")
        self.max_var.set("")
        self._query = ""
        self._min_price = ""
        self._max_price = ""
        self._persist_prices()
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
        if not self._items or self._grid_view_flag:
            return
        host = self._canvas or self._grid_fr
        if host is None:
            return
        w = max(1, host.winfo_width())
        new = self._compute_thumb(w)
        if abs(new - self._thumb) < 8:
            return
        self._thumb = new
        self._render_grid(preserve_scroll=True)

    def has_cached_view(self) -> bool:
        return bool(self._items)

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
            self._invalidate_view_slots()
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
            if not self._grid_view_flag:
                host = self._canvas or self._grid_fr
                if host is not None:
                    self._thumb = self._compute_thumb(max(1, host.winfo_width()))
            if self._canvas is not None:
                self._canvas.yview_moveto(0)
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

    def _clear_square_grid(self) -> None:
        if self._grid_fr is None:
            return
        for child in list(self._grid_fr.winfo_children()):
            child.destroy()
        release_photos(self._photos)

    def _clear_scroll_host(self) -> None:
        self._invalidate_view_slots()
        self._destroy_square_chrome()
        self._clear_empty_message_host()
        for child in list(self._scroll_host.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

    def _render_grid(self, *, preserve_scroll: bool = False) -> None:
        items = self._visible_items()
        if self._grid_view_flag:
            self._render_gallery(items, preserve_scroll=preserve_scroll)
        else:
            self._render_square(items, preserve_scroll=preserve_scroll)

    def _note_gallery_char_removed(self) -> None:
        mode = self._filter_mode()
        slot = self._view_slots.get(mode)
        if slot is not None:
            slot["sig"] = self._page_sig(hidden=mode)

    def _render_gallery(
        self, items: list[dict[str, Any]], *, preserve_scroll: bool = False
    ) -> None:
        mode = self._filter_mode()
        yfrac = self._gallery_yview() if preserve_scroll else 0.0
        if not self._items:
            self._clear_scroll_host()
            self._show_empty_message("No listings match.")
            return
        if not items:
            self._clear_empty_message_host()
            self._pack_all_slots_forget()
            if self._show_hidden.get():
                msg = "No hidden lots on this page."
            elif self._items:
                msg = "All listings on this page are hidden.\nEnable “Show hidden”."
            else:
                msg = "No listings match."
            self._show_empty_message(msg)
            return

        sig = self._page_sig(items, hidden=mode)
        slot = self._view_slots.get(mode)
        if (
            slot
            and slot.get("gallery") is not None
            and slot.get("sig") == sig
        ):
            self._activate_view_slot(mode)
            y = float(slot.get("yfrac") or yfrac)
            if preserve_scroll and y > 0.0:
                self.after(30, lambda yy=y: self._restore_gallery_yview(yy))
            self._claim_wheel()
            return

        self._clear_empty_message_host()
        gallery = self._ensure_gallery(mode=mode)
        for it in items:
            url = self._thumb_url(it)
            if url:
                it["preview_url"] = url
            elif not (it.get("preview_url") or "").strip():
                it["preview_url"] = (it.get("image_url") or "").strip()
        gallery.render(items)
        store_y = yfrac if preserve_scroll else 0.0
        self._remember_view_slot(mode, store_y)
        if preserve_scroll and yfrac > 0.0:
            self.after(30, lambda y=yfrac: self._restore_gallery_yview(y))
        else:
            self._claim_wheel()

    def _render_square(
        self, items: list[dict[str, Any]], *, preserve_scroll: bool = False
    ) -> None:
        yfrac = 0.0
        if preserve_scroll and self._canvas is not None:
            try:
                yfrac = float(self._canvas.yview()[0])
            except Exception:
                yfrac = 0.0
        self._invalidate_view_slots()
        self._destroy_square_chrome()
        self._clear_empty_message_host()
        for child in list(self._scroll_host.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        grid_fr = self._ensure_scroll_chrome()
        self._clear_square_grid()
        if not self._items:
            ttk.Label(grid_fr, text="No listings match.").grid(
                row=0, column=0, sticky="nsew"
            )
            self._update_scrollregion()
            return
        if not items:
            if self._show_hidden.get():
                msg = "No hidden lots on this page."
            elif self._items:
                msg = "All listings on this page are hidden.\nEnable “Show hidden”."
            else:
                msg = "No listings match."
            ttk.Label(grid_fr, text=msg).grid(row=0, column=0, sticky="nsew")
            self._update_scrollregion()
            return
        from link_bridge.theme import surface_for

        surf = surface_for(self).get("canvas", "#1e1f22")
        muted = surface_for(self).get("muted", "#b5bac1")
        for cidx in range(COLS):
            grid_fr.columnconfigure(cidx, weight=1, uniform="mcol")
        rows = max(1, (len(items) + COLS - 1) // COLS) if items else 1
        for ridx in range(rows):
            grid_fr.rowconfigure(ridx, weight=0)
        thumb = self._thumb
        for i, item in enumerate(items):
            r, ccol = divmod(i, COLS)
            cell = ttk.Frame(grid_fr)
            cell.grid(row=r, column=ccol, sticky="nsew", padx=2, pady=2)
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
            self._bind_thumb(thumb_lbl, item)
            url = self._thumb_url(item).strip()
            if url:
                self._load_thumb(thumb_lbl, url, self._gen, item=item)
        self._update_scrollregion()
        if preserve_scroll and yfrac > 0 and self._canvas is not None:
            self.after(30, lambda y=yfrac: self._canvas.yview_moveto(y))

    def _load_thumb(
        self,
        label: tk.Label,
        url: str,
        gen: int,
        *,
        item: dict[str, Any],
    ) -> None:
        thumb = self._thumb

        def apply_bytes(data: bytes) -> None:
            if gen != self._gen or not label.winfo_exists():
                return
            try:
                photo = decode_thumb(data, thumb, natural=False)
                self._photos.append(photo)
                label.configure(image=photo, text="")
                self._bind_thumb(label, item)
            except Exception:
                label.configure(text="no preview")

        def on_fail(_exc: BaseException) -> None:
            if gen != self._gen or not label.winfo_exists():
                return
            label.configure(text="no preview")

        schedule_thumb_fetch(url, on_data=apply_bytes, on_err=on_fail)

    def _gallery_bind_thumb(
        self, label: tk.Label, char_id: int, post_url: str
    ) -> None:
        item = self._item_by_id(char_id)
        if item is None:
            return
        self._bind_thumb(label, item)

    def _bind_thumb(self, label: tk.Label, item: dict[str, Any]) -> None:
        it = dict(item)
        label.bind("<Button-1>", lambda _e, row=it: self._on_primary_click(row))
        label.bind("<Button-2>", lambda _e, row=it: self._click_post(row))
        label.bind("<Button-3>", lambda e, row=it: self._popup_thumb_menu(e, row))
        post_url = (item.get("post_url") or "").strip()
        if post_url:
            label.bind(
                "<Control-Button-1>",
                lambda _e, u=post_url: self._open_post(u),
            )

    def _click_post(self, item: dict[str, Any]) -> None:
        post_url = (item.get("post_url") or "").strip()
        if post_url:
            self._open_post(post_url)

    def _on_primary_click(self, item: dict[str, Any]) -> None:
        if self._hide_mode.get():
            lid = int(item.get("listing_id") or 0)
            if lid > 0:
                self._toggle_hide_lot(lid)
            return
        self._open_lot_window(item)

    def _popup_thumb_menu(self, event, item: dict[str, Any]) -> None:
        menu = tk.Menu(self, tearoff=0)
        name = (item.get("name") or f"#{item.get('id')}").strip()
        lid = int(item.get("listing_id") or 0)
        price = int(item.get("price") or 0)
        buyable = bool(item.get("buyable")) and lid > 0
        menu.add_command(
            label="Open lot…",
            command=lambda: self._open_lot_window(item),
        )
        if buyable:
            buy_label = f"Buy for {price}🐷"
            menu.add_command(
                label=buy_label if len(buy_label) <= 40 else buy_label[:37] + "…",
                command=lambda: self._confirm_buy(lid, price, name),
            )
        if lid > 0:
            from link_bridge.market_hidden import is_hidden

            if is_hidden(lid):
                menu.add_command(
                    label="Unhide lot",
                    command=lambda: self._set_lot_hidden(lid, False),
                )
            else:
                menu.add_command(
                    label="Hide lot",
                    command=lambda: self._set_lot_hidden(lid, True),
                )
        from link_bridge.market_links import browser_link_specs

        for label, url in browser_link_specs(item):
            menu.add_command(label=f"Open {label}", command=lambda u=url: self._open_post(u))
        artist_urls = []
        try:
            from link_bridge.market_links import artist_open_urls

            artist_urls = artist_open_urls(item)
        except Exception:
            pass
        if len(artist_urls) > 1:
            menu.add_command(
                label="Open author tabs (solo + −solo)",
                command=lambda us=artist_urls: self._open_urls(us),
            )
        try:
            menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _open_lot_window(self, item: dict[str, Any]) -> None:
        from link_bridge.market_lot import open_market_lot

        open_market_lot(
            self.winfo_toplevel(),
            item,
            buy_listing=self._buy_listing,
            prefer_original_open=self._prefer_original,
            full_image_get=self._full_image_get,
            full_image_set=self._full_image_set,
            on_hide_toggle=self._set_lot_hidden,
            get_window_geo=self._get_lot_window_geo,
            set_window_geo=self._set_lot_window_geo,
            on_log=self._on_log,
            on_bought=lambda: self.load_page(self._page),
        )

    def _set_lot_hidden(self, listing_id: int, hidden: bool) -> None:
        from link_bridge.market_hidden import is_hidden, set_hidden

        lid = int(listing_id)
        if bool(hidden) == is_hidden(lid):
            return
        set_hidden(lid, bool(hidden))
        if (
            hidden
            and not self._show_hidden.get()
            and self._grid_view_flag
            and self._gallery is not None
        ):
            item = self._item_by_listing_id(lid)
            if item is not None:
                cid = int(item.get("id") or 0)
                if cid > 0 and self._gallery.remove_char(cid):
                    self._note_gallery_char_removed()
                    self._on_log(f"Market lot {lid} hidden")
                    return
        self._render_grid(preserve_scroll=True)

    def _item_by_id(self, char_id: int) -> dict[str, Any] | None:
        for it in self._items:
            try:
                if int(it.get("id") or 0) == int(char_id):
                    return it
            except Exception:
                continue
        return None

    def _item_by_listing_id(self, listing_id: int) -> dict[str, Any] | None:
        for it in self._items:
            try:
                if int(it.get("listing_id") or 0) == int(listing_id):
                    return it
            except Exception:
                continue
        return None

    def _open_post(self, post_url: str) -> None:
        from link_bridge.browser_open import open_url

        url = (post_url or "").strip()
        if not url:
            return
        try:
            open_url(url)
        except Exception as exc:
            self._on_log(f"Open post failed: {exc}")

    def _open_urls(self, urls: list[str]) -> None:
        from link_bridge.browser_open import open_url

        for raw in urls:
            url = (raw or "").strip()
            if not url:
                continue
            try:
                open_url(url)
            except Exception as exc:
                self._on_log(f"Open URL failed: {exc}")

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
