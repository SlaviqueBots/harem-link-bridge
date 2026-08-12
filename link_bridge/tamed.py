"""Tamed cards — before/after paired gallery with paging."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import ttk

from link_bridge.thumb_grid import (
    PAGE_SIZE,
    cache_get,
    decode_thumb_sized,
    release_photos,
    schedule_thumb_fetch,
)

logger = logging.getLogger(__name__)

# Fewer pairs per page — each cell loads two images.
TAMED_PAGE_SIZE = max(24, PAGE_SIZE // 2)
PAIR_PAD = 10
PAIR_INNER_GAP = 6
LABEL_H = 18
HEADER_H = 22

OkCb = Callable[[dict[str, Any]], None]
ErrCb = Callable[[BaseException], None]
FetchTamedFn = Callable[[int, str, OkCb, ErrCb], None]
PostGridFn = Callable[[int, OkCb, ErrCb], None]
OpenOmniFn = Callable[[int, OkCb, ErrCb], None]
RegisterCupFn = Callable[[int, OkCb, ErrCb], None]
DmCraftFn = Callable[[int, str, OkCb, ErrCb], None]
FocusPrefFn = Callable[[], bool]
TargetGetFn = Callable[[], str]
TargetSetFn = Callable[[str], None]


class NumberedPairBoard:
    """Scrollable columns of numbered before|after pair cards."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        photos: list[Any],
        bind_pair: Callable[..., None],
        gen_fn: Callable[[], int],
        preview_scale: float = 1.0,
        page_base: int = 0,
    ) -> None:
        self._parent = parent
        self._photos = photos
        self._bind_pair = bind_pair
        self._gen_fn = gen_fn
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.0)))
        self._page_base = max(0, int(page_base))
        self._entries: list[dict[str, Any]] = []
        self._canvas: tk.Canvas | None = None
        self._inner: tk.Frame | None = None
        self._sb: ttk.Scrollbar | None = None
        self._win = None
        self._layout_after: str | None = None
        self._wheel_bound = False
        self._last_w = 0

    def set_preview_scale(self, scale: float) -> None:
        self._preview_scale = max(0.5, min(2.0, float(scale or 1.0)))
        self._schedule_layout()

    def destroy(self) -> None:
        self._cancel_layout()
        try:
            from link_bridge import gallery as _gal

            if getattr(_gal, "_wheel_target", None) is self:
                _gal._wheel_target = None
        except Exception:
            pass
        self._wheel_bound = False
        self._entries.clear()
        if self._canvas is not None:
            try:
                self._canvas.destroy()
            except Exception:
                pass
        if self._sb is not None:
            try:
                self._sb.destroy()
            except Exception:
                pass
        self._canvas = None
        self._inner = None
        self._sb = None
        self._win = None

    def _cancel_layout(self) -> None:
        if self._layout_after is not None and self._canvas is not None:
            try:
                self._canvas.after_cancel(self._layout_after)
            except Exception:
                pass
        self._layout_after = None

    def _schedule_layout(self) -> None:
        self._cancel_layout()
        if self._canvas is None:
            return
        self._layout_after = self._canvas.after(40, self._layout)

    def _tile_size(self) -> int:
        # Square tile side; aspect preserved via contain decode.
        return max(72, int(round(110 * self._preview_scale)))

    def _ensure_chrome(self) -> tk.Frame:
        if self._canvas is not None and self._inner is not None:
            return self._inner
        for child in list(self._parent.winfo_children()):
            child.destroy()
        self._sb = ttk.Scrollbar(self._parent, orient=tk.VERTICAL)
        self._canvas = tk.Canvas(self._parent, highlightthickness=0, bd=0)
        self._sb.configure(command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._sb.set)
        self._sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._inner = tk.Frame(self._canvas)
        self._win = self._canvas.create_window((0, 0), window=self._inner, anchor=tk.NW)

        def _on_inner(_event=None) -> None:
            if self._canvas is None:
                return
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))

        def _on_canvas(event) -> None:
            if self._canvas is None or self._win is None:
                return
            self._canvas.itemconfigure(self._win, width=event.width)
            if abs(int(event.width) - self._last_w) < 4:
                return
            self._schedule_layout()

        self._inner.bind("<Configure>", _on_inner)
        self._canvas.bind("<Configure>", _on_canvas)

        try:
            from link_bridge import gallery as _gal

            _gal._wheel_target = self  # type: ignore[assignment]
            if not self._wheel_bound:
                self._canvas.bind_all("<MouseWheel>", _gal._gallery_wheel)
                self._wheel_bound = True
        except Exception:
            pass
        return self._inner

    def render(self, items: list[dict[str, Any]], *, page_base: int = 0) -> None:
        self.destroy()
        self._page_base = max(0, int(page_base))
        inner = self._ensure_chrome()
        gen = self._gen_fn()
        self._entries = []
        for i, item in enumerate(items):
            cid = int(item.get("id") or 0)
            post_url = (item.get("post_url") or "").strip()
            name = (item.get("name") or "").strip() or f"#{cid}"
            num = self._page_base + i + 1
            before_url = (
                (item.get("before_preview_url") or "").strip()
                or (item.get("preview_url") or "").strip()
            )
            after_url = (
                (item.get("after_preview_url") or "").strip()
                or (item.get("preview_url") or "").strip()
            )

            card = tk.Frame(inner, bd=1, relief=tk.GROOVE, padx=4, pady=4)
            head = ttk.Label(card, text=f"#{num}  ·  {name}", anchor=tk.W)
            head.pack(fill=tk.X)

            pics = tk.Frame(card)
            pics.pack(fill=tk.X, pady=(4, 0))

            before_col = tk.Frame(pics)
            after_col = tk.Frame(pics)
            before_col.pack(side=tk.LEFT, padx=(0, PAIR_INNER_GAP // 2))
            after_col.pack(side=tk.LEFT, padx=(PAIR_INNER_GAP // 2, 0))

            ttk.Label(before_col, text="Before", anchor=tk.CENTER).pack(fill=tk.X)
            ttk.Label(after_col, text="After", anchor=tk.CENTER).pack(fill=tk.X)

            before_lbl = tk.Label(
                before_col, text="…", relief=tk.FLAT, cursor="hand2", bd=0, bg="#2a2a2a"
            )
            after_lbl = tk.Label(
                after_col, text="…", relief=tk.FLAT, cursor="hand2", bd=0, bg="#2a2a2a"
            )
            before_lbl.pack()
            after_lbl.pack()

            self._bind_pair(
                before_lbl,
                after_lbl,
                cid,
                post_url,
                extras=(card, head),
            )

            entry = {
                "card": card,
                "before_lbl": before_lbl,
                "after_lbl": after_lbl,
                "before_url": before_url,
                "after_url": after_url,
                "before_data": None,
                "after_data": None,
                "before_photo": None,
                "after_photo": None,
                "char_id": cid,
                "post_url": post_url,
            }
            self._entries.append(entry)
            if before_url:
                self._fetch_side(entry, "before", gen)
            else:
                before_lbl.configure(text="?")
            if after_url:
                self._fetch_side(entry, "after", gen)
            else:
                after_lbl.configure(text="?")

        self._schedule_layout()

    def _fetch_side(self, entry: dict[str, Any], side: str, gen: int) -> None:
        url = entry[f"{side}_url"]
        lbl: tk.Label = entry[f"{side}_lbl"]

        def apply(data: bytes) -> None:
            if gen != self._gen_fn() or not lbl.winfo_exists():
                return
            entry[f"{side}_data"] = data
            tile = self._tile_size()
            try:
                photo = decode_thumb_sized(data, tile, tile)
                old = entry.get(f"{side}_photo")
                entry[f"{side}_photo"] = photo
                self._photos.append(photo)
                lbl.configure(image=photo, text="", width=tile, height=tile)
                if old is not None:
                    try:
                        if old in self._photos:
                            self._photos.remove(old)
                        release_photos([old])
                    except Exception:
                        pass
            except Exception:
                lbl.configure(text="×")

        def on_data(data: bytes) -> None:
            try:
                lbl.after(0, lambda d=data: apply(d))
            except Exception:
                pass

        def on_err(_exc: BaseException) -> None:
            try:
                lbl.after(
                    0,
                    lambda: lbl.configure(text="×")
                    if gen == self._gen_fn() and lbl.winfo_exists()
                    else None,
                )
            except Exception:
                pass

        cached = cache_get(url)
        if cached is not None:
            apply(cached)
            return
        schedule_thumb_fetch(url, on_data=on_data, on_err=on_err)

    def _layout(self) -> None:
        self._layout_after = None
        if self._canvas is None or self._inner is None:
            return
        view_w = max(120, self._canvas.winfo_width())
        self._last_w = view_w
        tile = self._tile_size()
        # Pair card width: two square tiles + gap + padding + border.
        pair_w = tile * 2 + PAIR_INNER_GAP + 16
        pair_h = HEADER_H + LABEL_H + tile + 16
        cols = max(1, view_w // (pair_w + PAIR_PAD))
        # Stretch tile slightly if leftover space is large.
        usable = max(pair_w, (view_w - PAIR_PAD * (cols + 1)) // cols)
        if usable > pair_w + 8:
            # Grow tiles to use column width better while staying square-ish.
            extra = usable - (PAIR_INNER_GAP + 16)
            tile = max(tile, extra // 2)
            pair_w = tile * 2 + PAIR_INNER_GAP + 16
            pair_h = HEADER_H + LABEL_H + tile + 16

        for entry in self._entries:
            for side in ("before", "after"):
                data = entry.get(f"{side}_data")
                lbl: tk.Label = entry[f"{side}_lbl"]
                if data is None or not lbl.winfo_exists():
                    lbl.configure(width=tile, height=tile)
                    continue
                try:
                    photo = decode_thumb_sized(data, tile, tile)
                    old = entry.get(f"{side}_photo")
                    entry[f"{side}_photo"] = photo
                    self._photos.append(photo)
                    lbl.configure(image=photo, text="", width=tile, height=tile)
                    if old is not None:
                        try:
                            if old in self._photos:
                                self._photos.remove(old)
                            release_photos([old])
                        except Exception:
                            pass
                except Exception:
                    lbl.configure(width=tile, height=tile)

        rows = (len(self._entries) + cols - 1) // cols if self._entries else 0
        total_h = max(pair_h, rows * (pair_h + PAIR_PAD) + PAIR_PAD)
        self._inner.configure(width=view_w, height=total_h)

        for i, entry in enumerate(self._entries):
            r, c = divmod(i, cols)
            # Column-major numbering feel: still left-to-right reading order,
            # but each card is a clear numbered unit.
            x = PAIR_PAD + c * (pair_w + PAIR_PAD)
            y = PAIR_PAD + r * (pair_h + PAIR_PAD)
            entry["card"].place(x=x, y=y, width=pair_w, height=pair_h)

        self._canvas.configure(scrollregion=(0, 0, view_w, max(total_h, 1)))


class TamedPanel(ttk.Frame):
    """Paged before|after pairs for tamed cards."""

    def __init__(
        self,
        master,
        *,
        fetch_tamed: FetchTamedFn,
        post_grid: PostGridFn,
        open_omni: OpenOmniFn | None = None,
        register_cup: RegisterCupFn | None = None,
        dm_craft: DmCraftFn | None = None,
        should_focus_telegram: FocusPrefFn | None = None,
        get_post_target: TargetGetFn | None = None,
        set_post_target: TargetSetFn | None = None,
        preview_scale: float = 1.5,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._fetch_tamed = fetch_tamed
        self._post_grid = post_grid
        self._open_omni = open_omni
        self._register_cup = register_cup
        self._dm_craft = dm_craft
        self._should_focus = should_focus_telegram or (lambda: False)
        self._get_post_target = get_post_target or (lambda: "group")
        self._set_post_target = set_post_target
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.5)))
        self._on_log = on_log or (lambda _s: None)
        self._page = 0
        self._total = 0
        self._page_size = TAMED_PAGE_SIZE
        self._query = ""
        self._items: list[dict[str, Any]] = []
        self._photos: list[Any] = []
        self._busy = False
        self._gen = 0
        self._gallery = None
        self._search_after: str | None = None

        search_row = ttk.Frame(self)
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

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, pady=(6, 0))
        self.prev_btn = ttk.Button(bar, text="◀ Prev", command=self.prev_page)
        self.prev_btn.pack(side=tk.LEFT)
        self.next_btn = ttk.Button(bar, text="Next ▶", command=self.next_page)
        self.next_btn.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        self._target_btn = ttk.Button(
            bar, text=self._target_label(), command=self._toggle_target
        )
        self._target_btn.pack(side=tk.LEFT, padx=(12, 0))
        self.meta_var = tk.StringVar(value="Connect to load tamed cards.")
        ttk.Label(bar, textvariable=self.meta_var).pack(side=tk.LEFT, padx=(12, 0))

        self.grid_fr = ttk.Frame(self)
        self.grid_fr.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self._set_nav(False)

    def _target_label(self) -> str:
        t = (self._get_post_target() or "group").strip().lower()
        return "Middle-click → DM" if t == "dm" else "Middle-click → Group"

    def _toggle_target(self) -> None:
        cur = (self._get_post_target() or "group").strip().lower()
        nxt = "dm" if cur != "dm" else "group"
        if self._set_post_target is not None:
            self._set_post_target(nxt)
        self.sync_target_button()

    def sync_target_button(self) -> None:
        try:
            self._target_btn.configure(text=self._target_label())
        except Exception:
            pass

    def set_preview_scale(self, scale: float) -> None:
        self._preview_scale = max(0.5, min(2.0, float(scale or 1.5)))
        if self._gallery is not None:
            try:
                self._gallery.set_preview_scale(self._preview_scale)
            except Exception:
                pass

    def clear(self) -> None:
        self._items = []
        self._total = 0
        self._page = 0
        if self._gallery is not None:
            try:
                self._gallery.destroy()
            except Exception:
                pass
            self._gallery = None
        release_photos(self._photos)
        self._photos = []
        self.meta_var.set("Connect to load tamed cards.")
        self._set_nav(False)

    def _on_search_typed(self, *_args) -> None:
        if self._search_after is not None:
            try:
                self.after_cancel(self._search_after)
            except Exception:
                pass
        self._search_after = self.after(400, self._search_now)

    def _search_now(self) -> None:
        if self._search_after is not None:
            try:
                self.after_cancel(self._search_after)
            except Exception:
                pass
            self._search_after = None
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
        self._busy = True
        self._gen += 1
        gen = self._gen
        self._page = max(0, int(page))
        self.meta_var.set("Loading tamed…")
        self._set_nav(False)

        def on_ok(body: dict) -> None:
            if gen != self._gen:
                return
            self._busy = False
            if body.get("op") != "roster_page_ok":
                self.meta_var.set(f"Tamed failed: {body.get('error') or 'failed'}")
                self._set_nav(True)
                return
            self._items = list(body.get("items") or [])
            try:
                self._total = int(body.get("total") or 0)
            except Exception:
                self._total = len(self._items)
            try:
                self._page = int(body.get("page") or self._page)
            except Exception:
                pass
            pages = max(1, (self._total + self._page_size - 1) // self._page_size)
            qbit = f" · “{self._query}”" if self._query else ""
            self.meta_var.set(
                f"Tamed · page {self._page + 1}/{pages} · {self._total}{qbit}"
            )
            self._render_grid()
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            if gen != self._gen:
                return
            self._busy = False
            self.meta_var.set(f"Tamed failed: {exc}")
            self._set_nav(True)

        self._fetch_tamed(self._page, self._query, on_ok, on_err)

    def _render_grid(self) -> None:
        release_photos(self._photos)
        self._photos = []
        if self._gallery is not None:
            try:
                self._gallery.destroy()
            except Exception:
                pass
            self._gallery = None
        self._gallery = NumberedPairBoard(
            self.grid_fr,
            photos=self._photos,
            bind_pair=self._bind_pair,
            gen_fn=lambda: self._gen,
            preview_scale=self._preview_scale,
            page_base=self._page * self._page_size,
        )
        self._gallery.render(
            self._items, page_base=self._page * self._page_size
        )

    def _bind_pair(
        self,
        before_lbl: tk.Label,
        after_lbl: tk.Label,
        char_id: int,
        post_url: str,
        extras: tuple[tk.Misc, ...] = (),
    ) -> None:
        before_lbl.bind(
            "<Button-1>", lambda _e, x=char_id: self._click_open_image(x, "before")
        )
        after_lbl.bind(
            "<Button-1>", lambda _e, x=char_id: self._click_open_image(x, "after")
        )
        widgets: tuple[tk.Misc, ...] = (before_lbl, after_lbl) + tuple(extras)
        for w in widgets:
            w.bind("<Button-2>", lambda _e, x=char_id: self._click_post(x))
            w.bind(
                "<Button-3>",
                lambda e, x=char_id, u=post_url: self._thumb_context_menu(e, x, u),
            )

    def _item_by_id(self, char_id: int) -> dict[str, Any] | None:
        for it in self._items:
            try:
                if int(it.get("id") or 0) == int(char_id):
                    return it
            except Exception:
                continue
        return None

    def _click_open_image(self, char_id: int, side: str) -> None:
        from link_bridge.open_image import open_full_image

        item = self._item_by_id(char_id) or {}
        if side == "before":
            url = (
                (item.get("before_image_url") or "").strip()
                or (item.get("before_preview_url") or "").strip()
            )
        else:
            url = (
                (item.get("after_image_url") or "").strip()
                or (item.get("image_url") or "").strip()
                or (item.get("after_preview_url") or "").strip()
                or (item.get("preview_url") or "").strip()
            )
        if not url:
            self.meta_var.set(f"No {side} image for #{char_id}")
            return
        self.meta_var.set(f"Opening {side} #{char_id}…")

        def on_err(exc: BaseException) -> None:
            self.after(0, lambda: self.meta_var.set(f"Open image failed: {exc}"))

        open_full_image(url, on_err=on_err)

    def _thumb_context_menu(self, event, char_id: int, post_url: str) -> None:
        from link_bridge.thumb_menu import popup_thumb_menu

        item = self._item_by_id(char_id) or {}
        popup_thumb_menu(
            event.widget,
            event,
            char_id=int(char_id),
            post_url=post_url,
            on_open_post=self._open_post,
            on_craft=self._menu_craft,
            on_register_cup=self._click_register_cup
            if self._register_cup is not None
            else None,
            on_show_checkpoint=self._show_checkpoint_image,
            can_tame=bool(item.get("can_tame")),
            is_tamed=bool(item.get("tamed")),
            has_checkpoint=bool(item.get("has_checkpoint")),
            checkpoint_image_url=str(item.get("checkpoint_image_url") or ""),
        )

    def _show_checkpoint_image(self, url: str) -> None:
        from link_bridge.open_image import open_full_image

        target = (url or "").strip()
        if not target:
            self.meta_var.set("No checkpoint image")
            return
        self.meta_var.set("Opening checkpoint…")

        def on_err(exc: BaseException) -> None:
            self.after(0, lambda: self.meta_var.set(f"Open checkpoint failed: {exc}"))

        open_full_image(target, on_err=on_err)

    def _open_post(self, url: str) -> None:
        from link_bridge.browser_open import open_url

        open_url(url)

    def _menu_craft(self, char_id: int, action_id: str) -> None:
        if char_id <= 0 or self._busy:
            return
        if self._dm_craft is None:
            if action_id == "omni" and self._open_omni is not None:
                self._busy = True
                self.meta_var.set(f"Opening omni #{char_id}…")

                def on_ok(body: dict) -> None:
                    self._busy = False
                    if body.get("op") == "open_omni_ok":
                        self.meta_var.set(f"Omni #{char_id} sent to DM")
                        if self._should_focus():
                            try:
                                from link_bridge.focus_telegram import focus_telegram

                                focus_telegram()
                            except Exception:
                                pass
                    else:
                        self.meta_var.set(
                            f"Omni failed: {body.get('error') or 'failed'}"
                        )
                    self._set_nav(True)

                def on_err(exc: BaseException) -> None:
                    self._busy = False
                    self.meta_var.set(f"Omni failed: {exc}")
                    self._set_nav(True)

                self._open_omni(int(char_id), on_ok, on_err)
            return
        self._busy = True
        label = action_id if action_id != "omni" else "Omnicraft"
        self.meta_var.set(f"DM craft #{char_id}: {label}…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "dm_craft_ok":
                self.meta_var.set(f"DM craft #{char_id}: {label} ✓")
                if self._should_focus():
                    try:
                        from link_bridge.focus_telegram import focus_telegram

                        focus_telegram()
                    except Exception:
                        pass
            else:
                self.meta_var.set(f"Craft failed: {body.get('error') or 'failed'}")
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.meta_var.set(f"Craft failed: {exc}")
            self._set_nav(True)

        self._dm_craft(int(char_id), str(action_id), on_ok, on_err)

    def _click_register_cup(self, char_id: int) -> None:
        if char_id <= 0 or self._busy or self._register_cup is None:
            return
        self._busy = True
        self.meta_var.set(f"Registering #{char_id} for daily cup…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "register_cup_ok":
                theme = (body.get("theme") or "").strip()
                bit = f" · {theme}" if theme else ""
                self.meta_var.set(f"Daily cup: #{char_id} registered{bit}")
                if self._should_focus():
                    try:
                        from link_bridge.focus_telegram import focus_telegram

                        focus_telegram()
                    except Exception:
                        pass
            else:
                self.meta_var.set(
                    f"Daily cup failed: {body.get('error') or 'failed'}"
                )
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.meta_var.set(f"Daily cup failed: {exc}")
            self._set_nav(True)

        self._register_cup(int(char_id), on_ok, on_err)

    def _click_post(self, char_id: int) -> None:
        if char_id <= 0 or self._busy:
            return
        target = (self._get_post_target() or "group").strip().lower()
        dest = "DM" if target == "dm" else "group"
        self._busy = True
        self.meta_var.set(f"Posting tamed #{char_id} → {dest}…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "post_grid_ok":
                kind = "tamed" if body.get("tamed") else "card"
                self.meta_var.set(f"Posted {kind} #{char_id} → {dest}")
                self._on_log(f"Post {kind} char {char_id} → {dest}")
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
