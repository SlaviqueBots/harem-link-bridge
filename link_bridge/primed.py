"""Primed cards — roster gallery of obtain/origin previews ready to tame."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import ttk

from link_bridge.tamed import (
    TAMED_PAGE_SIZE,
    _effective_owner_q,
    _normalize_whose,
)
from link_bridge.thumb_grid import PAGE_SIZE, release_photos

logger = logging.getLogger(__name__)

PRIMED_PAGE_SIZE = max(PAGE_SIZE, TAMED_PAGE_SIZE)

OkCb = Callable[[dict[str, Any]], None]
ErrCb = Callable[[BaseException], None]
FetchPrimedFn = Callable[[int, str, OkCb, ErrCb], None]
PostGridFn = Callable[[int, OkCb, ErrCb], None]
OpenOmniFn = Callable[[int, OkCb, ErrCb], None]
RegisterCupFn = Callable[[int, OkCb, ErrCb], None]
DmCraftFn = Callable[[int, str, OkCb, ErrCb], None]
GetSetNamesFn = Callable[[], list[str]]
OnSetNamesFn = Callable[[list[str]], None]
FocusPrefFn = Callable[[], bool]
TargetGetFn = Callable[[], str]
TargetSetFn = Callable[[str], None]
LeftClickOmniGetFn = Callable[[], bool]
LeftClickOmniSetFn = Callable[[bool], None]
BrowseUsersFn = Callable[[str, OkCb, ErrCb], None]
OpenOmniUiFn = Callable[[int], None]


class PrimedPanel(ttk.Frame):
    """Paged origin-preview grid for cards primed for taming."""

    def __init__(
        self,
        master,
        *,
        fetch_primed: FetchPrimedFn,
        post_grid: PostGridFn,
        open_omni: OpenOmniFn | None = None,
        open_omni_ui: OpenOmniUiFn | None = None,
        register_cup: RegisterCupFn | None = None,
        dm_craft: DmCraftFn | None = None,
        should_focus_telegram: FocusPrefFn | None = None,
        get_post_target: TargetGetFn | None = None,
        set_post_target: TargetSetFn | None = None,
        get_left_click_omni: LeftClickOmniGetFn | None = None,
        set_left_click_omni: LeftClickOmniSetFn | None = None,
        prefer_original_open: Callable[[], bool] | None = None,
        get_text_edit_geometry: Callable[[], str] | None = None,
        set_text_edit_geometry: Callable[[str], None] | None = None,
        fetch_browse_users: BrowseUsersFn | None = None,
        preview_scale: float = 1.5,
        scroll_speed: float = 3.0,
        get_set_names: GetSetNamesFn | None = None,
        on_set_names: OnSetNamesFn | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._fetch_primed = fetch_primed
        self._post_grid = post_grid
        self._open_omni = open_omni
        self._open_omni_ui = open_omni_ui
        self._register_cup = register_cup
        self._dm_craft = dm_craft
        self._should_focus = should_focus_telegram or (lambda: False)
        self._get_post_target = get_post_target or (lambda: "group")
        self._set_post_target = set_post_target
        self._get_left_click_omni = get_left_click_omni or (lambda: False)
        self._set_left_click_omni = set_left_click_omni
        self._prefer_original = prefer_original_open or (lambda: True)
        self._get_text_geo = get_text_edit_geometry or (lambda: "")
        self._set_text_geo = set_text_edit_geometry
        self._fetch_browse_users = fetch_browse_users
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.5)))
        self._scroll_speed = max(0.25, min(6.0, float(scroll_speed or 3.0)))
        self._get_set_names = get_set_names or (lambda: [])
        self._on_set_names = on_set_names
        self._on_log = on_log or (lambda _s: None)
        self._page = 0
        self._total = 0
        self._page_size = PRIMED_PAGE_SIZE
        self._query = ""
        self._whose = ""
        self._scope = "own"
        self._items: list[dict[str, Any]] = []
        self._photos: list[Any] = []
        self._busy = False
        self._gen = 0
        self._gallery = None
        self._gallery_host: ttk.Frame | None = None
        self._search_after: str | None = None
        self._show_hidden = tk.BooleanVar(value=False)
        self._hide_mode = tk.BooleanVar(value=False)
        self._view_slots: dict[bool, dict[str, Any]] = {}
        self._empty_msg_host: ttk.Frame | None = None

        from link_bridge.theme import bind_entry_clipboard

        search_row = ttk.Frame(self)
        search_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(search_row, text="Search").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.search_entry.bind("<Return>", lambda _e: self._search_now())
        self.search_var.trace_add("write", self._on_search_typed)
        bind_entry_clipboard(self.search_entry)
        ttk.Button(search_row, text="Search", command=self._search_now).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(search_row, text="Clear", command=self._clear_search).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        filter_row = ttk.Frame(self)
        filter_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Checkbutton(
            filter_row,
            text="Show hidden",
            variable=self._show_hidden,
            command=self._on_show_hidden_toggle,
        ).pack(side=tk.LEFT)
        self._hide_mode_btn = ttk.Checkbutton(
            filter_row,
            text="Hide mode (LMB)",
            variable=self._hide_mode,
            command=self._sync_hide_mode_style,
        )
        self._hide_mode_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._sync_hide_mode_style()

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
        self._lmb_omni_btn = ttk.Button(
            bar, text=self._lmb_omni_label(), command=self._toggle_left_click_omni
        )
        self._lmb_omni_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.meta_var = tk.StringVar(value="Connect to load primed cards.")
        ttk.Label(bar, textvariable=self.meta_var).pack(side=tk.LEFT, padx=(12, 0))

        self.grid_fr = ttk.Frame(self)
        self.grid_fr.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self._set_nav(False)

    def _sync_hide_mode_style(self) -> None:
        try:
            if self._hide_mode.get():
                self._hide_mode_btn.configure(style="Accent.TCheckbutton")
            else:
                self._hide_mode_btn.configure(style="TCheckbutton")
        except Exception:
            pass

    def _filter_mode(self) -> bool:
        return bool(self._show_hidden.get())

    def _visible_items_for_mode(
        self, items: list[dict[str, Any]], hidden: bool
    ) -> list[dict[str, Any]]:
        from link_bridge.primed_hidden import is_hidden

        if hidden:
            return [it for it in items if is_hidden(int(it.get("id") or 0))]
        return [it for it in items if not is_hidden(int(it.get("id") or 0))]

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
            else self._visible_items_for_mode(self._items, mode)
        )
        return (
            int(self._page),
            (self._query or "").strip().lower(),
            (self._whose or "").strip().lower(),
            bool(mode),
            tuple(int(it.get("id") or 0) for it in visible),
        )

    def _destroy_gallery(self) -> None:
        self._gallery = None
        self._gallery_host = None

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
        self._empty_msg_host = ttk.Frame(self.grid_fr)
        self._empty_msg_host.pack(fill=tk.BOTH, expand=True)
        ttk.Label(self._empty_msg_host, text=msg, justify=tk.CENTER).pack(
            expand=True, pady=12
        )

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

    def _save_view_slot(self, mode: bool) -> None:
        if self._gallery is None or self._gallery_host is None:
            return
        self._view_slots[mode] = {
            "host": self._gallery_host,
            "gallery": self._gallery,
            "sig": self._page_sig(self._items, hidden=mode),
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
        slot = self._view_slots.get(mode)
        if not slot or slot.get("gallery") is None:
            return False
        if slot.get("sig") != self._page_sig(self._items, hidden=mode):
            return False
        self._activate_view_slot(mode)
        yfrac = float(slot.get("yfrac") or 0.0)
        if yfrac > 0.0:
            self.after(30, lambda y=yfrac: self._restore_gallery_yview(y))
        return True

    def _remember_view_slot(self, mode: bool, yfrac: float) -> None:
        if self._gallery is None or self._gallery_host is None:
            return
        self._view_slots[mode] = {
            "host": self._gallery_host,
            "gallery": self._gallery,
            "sig": self._page_sig(self._items, hidden=mode),
            "yfrac": float(yfrac or 0.0),
        }

    def _note_gallery_char_removed(self) -> None:
        mode = self._filter_mode()
        slot = self._view_slots.get(mode)
        if slot is not None:
            slot["sig"] = self._page_sig(self._items, hidden=mode)

    def has_cached_view(self) -> bool:
        return bool(self._items)

    def _visible_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._visible_items_for_mode(items, self._filter_mode())

    def _lmb_omni_label(self) -> str:
        return (
            "LMB → Omni: on"
            if self._get_left_click_omni()
            else "LMB → Omni: off"
        )

    def _toggle_left_click_omni(self) -> None:
        nxt = not bool(self._get_left_click_omni())
        if self._set_left_click_omni is not None:
            self._set_left_click_omni(nxt)
        else:
            self.sync_lmb_omni_button()

    def sync_lmb_omni_button(self) -> None:
        try:
            self._lmb_omni_btn.configure(text=self._lmb_omni_label())
        except Exception:
            pass

    def _on_show_hidden_toggle(self) -> None:
        new_mode = self._filter_mode()
        old_mode = not new_mode
        self._save_view_slot(old_mode)
        if self._restore_view_slot(new_mode):
            self._sync_meta_label()
            return
        self._render_grid(preserve_scroll=True)

    def _apply_hide_change(self, char_id: int, now_hidden: bool, *, log: bool = True) -> None:
        cid = int(char_id)
        mode = self._filter_mode()
        if now_hidden and not mode:
            if self._gallery is not None and self._gallery.remove_char(cid):
                self._note_gallery_char_removed()
                self._sync_meta_label()
                if log:
                    self._on_log(f"Primed #{cid} hidden")
                if not self._visible_items_for_mode(self._items, False):
                    self._view_slots.pop(False, None)
                    self._destroy_gallery()
                    self._render_grid()
                return
        if (not now_hidden) and mode:
            if self._gallery is not None and self._gallery.remove_char(cid):
                self._note_gallery_char_removed()
                self._sync_meta_label()
                if log:
                    self._on_log(f"Primed #{cid} unhidden")
                if not self._visible_items_for_mode(self._items, True):
                    self._view_slots.pop(True, None)
                    self._destroy_gallery()
                    self._render_grid()
                return
        if not now_hidden and not mode:
            self._view_slots.pop(False, None)
        elif now_hidden and mode:
            self._view_slots.pop(True, None)
        self._render_grid(preserve_scroll=True)
        if log:
            state = "hidden" if now_hidden else "visible"
            self._on_log(f"Primed #{cid} {state}")

    def _toggle_hide_card(self, char_id: int) -> None:
        from link_bridge.primed_hidden import toggle_hidden

        cid = int(char_id)
        if cid <= 0:
            return
        now_hidden = toggle_hidden(cid)
        self._apply_hide_change(cid, now_hidden)

    def _set_card_hidden(self, char_id: int, hidden: bool) -> None:
        from link_bridge.primed_hidden import is_hidden, set_hidden

        cid = int(char_id)
        if bool(hidden) == is_hidden(cid):
            return
        set_hidden(cid, bool(hidden))
        self._apply_hide_change(cid, bool(hidden))

    def _post_btn_label(self) -> str:
        t = (self._get_post_target() or "group").strip().lower()
        return "Post → DM" if t == "dm" else "Post → Group"

    def _target_label(self) -> str:
        t = (self._get_post_target() or "group").strip().lower()
        return "Post target: DM" if t == "dm" else "Post target: Group"

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

    def _browse_pick_user(self, username: str) -> None:
        self._whose = (username or "").strip().lstrip("@")
        self.load_page(0)

    def _effective_q(self) -> str:
        return _effective_owner_q(self._whose, self.search_var.get())

    def set_preview_scale(self, scale: float) -> None:
        self._preview_scale = max(0.5, min(2.0, float(scale or 1.5)))
        if self._gallery is not None:
            try:
                self._gallery.set_preview_scale(self._preview_scale)
            except Exception:
                pass

    def set_scroll_speed(self, speed: float) -> None:
        self._scroll_speed = max(0.25, min(6.0, float(speed or 3.0)))
        if self._gallery is not None and hasattr(self._gallery, "set_scroll_speed"):
            try:
                self._gallery.set_scroll_speed(self._scroll_speed)
            except Exception:
                pass

    def clear(self) -> None:
        self._items = []
        self._total = 0
        self._page = 0
        self._invalidate_view_slots()
        self._clear_empty_message_host()
        for child in list(self.grid_fr.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        release_photos(self._photos)
        self._photos = []
        self.meta_var.set("Connect to load primed cards.")
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
        self._whose = _normalize_whose(self._whose)
        self._query = (self.search_var.get() or "").strip()
        q = self._effective_q()
        self.meta_var.set("Loading primed…")
        self._set_nav(False)

        def on_ok(body: dict) -> None:
            if gen != self._gen:
                return
            self._busy = False
            if body.get("op") != "roster_page_ok":
                self.meta_var.set(f"Primed failed: {body.get('error') or 'failed'}")
                self._set_nav(True)
                return
            raw_items = list(body.get("items") or [])
            self._invalidate_view_slots()
            self._items = raw_items
            try:
                self._total = int(body.get("total") or 0)
            except Exception:
                self._total = len(raw_items)
            try:
                self._page = int(body.get("page") or self._page)
            except Exception:
                pass
            self._scope = str(body.get("scope") or "own")
            self._sync_meta_label()
            self._render_grid()
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            if gen != self._gen:
                return
            self._busy = False
            self.meta_var.set(f"Primed failed: {exc}")
            self._set_nav(True)

        self._fetch_primed(self._page, q, on_ok, on_err)

    def _sync_meta_label(self) -> None:
        visible = self._visible_items(self._items)
        pages = (
            max(1, (self._total + self._page_size - 1) // self._page_size)
            if self._total
            else 1
        )
        scope_bit = ""
        if self._scope == "user" and self._whose:
            scope_bit = f" · @{self._whose}"
        qbit = f" · “{self._query}”" if self._query else ""
        shown = len(visible)
        filter_note = ""
        if self._show_hidden.get():
            filter_note = " · hidden only"
        elif shown != len(self._items):
            filter_note = f" · {shown} shown"
        self.meta_var.set(
            f"Primed{scope_bit} · page {self._page + 1}/{pages} · "
            f"{self._total}{filter_note}{qbit}"
        )

    def _ensure_gallery(self, *, mode: bool | None = None):
        hidden = self._filter_mode() if mode is None else bool(mode)
        slot = self._view_slots.get(hidden)
        if slot and slot.get("gallery") is not None:
            self._activate_view_slot(hidden)
            return slot["gallery"]
        self._clear_empty_message_host()
        self._pack_all_slots_forget()
        self._gallery_host = ttk.Frame(self.grid_fr)
        self._gallery_host.pack(fill=tk.BOTH, expand=True)
        from link_bridge.gallery import JustifiedGallery

        self._gallery = JustifiedGallery(
            self._gallery_host,
            photos=self._photos,
            bind_thumb=self._bind_thumb,
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

    def _render_grid(self, *, preserve_scroll: bool = False) -> None:
        mode = self._filter_mode()
        yfrac = self._gallery_yview() if preserve_scroll else 0.0
        visible = self._visible_items(self._items)
        if not self._items:
            self._invalidate_view_slots()
            self._clear_empty_message_host()
            for child in list(self.grid_fr.winfo_children()):
                try:
                    child.destroy()
                except Exception:
                    pass
            self._show_empty_message("No primed cards match.")
            return
        if not visible:
            self._clear_empty_message_host()
            self._pack_all_slots_forget()
            if self._show_hidden.get():
                msg = "No hidden cards on this page."
            else:
                msg = "All cards on this page are hidden.\nEnable “Show hidden”."
            self._show_empty_message(msg)
            return

        sig = self._page_sig(visible, hidden=mode)
        slot = self._view_slots.get(mode)
        if slot and slot.get("gallery") is not None and slot.get("sig") == sig:
            self._activate_view_slot(mode)
            y = float(slot.get("yfrac") or yfrac)
            if preserve_scroll and y > 0.0:
                self.after(30, lambda yy=y: self._restore_gallery_yview(yy))
            return

        self._clear_empty_message_host()
        gallery = self._ensure_gallery(mode=mode)
        gallery.render(visible)
        store_y = yfrac if preserve_scroll else 0.0
        self._remember_view_slot(mode, store_y)
        if preserve_scroll and yfrac > 0.0:
            self.after(30, lambda y=yfrac: self._restore_gallery_yview(y))

    def _bind_thumb(self, label: tk.Label, char_id: int, post_url: str) -> None:
        label.bind("<Button-1>", lambda _e, x=char_id: self._click_primary(x))
        label.bind("<Button-2>", lambda _e, x=char_id: self._click_post(x))
        label.bind(
            "<Button-3>",
            lambda e, x=char_id, u=post_url: self._thumb_context_menu(e, x, u),
        )

    def _click_primary(self, char_id: int) -> None:
        if self._hide_mode.get():
            self._toggle_hide_card(char_id)
            return
        if self._get_left_click_omni() and self._open_omni_ui is not None:
            self._open_omni_ui(int(char_id))
            return
        self._click_open_image(char_id)

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
            self.meta_var.set(f"No image for #{char_id}")
            return
        self.meta_var.set(f"Opening #{char_id}…")

        def on_err(exc: BaseException) -> None:
            self.after(0, lambda: self.meta_var.set(f"Open image failed: {exc}"))

        open_full_image(url, on_err=on_err)

    def _thumb_context_menu(self, event, char_id: int, post_url: str) -> None:
        from link_bridge.primed_hidden import is_hidden
        from link_bridge.thumb_menu import popup_thumb_menu

        item = self._item_by_id(char_id) or {}
        name = str(item.get("name") or "").strip()
        if name:
            self.meta_var.set(f"#{char_id} · {name}")
        else:
            self.meta_var.set(f"#{char_id}")
        cid = int(char_id)
        extra: list[tuple[str, Callable[[], None]]] = []
        if is_hidden(cid):
            extra.append(
                ("Unhide from Primed", lambda: self._set_card_hidden(cid, False))
            )
        else:
            extra.append(
                ("Hide from Primed", lambda: self._set_card_hidden(cid, True))
            )
        popup_thumb_menu(
            event.widget,
            event,
            char_id=cid,
            post_url=post_url,
            on_open_post=self._open_post,
            on_craft=self._menu_craft,
            on_register_cup=self._click_register_cup
            if self._register_cup is not None
            else None,
            on_show_checkpoint=self._show_checkpoint_image,
            on_edit_flavour=self._edit_flavour,
            on_edit_note=self._edit_note,
            can_tame=bool(item.get("can_tame")),
            is_tamed=bool(item.get("tamed")),
            has_checkpoint=bool(item.get("has_checkpoint")),
            checkpoint_image_url=str(item.get("checkpoint_image_url") or ""),
            char_name=name,
            set_names=list(self._get_set_names()),
            current_set=str(item.get("set") or ""),
            on_add_to_set=self._add_to_set,
            on_new_set=self._add_to_new_set,
            on_remove_from_set=self._remove_from_set,
            can_edit_sets=bool(item.get("mine", True)) and not self._whose,
            can_cycle_name=bool(item.get("can_cycle_name")),
            is_done=bool(item.get("done")),
            extra_entries=extra,
        )

    def _edit_flavour(self, char_id: int) -> None:
        from link_bridge.text_edit_dialog import ask_text

        item = self._item_by_id(char_id) or {}
        text = ask_text(
            self,
            title=f"Flavour #{char_id}",
            initial=str(item.get("flavour") or ""),
            prompt="Public flavour text (saved quietly — no Telegram post).",
            geometry=self._get_text_geo(),
            on_geometry=self._set_text_geo,
        )
        if text is None:
            return
        self._menu_craft(char_id, f"flset:{text}")

    def _edit_note(self, char_id: int) -> None:
        from link_bridge.text_edit_dialog import ask_text

        item = self._item_by_id(char_id) or {}
        text = ask_text(
            self,
            title=f"Note #{char_id}",
            initial=str(item.get("note") or ""),
            prompt="Owner-only note (saved quietly — no Telegram post).",
            geometry=self._get_text_geo(),
            on_geometry=self._set_text_geo,
        )
        if text is None:
            return
        self._menu_craft(char_id, f"ntset:{text}")

    def _note_set_used(self, name: str) -> None:
        n = " ".join((name or "").split())
        if not n or self._on_set_names is None:
            return
        names = list(self._get_set_names())
        key = n.casefold()
        if not any(x.casefold() == key for x in names):
            names.append(n)
            self._on_set_names(names)

    def _add_to_set(self, char_id: int, set_name: str) -> None:
        name = " ".join((set_name or "").split())
        if not name:
            return
        self._menu_craft(char_id, f"stadd:{name}")

    def _remove_from_set(self, char_id: int, set_name: str) -> None:
        name = " ".join((set_name or "").split())
        if not name:
            return
        self._menu_craft(char_id, f"strem:{name}")

    def _add_to_new_set(self, char_id: int) -> None:
        from link_bridge.text_edit_dialog import ask_set_name

        text = ask_set_name(
            self,
            title=f"New set #{char_id}",
            geometry=self._get_text_geo(),
            on_geometry=self._set_text_geo,
        )
        if text is None:
            return
        self._menu_craft(char_id, f"stadd:{text}")

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
        if action_id == "omni" and self._open_omni_ui is not None:
            self._open_omni_ui(int(char_id))
            return
        if action_id == "mi_omni":
            open_omni_after_mirror = True
            action_id = "mi"
        else:
            open_omni_after_mirror = False
        if self._dm_craft is None:
            return
        self._busy = True
        label = action_id if action_id != "omni" else "Omnicraft"
        self.meta_var.set(f"DM craft #{char_id}: {label}…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "dm_craft_ok":
                detail = str(body.get("detail") or "ok").strip()
                silent = bool(body.get("silent"))
                notice = detail if detail and detail != "ok" else f"{label} ✓"
                self.meta_var.set(f"#{char_id}: {notice}")
                if open_omni_after_mirror and self._open_omni_ui is not None:
                    from link_bridge.thumb_menu import mirror_char_id_from_craft

                    mirror_id = mirror_char_id_from_craft(body)
                    if mirror_id > 0:
                        self._open_omni_ui(mirror_id)
                if silent:
                    from link_bridge.thumb_menu import apply_silent_craft_item

                    apply_silent_craft_item(self._item_by_id(char_id), action_id)
                    if str(action_id).startswith("stadd:"):
                        self._note_set_used(str(action_id).split(":", 1)[1])
                    if action_id in ("tr", "ptr", "tm"):
                        self._drop_char(int(char_id))
                elif self._should_focus():
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

    def _drop_char(self, char_id: int) -> None:
        """Remove a card from the primed grid in place (e.g. after taming)."""
        cid = int(char_id)
        before = len(self._items)
        self._items = [it for it in self._items if int(it.get("id") or 0) != cid]
        if len(self._items) == before:
            return
        self._total = max(0, int(self._total) - 1)
        if self._gallery is not None and hasattr(self._gallery, "remove_char"):
            try:
                if self._gallery.remove_char(cid):
                    self._note_gallery_char_removed()
                    self._sync_meta_label()
                    return
            except Exception:
                pass
        self._sync_meta_label()
        self._render_grid(preserve_scroll=True)

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
                self.meta_var.set(f"Posted card #{char_id} → {dest}")
                self._on_log(f"Post card char {char_id} → {dest}")
            else:
                self.meta_var.set(f"Post failed: {body.get('error') or 'failed'}")
            self._set_nav(True)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.meta_var.set(f"Post failed: {exc}")
            self._set_nav(True)

        self._post_grid(int(char_id), on_ok, on_err)
