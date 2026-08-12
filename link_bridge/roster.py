"""Paged roster: Undone / Done / Sets tabs + fill-viewport thumb grid."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import ttk

from link_bridge.thumb_grid import (
    COLS,
    DEFAULT_GEOMETRY,
    PAGE_SIZE,
    ROWS,
    cache_clear,
    cache_get,
    compute_thumb,
    decode_thumb,
    release_photos,
    schedule_thumb_fetch,
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
RegisterCupFn = Callable[[int, OkCb, ErrCb], None]
DmCraftFn = Callable[[int, str, OkCb, ErrCb], None]
FocusPrefFn = Callable[[], bool]
ListSetsFn = Callable[[OkCb, ErrCb], None]
TargetGetFn = Callable[[], str]
TargetSetFn = Callable[[str], None]
FetchTamedFn = Callable[[int, str, OkCb, ErrCb], None]


def _item_ids(body: dict[str, Any]) -> tuple[int, ...]:
    items = body.get("items") or []
    out: list[int] = []
    for it in items:
        try:
            out.append(int(it.get("id") or 0))
        except Exception:
            out.append(0)
    return tuple(out)


class RosterPanel(ttk.Frame):
    """Undone | Done | Sets — sets sits with done/undone, not in the top bar."""

    def __init__(
        self,
        master,
        *,
        fetch_page: FetchPageFn,
        open_omni: OpenOmniFn,
        post_grid: PostGridFn | None = None,
        register_cup: RegisterCupFn | None = None,
        dm_craft: DmCraftFn | None = None,
        list_sets: ListSetsFn | None = None,
        fetch_tamed: FetchTamedFn | None = None,
        should_focus_telegram: FocusPrefFn | None = None,
        get_post_target: TargetGetFn | None = None,
        set_post_target: TargetSetFn | None = None,
        natural_thumbs: bool = False,
        preview_scale: float = 1.5,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._fetch_page = fetch_page
        self._open_omni = open_omni
        self._post_grid = post_grid
        self._register_cup = register_cup
        self._dm_craft = dm_craft
        self._list_sets = list_sets
        self._fetch_tamed = fetch_tamed
        self._should_focus = should_focus_telegram or (lambda: False)
        self._get_post_target = get_post_target or (lambda: "group")
        self._set_post_target = set_post_target
        self._natural_thumbs = bool(natural_thumbs)
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.5)))
        self._on_log = on_log or (lambda _s: None)
        self._page = 0
        self._total = 0
        self._page_size = PAGE_SIZE
        self._query = ""
        self._done = 0
        self._mode = "undone"  # undone | done | sets | tamed
        self._scope = "own"  # own | user | all (from server)
        self._items: list[dict[str, Any]] = []
        self._photos: list[Any] = []
        self._thumb = 140
        self._busy = False
        self._gen = 0
        self._gen_by_mode = {"undone": 0, "done": 0}
        self._search_after: str | None = None
        self._resize_after: str | None = None
        self._gallery = None
        self._pane_fr: dict[str, ttk.Frame] = {}
        self._pane_gallery: dict[str, Any] = {"undone": None, "done": None}
        self._pane_photos: dict[str, list[Any]] = {"undone": [], "done": []}
        self._pane_ids: dict[str, tuple[int, ...]] = {"undone": (), "done": ()}
        self._pane_query: dict[str, str] = {"undone": "", "done": ""}
        self._pane_page: dict[str, int] = {"undone": 0, "done": 0}
        # Instant Done↔Undone switches: remember last roster_page bodies.
        self._page_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._max_page_cache = 8

        self._tab_nb = ttk.Notebook(self)
        self._tab_nb.pack(fill=tk.X)
        self._tab_undone = ttk.Frame(self._tab_nb)
        self._tab_done = ttk.Frame(self._tab_nb)
        self._tab_sets = ttk.Frame(self._tab_nb)
        self._tab_tamed = ttk.Frame(self._tab_nb)
        self._tab_nb.add(self._tab_undone, text="Undone")
        self._tab_nb.add(self._tab_done, text="Done")
        self._tab_nb.add(self._tab_sets, text="Sets")
        self._tab_nb.add(self._tab_tamed, text="Tamed")
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
                open_omni=open_omni,
                register_cup=register_cup,
                dm_craft=dm_craft,
                should_focus_telegram=should_focus_telegram,
                get_post_target=self._get_post_target,
                set_post_target=self._on_target_from_child,
                natural_thumbs=self._natural_thumbs,
                preview_scale=self._preview_scale,
                on_log=on_log,
            )
            self._sets_panel.pack(fill=tk.BOTH, expand=True)

        self._tamed_body = ttk.Frame(self)
        self._tamed_panel = None
        if fetch_tamed is not None and post_grid is not None:
            from link_bridge.tamed import TamedPanel

            self._tamed_panel = TamedPanel(
                self._tamed_body,
                fetch_tamed=fetch_tamed,
                post_grid=post_grid,
                open_omni=open_omni,
                register_cup=register_cup,
                dm_craft=dm_craft,
                should_focus_telegram=should_focus_telegram,
                get_post_target=self._get_post_target,
                set_post_target=self._on_target_from_child,
                preview_scale=self._preview_scale,
                on_log=on_log,
            )
            self._tamed_panel.pack(fill=tk.BOTH, expand=True)

        self._set_nav(False)

    def set_natural_thumbs(self, enabled: bool) -> None:
        flag = bool(enabled)
        if flag == self._natural_thumbs:
            return
        self._natural_thumbs = flag
        if self._sets_panel is not None:
            self._sets_panel.set_natural_thumbs(flag)
        self._clear_all_panes()
        if self._items:
            self._render_grid(reuse_bytes=True)

    def set_preview_scale(self, scale: float) -> None:
        self._preview_scale = max(0.5, min(2.0, float(scale or 1.5)))
        if self._sets_panel is not None:
            self._sets_panel.set_preview_scale(self._preview_scale)
        if self._tamed_panel is not None:
            self._tamed_panel.set_preview_scale(self._preview_scale)
        for g in self._pane_gallery.values():
            if g is not None:
                try:
                    g.set_preview_scale(self._preview_scale)
                except Exception:
                    pass

    def _target_label(self) -> str:
        t = (self._get_post_target() or "group").strip().lower()
        return "Middle-click → DM" if t == "dm" else "Middle-click → Group"

    def _toggle_post_target(self) -> None:
        cur = (self._get_post_target() or "group").strip().lower()
        nxt = "dm" if cur != "dm" else "group"
        if self._set_post_target is not None:
            self._set_post_target(nxt)
        self.sync_target_buttons()

    def _on_target_from_child(self, target: str) -> None:
        if self._set_post_target is not None:
            self._set_post_target(target)
        self.sync_target_buttons()

    def sync_target_buttons(self) -> None:
        label = self._target_label()
        try:
            self._target_btn.configure(text=label)
        except Exception:
            pass
        if self._sets_panel is not None and hasattr(self._sets_panel, "sync_target_button"):
            self._sets_panel.sync_target_button()
        if self._tamed_panel is not None and hasattr(
            self._tamed_panel, "sync_target_button"
        ):
            self._tamed_panel.sync_target_button()

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
        self._target_btn = ttk.Button(
            bar, text=self._target_label(), command=self._toggle_post_target
        )
        self._target_btn.pack(side=tk.LEFT, padx=(12, 0))
        self.meta_var = tk.StringVar(value="Connect to load roster.")
        ttk.Label(bar, textvariable=self.meta_var).pack(side=tk.LEFT, padx=(12, 0))

        self.grid_fr = ttk.Frame(parent)
        self.grid_fr.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        for mode in ("undone", "done"):
            fr = ttk.Frame(self.grid_fr)
            self._pane_fr[mode] = fr
        self._pane_fr["undone"].pack(fill=tk.BOTH, expand=True)
        self.grid_fr.bind("<Configure>", self._on_grid_resize)

    def _roster_mode_key(self) -> str:
        return "done" if self._mode == "done" else "undone"

    def _show_pane(self, mode: str) -> None:
        for m, fr in self._pane_fr.items():
            if m == mode:
                if not fr.winfo_ismapped():
                    fr.pack(fill=tk.BOTH, expand=True)
            else:
                fr.pack_forget()
        self._gallery = self._pane_gallery.get(mode)
        self._photos = self._pane_photos[mode]
        self._gen = self._gen_by_mode[mode]
        # Route mouse-wheel to the visible gallery.
        g = self._pane_gallery.get(mode)
        if g is not None:
            try:
                from link_bridge import gallery as _gal

                _gal._wheel_target = g
            except Exception:
                pass

    def _shrink_tab_bar(self) -> None:
        try:
            h = max(28, self._tab_nb.winfo_reqheight())
            self._tab_nb.configure(height=h)
        except Exception:
            self._tab_nb.configure(height=32)

    def _show_roster_mode(self) -> None:
        self._sets_body.pack_forget()
        self._tamed_body.pack_forget()
        if not self._roster_body.winfo_ismapped():
            self._roster_body.pack(fill=tk.BOTH, expand=True)

    def _show_sets_mode(self) -> None:
        self._roster_body.pack_forget()
        self._tamed_body.pack_forget()
        if not self._sets_body.winfo_ismapped():
            self._sets_body.pack(fill=tk.BOTH, expand=True)
        if self._sets_panel is not None:
            self._sets_panel.refresh_sets()

    def _show_tamed_mode(self) -> None:
        self._roster_body.pack_forget()
        self._sets_body.pack_forget()
        if not self._tamed_body.winfo_ismapped():
            self._tamed_body.pack(fill=tk.BOTH, expand=True)
        if self._tamed_panel is not None:
            self._tamed_panel.load_page(0)

    def _on_tab_changed(self, _event=None) -> None:
        try:
            idx = int(self._tab_nb.index(self._tab_nb.select()))
        except Exception:
            return
        if idx == 2:
            self._mode = "sets"
            self._show_sets_mode()
            return
        if idx == 3:
            self._mode = "tamed"
            self._show_tamed_mode()
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
        if self._mode in ("sets", "tamed") or not self._items:
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
        if self._mode == "tamed":
            if self._tamed_panel is not None:
                self._tamed_panel.refresh()
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
        if self._mode in ("sets", "tamed"):
            return
        mode = self._roster_mode_key()
        self._show_pane(mode)
        self._busy = True
        self._gen_by_mode[mode] += 1
        gen = self._gen_by_mode[mode]
        self._gen = gen
        q = self._query
        done = int(self._done)
        kind = "done" if done else "undone"
        hint = f" “{q}”" if q else ""
        cache_key = (done, (q or "").strip().lower(), int(page))
        cached = self._page_cache.get(cache_key)
        if cached is not None:
            ids = _item_ids(cached)
            same_view = (
                self._pane_gallery.get(mode) is not None
                and self._pane_ids.get(mode) == ids
                and self._pane_query.get(mode) == q
                and self._pane_page.get(mode) == int(page)
            )
            if same_view:
                # Keep live widgets — no blank flash.
                self._items = list(cached.get("items") or [])
                self._apply_roster_meta(cached, kind=kind, q=q)
                self._busy = False
                self._set_nav(True)
            else:
                self._apply_roster_body(cached, gen, kind=kind, q=q, from_cache=True)
        else:
            self.meta_var.set(f"Loading {kind} page {page + 1}{hint}…")
            self._set_nav(False)

        def on_ok(body: dict) -> None:
            self._busy = False
            if gen != self._gen_by_mode.get(mode, -1):
                return
            if body.get("op") != "roster_page_ok":
                err = body.get("error") or "failed"
                self.meta_var.set(f"Roster error: {err}")
                self._on_log(f"Roster error: {err}")
                self._set_nav(True)
                return
            self._page_cache[cache_key] = body
            self._page_cache.move_to_end(cache_key)
            while len(self._page_cache) > self._max_page_cache:
                self._page_cache.popitem(last=False)
            ids = _item_ids(body)
            if (
                self._pane_gallery.get(mode) is not None
                and self._pane_ids.get(mode) == ids
                and self._pane_query.get(mode) == q
                and self._pane_page.get(mode) == int(body.get("page") or page)
            ):
                self._apply_roster_meta(body, kind=kind, q=q)
                self._set_nav(True)
                return
            self._apply_roster_body(body, gen, kind=kind, q=q, from_cache=False)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            if gen != self._gen_by_mode.get(mode, -1):
                return
            if cached is None:
                self.meta_var.set(f"Roster error: {exc}")
            self._on_log(f"Roster error: {exc}")
            self._set_nav(True)

        self._fetch_page(int(page), q, done, "", on_ok, on_err)

    def _apply_roster_meta(self, body: dict, *, kind: str, q: str) -> None:
        self._page = int(body.get("page") or 0)
        self._page_size = int(body.get("page_size") or PAGE_SIZE)
        self._total = int(body.get("total") or 0)
        self._scope = str(body.get("scope") or "own")
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        q_bit = f" · “{q}”" if q else ""
        scope_bit = ""
        if self._scope == "all":
            scope_bit = " · all"
        elif self._scope == "user":
            scope_bit = " · @"
        self.meta_var.set(
            f"{kind.capitalize()}{scope_bit} · page {self._page + 1}/{pages} · "
            f"{self._total} cards{q_bit}"
        )

    def _apply_roster_body(
        self,
        body: dict,
        gen: int,
        *,
        kind: str,
        q: str,
        from_cache: bool,
    ) -> None:
        mode = self._roster_mode_key()
        if gen != self._gen_by_mode.get(mode, -1):
            return
        self._apply_roster_meta(body, kind=kind, q=q)
        self._items = list(body.get("items") or [])
        self._thumb = compute_thumb(
            max(1, self.grid_fr.winfo_width()),
            max(1, self.grid_fr.winfo_height()),
        )
        self._render_grid(reuse_bytes=False)
        self._set_nav(True)
        if from_cache:
            self._busy = False

    def clear(self) -> None:
        for mode in ("undone", "done"):
            self._gen_by_mode[mode] += 1
        self._gen = self._gen_by_mode[self._roster_mode_key()]
        self._items = []
        self._total = 0
        self._page = 0
        self._scope = "own"
        self._busy = False
        self._page_cache.clear()
        cache_clear()
        self.meta_var.set("Connect to load roster.")
        self._clear_all_panes()
        self._set_nav(False)
        if self._sets_panel is not None:
            self._sets_panel.clear()
        if self._tamed_panel is not None:
            self._tamed_panel.clear()

    def _clear_pane(self, mode: str) -> None:
        g = self._pane_gallery.get(mode)
        if g is not None:
            try:
                g.destroy()
            except Exception:
                pass
            self._pane_gallery[mode] = None
        fr = self._pane_fr.get(mode)
        if fr is not None:
            for child in list(fr.winfo_children()):
                child.destroy()
        release_photos(self._pane_photos[mode])
        self._pane_ids[mode] = ()
        self._pane_query[mode] = ""
        self._pane_page[mode] = 0
        if mode == self._roster_mode_key():
            self._gallery = None
            self._photos = self._pane_photos[mode]

    def _clear_all_panes(self) -> None:
        for mode in ("undone", "done"):
            self._clear_pane(mode)
        # Legacy square-grid leftovers on the host frame.
        for child in list(self.grid_fr.winfo_children()):
            if child not in self._pane_fr.values():
                child.destroy()

    def _clear_grid(self) -> None:
        self._clear_pane(self._roster_mode_key())

    def _render_grid(self, *, reuse_bytes: bool) -> None:
        del reuse_bytes
        mode = self._roster_mode_key()
        self._show_pane(mode)
        self._clear_pane(mode)
        host = self._pane_fr[mode]
        photos = self._pane_photos[mode]
        self._photos = photos
        if self._natural_thumbs:
            from link_bridge.gallery import JustifiedGallery

            gallery = JustifiedGallery(
                host,
                photos=photos,
                bind_thumb=self._bind_thumb,
                gen_fn=lambda m=mode: self._gen_by_mode[m],
                preview_scale=self._preview_scale,
            )
            self._pane_gallery[mode] = gallery
            self._gallery = gallery
            gallery.render(self._items)
        else:
            for c in range(COLS):
                host.columnconfigure(c, weight=1, uniform="col")
            for r in range(ROWS):
                host.rowconfigure(r, weight=1, uniform="row")
            thumb = self._thumb
            for i, item in enumerate(self._items):
                r, c = divmod(i, COLS)
                cell = ttk.Frame(host)
                cell.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
                box = tk.Frame(cell, width=thumb, height=thumb)
                box.pack_propagate(False)
                box.pack(expand=True)
                thumb_lbl = tk.Label(box, text="…", relief=tk.GROOVE, cursor="hand2")
                thumb_lbl.pack(fill=tk.BOTH, expand=True)
                name = (item.get("name") or f"#{item.get('id')}")[:22]
                owner = (item.get("owner") or "").strip()
                label_txt = f"{name}\n{owner}" if owner else name
                ttk.Label(
                    cell, text=label_txt, wraplength=max(60, thumb), justify=tk.CENTER
                ).pack()
                cid = int(item.get("id") or 0)
                post_url = (item.get("post_url") or "").strip()
                self._bind_thumb(thumb_lbl, cid, post_url)
                url = (item.get("preview_url") or "").strip()
                if url:
                    self._load_thumb(
                        thumb_lbl,
                        url,
                        self._gen_by_mode[mode],
                        post_url=post_url,
                        char_id=cid,
                    )
        self._pane_ids[mode] = tuple(
            int(it.get("id") or 0) for it in self._items
        )
        self._pane_query[mode] = self._query
        self._pane_page[mode] = self._page

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
            except Exception as exc:
                logger.debug("thumb decode failed: %s", exc)
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
        # Prefer dedicated dm_craft; fall back to open_omni for plain omni.
        if self._dm_craft is None:
            if action_id == "omni":
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
                        logger.debug("focus telegram failed", exc_info=True)
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

                        if focus_telegram():
                            self._on_log("Focused Telegram window")
                    except Exception:
                        logger.debug("focus telegram failed", exc_info=True)
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

    def _click_primary(self, char_id: int) -> None:
        # Legacy name — left click always opens the full image now.
        self._click_open_image(char_id)

    def _click_post(self, char_id: int) -> None:
        if char_id <= 0 or self._busy or self._post_grid is None:
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
