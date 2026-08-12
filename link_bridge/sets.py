"""Sets browser — list set names + fill-viewport card grid."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import ttk

from link_bridge.thumb_grid import (
    COLS,
    PAGE_SIZE,
    ROWS,
    cache_get,
    compute_thumb,
    decode_thumb,
    release_photos,
    schedule_thumb_fetch,
)

logger = logging.getLogger(__name__)

RESIZE_DEBOUNCE_MS = 120

OkCb = Callable[[dict[str, Any]], None]
ErrCb = Callable[[BaseException], None]
ListSetsFn = Callable[[OkCb, ErrCb], None]
FetchPageFn = Callable[[int, str, int, str, OkCb, ErrCb], None]
PostGridFn = Callable[[int, OkCb, ErrCb], None]
OpenOmniFn = Callable[[int, OkCb, ErrCb], None]
RegisterCupFn = Callable[[int, OkCb, ErrCb], None]
DmCraftFn = Callable[[int, str, OkCb, ErrCb], None]
FocusPrefFn = Callable[[], bool]
TargetGetFn = Callable[[], str]
TargetSetFn = Callable[[str], None]


class SetsPanel(ttk.Frame):
    """Left: set names. Right: all cards in that set (no done/undone tabs)."""

    def __init__(
        self,
        master,
        *,
        list_sets: ListSetsFn,
        fetch_page: FetchPageFn,
        post_grid: PostGridFn,
        open_omni: OpenOmniFn | None = None,
        register_cup: RegisterCupFn | None = None,
        dm_craft: DmCraftFn | None = None,
        should_focus_telegram: FocusPrefFn | None = None,
        get_post_target: TargetGetFn | None = None,
        set_post_target: TargetSetFn | None = None,
        natural_thumbs: bool = False,
        preview_scale: float = 1.5,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._list_sets = list_sets
        self._fetch_page = fetch_page
        self._post_grid = post_grid
        self._open_omni = open_omni
        self._register_cup = register_cup
        self._dm_craft = dm_craft
        self._should_focus = should_focus_telegram or (lambda: False)
        self._get_post_target = get_post_target or (lambda: "group")
        self._set_post_target = set_post_target
        self._natural_thumbs = bool(natural_thumbs)
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.5)))
        self._on_log = on_log or (lambda _s: None)
        self._selected = ""
        self._page = 0
        self._total = 0
        self._page_size = PAGE_SIZE
        self._items: list[dict[str, Any]] = []
        self._photos: list[Any] = []
        self._thumb = 140
        self._busy = False
        self._gen = 0
        self._resize_after: str | None = None
        self._gallery = None

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
        self._target_btn = ttk.Button(
            bar, text=self._target_label(), command=self._toggle_target
        )
        self._target_btn.pack(side=tk.LEFT, padx=(12, 0))
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

    def set_natural_thumbs(self, enabled: bool) -> None:
        flag = bool(enabled)
        if flag == self._natural_thumbs:
            return
        self._natural_thumbs = flag
        if self._items:
            self._render_grid(reuse_bytes=True)

    def set_preview_scale(self, scale: float) -> None:
        self._preview_scale = max(0.5, min(2.0, float(scale or 1.5)))
        if self._gallery is not None:
            try:
                self._gallery.set_preview_scale(self._preview_scale)
            except Exception:
                pass

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
        self.clear_grid()
        self.meta_var.set("Pick a set on the left.")

    def clear_grid(self) -> None:
        self._gen += 1
        self._items = []
        self._total = 0
        self._page = 0
        if self._gallery is not None:
            try:
                self._gallery.destroy()
            except Exception:
                pass
            self._gallery = None
        for child in list(self.grid_fr.winfo_children()):
            child.destroy()
        release_photos(self._photos)
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
        if self._natural_thumbs:
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
        del reuse_bytes
        if self._gallery is not None:
            try:
                self._gallery.destroy()
            except Exception:
                pass
            self._gallery = None
        for child in list(self.grid_fr.winfo_children()):
            child.destroy()
        release_photos(self._photos)
        if self._natural_thumbs:
            from link_bridge.gallery import JustifiedGallery

            self._gallery = JustifiedGallery(
                self.grid_fr,
                photos=self._photos,
                bind_thumb=self._bind_thumb,
                gen_fn=lambda: self._gen,
                preview_scale=self._preview_scale,
            )
            self._gallery.render(self._items)
            return
        for c in range(COLS):
            self.grid_fr.columnconfigure(c, weight=1, uniform="col")
        for r in range(ROWS):
            self.grid_fr.rowconfigure(r, weight=1, uniform="row")
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
            self._bind_thumb(thumb_lbl, cid, post_url)
            url = (item.get("preview_url") or "").strip()
            if url:
                self._load_thumb(
                    thumb_lbl,
                    url,
                    self._gen,
                    post_url=post_url,
                    char_id=cid,
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
    ) -> None:
        thumb = self._thumb

        def apply_bytes(data: bytes) -> None:
            if gen != self._gen or not label.winfo_exists():
                return
            try:
                photo = decode_thumb(data, thumb, natural=False)
                self._photos.append(photo)
                label.configure(image=photo, text="")
                self._bind_thumb(label, char_id, post_url)
            except Exception:
                label.configure(text="no preview")

        cached = cache_get(url)
        if cached is not None:
            apply_bytes(cached)
            return

        def on_data(data: bytes) -> None:
            try:
                label.after(0, lambda d=data: apply_bytes(d))
            except Exception:
                pass

        def on_err(_exc: BaseException) -> None:
            def fail() -> None:
                if gen != self._gen or not label.winfo_exists():
                    return
                label.configure(text="no preview")

            try:
                label.after(0, fail)
            except Exception:
                pass

        schedule_thumb_fetch(url, on_data=on_data, on_err=on_err)

    def _bind_thumb(self, label: tk.Label, char_id: int, post_url: str) -> None:
        label.bind("<Button-1>", lambda _e, x=char_id: self._click_open_image(x))
        label.bind("<Button-2>", lambda _e, x=char_id: self._click_post(x))
        label.bind(
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

    def _click_open_image(self, char_id: int) -> None:
        from link_bridge.open_image import open_full_image

        item = self._item_by_id(char_id) or {}
        url = (
            (item.get("image_url") or "").strip()
            or (item.get("preview_url") or "").strip()
        )
        if not url:
            self.meta_var.set(f"No image URL for #{char_id}")
            return
        self.meta_var.set(f"Opening image #{char_id}…")

        def on_err(exc: BaseException) -> None:
            self.after(
                0,
                lambda: self.meta_var.set(f"Open image failed: {exc}"),
            )

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
            self.after(
                0,
                lambda: self.meta_var.set(f"Open checkpoint failed: {exc}"),
            )

        open_full_image(target, on_err=on_err)

    def _menu_craft(self, char_id: int, action_id: str) -> None:
        if char_id <= 0 or self._busy:
            return
        if self._dm_craft is None:
            if action_id == "omni" and self._open_omni is not None:
                self._click_omni(char_id)
            else:
                self.meta_var.set(f"Craft “{action_id}” needs a connected update")
            return
        self._busy = True
        label = action_id if action_id != "omni" else "Omnicraft"
        self.meta_var.set(f"DM craft #{char_id}: {label}…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "dm_craft_ok":
                self.meta_var.set(f"DM craft #{char_id}: {label} ✓")
                self._on_log(f"DM craft {action_id} char {char_id}")
                if self._should_focus():
                    try:
                        from link_bridge.focus_telegram import focus_telegram

                        focus_telegram()
                    except Exception:
                        pass
            else:
                self.meta_var.set(
                    f"Craft failed: {body.get('error') or 'failed'}"
                )
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
                self._on_log(f"Daily cup registered char {char_id}{bit}")
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
        self.meta_var.set(f"Posting #{char_id} → {dest}…")

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

    def _click_omni(self, char_id: int) -> None:
        if char_id <= 0 or self._busy or self._open_omni is None:
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

                        focus_telegram()
                    except Exception:
                        pass
            else:
                self.meta_var.set(f"Open failed: {body.get('error') or 'failed'}")
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.meta_var.set(f"Open failed: {exc}")
            self._set_nav(True)

        self._open_omni(int(char_id), on_ok, on_err)
