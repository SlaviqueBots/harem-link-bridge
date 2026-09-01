"""Paged roster: Undone / Done / Flavoured / Unflavoured / Sets / Taming / Market."""

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
FetchPageFn = Callable[..., None]
OpenOmniFn = Callable[[int, OkCb, ErrCb], None]
OpenOmniUiFn = Callable[[int], None]
PostGridFn = Callable[[int, OkCb, ErrCb], None]
RegisterCupFn = Callable[[int, OkCb, ErrCb], None]
DmCraftFn = Callable[[int, str, OkCb, ErrCb], None]
FocusPrefFn = Callable[[], bool]
ListSetsFn = Callable[[str, OkCb, ErrCb], None]
RenameSetFn = Callable[[str, str, OkCb, ErrCb], None]
DeleteSetFn = Callable[[str, OkCb, ErrCb], None]
AvoidSetFn = Callable[[str, bool, OkCb, ErrCb], None]
PresentSetFn = Callable[[str, OkCb, ErrCb], None]
TargetGetFn = Callable[[], str]
TargetSetFn = Callable[[str], None]
PreferOriginalFn = Callable[[], bool]
LeftClickOmniGetFn = Callable[[], bool]
LeftClickOmniSetFn = Callable[[bool], None]
LeftClickFlavourGetFn = Callable[[], bool]
LeftClickFlavourSetFn = Callable[[bool], None]
HideInAnySetGetFn = Callable[[], bool]
HideInAnySetSetFn = Callable[[bool], None]
TextGeoGetFn = Callable[[], str]
TextGeoSetFn = Callable[[str], None]
BrowseUsersFn = Callable[[str, OkCb, ErrCb], None]
FetchTamedFn = Callable[[int, str, OkCb, ErrCb], None]
FetchPrimedFn = Callable[[int, str, OkCb, ErrCb], None]
FetchMarketFn = Callable[..., None]
BuyMarketFn = Callable[[int, OkCb, ErrCb], None]
StatusVarFn = Callable[[], tk.StringVar | None]

_ROSTER_GRID_MODES = ("undone", "done", "flavoured", "unflavoured")
_TAB_MODES = (
    "undone",
    "done",
    "flavoured",
    "unflavoured",
    "sets",
    "tamed",
    "market",
)


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
    """Undone | Done | Flavoured | Unflavoured | Sets | Taming | Market."""

    def __init__(
        self,
        master,
        *,
        fetch_page: FetchPageFn,
        open_omni: OpenOmniFn,
        open_omni_ui: OpenOmniUiFn | None = None,
        post_grid: PostGridFn | None = None,
        register_cup: RegisterCupFn | None = None,
        dm_craft: DmCraftFn | None = None,
        list_sets: ListSetsFn | None = None,
        rename_set: RenameSetFn | None = None,
        delete_set: DeleteSetFn | None = None,
        avoid_set: AvoidSetFn | None = None,
        present_set: PresentSetFn | None = None,
        fetch_tamed: FetchTamedFn | None = None,
        fetch_primed: FetchPrimedFn | None = None,
        fetch_market: FetchMarketFn | None = None,
        buy_market: BuyMarketFn | None = None,
        should_focus_telegram: FocusPrefFn | None = None,
        get_post_target: TargetGetFn | None = None,
        set_post_target: TargetSetFn | None = None,
        prefer_original_open: PreferOriginalFn | None = None,
        get_left_click_omni: LeftClickOmniGetFn | None = None,
        set_left_click_omni: LeftClickOmniSetFn | None = None,
        get_left_click_flavour: LeftClickFlavourGetFn | None = None,
        set_left_click_flavour: LeftClickFlavourSetFn | None = None,
        get_hide_in_any_set: HideInAnySetGetFn | None = None,
        set_hide_in_any_set: HideInAnySetSetFn | None = None,
        status_var: tk.StringVar | None = None,
        get_text_edit_geometry: TextGeoGetFn | None = None,
        set_text_edit_geometry: TextGeoSetFn | None = None,
        fetch_browse_users: BrowseUsersFn | None = None,
        natural_thumbs: bool = False,
        preview_scale: float = 1.5,
        scroll_speed: float = 3.0,
        market_grid_view: bool = True,
        market_min_price: str = "",
        market_max_price: str = "",
        save_market_prices: Callable[[str, str], None] | None = None,
        full_image_get: Callable[[], bool] | None = None,
        full_image_set: Callable[[bool], None] | None = None,
        get_market_lot_geo: Callable[[], str] | None = None,
        set_market_lot_geo: Callable[[str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._fetch_page = fetch_page
        self._open_omni = open_omni
        self._open_omni_ui = open_omni_ui
        self._post_grid = post_grid
        self._register_cup = register_cup
        self._dm_craft = dm_craft
        self._list_sets = list_sets
        self._rename_set = rename_set
        self._delete_set = delete_set
        self._avoid_set = avoid_set
        self._present_set = present_set
        self._fetch_tamed = fetch_tamed
        self._fetch_primed = fetch_primed
        self._fetch_market = fetch_market
        self._buy_market = buy_market
        self._fetch_browse_users = fetch_browse_users
        self._should_focus = should_focus_telegram or (lambda: False)
        self._get_post_target = get_post_target or (lambda: "group")
        self._set_post_target = set_post_target
        self._prefer_original = prefer_original_open or (lambda: True)
        self._get_left_click_omni = get_left_click_omni or (lambda: False)
        self._set_left_click_omni = set_left_click_omni
        self._get_left_click_flavour = get_left_click_flavour or (lambda: True)
        self._set_left_click_flavour = set_left_click_flavour
        self._get_hide_in_any_set = get_hide_in_any_set or (lambda: False)
        self._set_hide_in_any_set = set_hide_in_any_set
        self._status_var = status_var
        self._get_text_geo = get_text_edit_geometry or (lambda: "")
        self._set_text_geo = set_text_edit_geometry
        self._natural_thumbs = bool(natural_thumbs)
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.5)))
        self._scroll_speed = max(0.25, min(6.0, float(scroll_speed or 3.0)))
        self._market_grid_view = bool(market_grid_view)
        self._market_min_price = (market_min_price or "").strip()
        self._market_max_price = (market_max_price or "").strip()
        self._save_market_prices = save_market_prices
        self._full_image_get = full_image_get or (lambda: False)
        self._full_image_set = full_image_set or (lambda _v: None)
        self._get_market_lot_geo = get_market_lot_geo or (lambda: "")
        self._set_market_lot_geo = set_market_lot_geo
        self._on_log = on_log or (lambda _s: None)
        self._pending_media: dict[int, dict[str, Any]] = {}
        self._page = 0
        self._total = 0
        self._page_size = PAGE_SIZE
        self._query = ""
        self._done = 0
        self._mode = "undone"  # see _TAB_MODES
        self._scope = "own"  # own | user | all (from server)
        self._items: list[dict[str, Any]] = []
        self._photos: list[Any] = []
        self._thumb = 140
        self._busy = False
        self._gen = 0
        self._gen_by_mode = {m: 0 for m in _ROSTER_GRID_MODES}
        self._search_after: str | None = None
        self._resize_after: str | None = None
        self._gallery = None
        self._pane_fr: dict[str, ttk.Frame] = {}
        self._pane_gallery: dict[str, Any] = {m: None for m in _ROSTER_GRID_MODES}
        self._pane_photos: dict[str, list[Any]] = {m: [] for m in _ROSTER_GRID_MODES}
        self._pane_ids: dict[str, tuple[int, ...]] = {
            m: () for m in _ROSTER_GRID_MODES
        }
        self._pane_query: dict[str, str] = {m: "" for m in _ROSTER_GRID_MODES}
        self._pane_page: dict[str, int] = {m: 0 for m in _ROSTER_GRID_MODES}
        # Instant tab switches: remember last roster_page bodies.
        self._page_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._max_page_cache = 8
        # Keep last N rendered pages alive so Prev/Next is instant.
        self._live_views: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._max_live_views = 8
        self._set_names: list[str] = []
        self._set_names_loaded = False

        self._tab_row = ttk.Frame(self)
        self._tab_row.pack(fill=tk.X)
        # Compact mode strip — no Notebook (empty pages stole a huge vertical gap).
        self._mode_var = tk.StringVar(value="undone")
        self._mode_btns: dict[str, ttk.Radiobutton] = {}
        mode_bar = ttk.Frame(self._tab_row)
        mode_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _labels = (
            ("undone", "Undone"),
            ("done", "Done"),
            ("flavoured", "Flavoured"),
            ("unflavoured", "Unflavoured"),
            ("sets", "Sets"),
            ("tamed", "Taming"),
            ("market", "Market"),
        )
        for mode, label in _labels:
            btn = ttk.Radiobutton(
                mode_bar,
                text=label,
                value=mode,
                variable=self._mode_var,
                command=self._on_mode_changed,
                style="Toolbutton",
            )
            btn.pack(side=tk.LEFT, padx=(0, 2))
            self._mode_btns[mode] = btn

        # Status + members sit on the mode-tab line (max grid height).
        if self._status_var is not None:
            ttk.Label(
                self._tab_row,
                textvariable=self._status_var,
                wraplength=280,
            ).pack(side=tk.RIGHT, padx=(8, 4))

        self._roster_browse = None
        if self._fetch_browse_users is not None:
            from link_bridge.member_browse import MemberBrowsePanel

            self._roster_browse = MemberBrowsePanel(
                self._tab_row,
                title="undone",
                unit="undone",
                fetch_users=lambda on_ok, on_err: self._fetch_browse_users(
                    self._members_browse_kind(), on_ok, on_err
                ),
                on_pick=self._browse_pick_member,
                on_log=self._on_log,
            )
            self._roster_browse.pack(side=tk.RIGHT, padx=(2, 2), pady=(1, 0))

            self._balance_slot = ttk.Frame(self._tab_row)
            self._balance_slot.pack(side=tk.RIGHT, padx=(4, 0))
        else:
            self._balance_slot = ttk.Frame(self._tab_row)
            self._balance_slot.pack(side=tk.RIGHT, padx=(4, 6))

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
                open_omni_ui=open_omni_ui,
                register_cup=register_cup,
                dm_craft=dm_craft,
                rename_set=rename_set,
                delete_set=delete_set,
                avoid_set=avoid_set,
                present_set=present_set,
                get_set_names=self._own_set_names,
                on_set_names=self._remember_set_names,
                should_focus_telegram=should_focus_telegram,
                get_post_target=self._get_post_target,
                set_post_target=self._on_target_from_child,
                prefer_original_open=self._prefer_original,
                get_text_edit_geometry=self._get_text_geo,
                set_text_edit_geometry=self._set_text_geo,
                fetch_browse_users=self._fetch_browse_users,
                natural_thumbs=self._natural_thumbs,
                preview_scale=self._preview_scale,
                on_log=on_log,
            )
            self._sets_panel.pack(fill=tk.BOTH, expand=True)

        self._tamed_body = ttk.Frame(self)
        self._tamed_sub_var = tk.StringVar(value="pairs")
        self._tamed_sub_row = ttk.Frame(self._tamed_body)
        self._tamed_sub_row.pack(fill=tk.X, pady=(4, 0))
        for val, label in (("pairs", "Tamed"), ("primed", "Primed")):
            ttk.Radiobutton(
                self._tamed_sub_row,
                text=label,
                value=val,
                variable=self._tamed_sub_var,
                command=self._on_tamed_sub_changed,
                style="Toolbutton",
            ).pack(side=tk.LEFT, padx=(0, 4))
        self._tamed_pairs_body = ttk.Frame(self._tamed_body)
        self._tamed_primed_body = ttk.Frame(self._tamed_body)
        self._tamed_panel = None
        self._primed_panel = None
        if fetch_tamed is not None and post_grid is not None:
            from link_bridge.tamed import TamedPanel

            self._tamed_panel = TamedPanel(
                self._tamed_pairs_body,
                fetch_tamed=fetch_tamed,
                post_grid=post_grid,
                open_omni=open_omni,
                open_omni_ui=open_omni_ui,
                register_cup=register_cup,
                dm_craft=dm_craft,
                should_focus_telegram=should_focus_telegram,
                get_post_target=self._get_post_target,
                set_post_target=self._on_target_from_child,
                get_left_click_omni=self._get_left_click_omni,
                set_left_click_omni=self._set_left_click_omni,
                prefer_original_open=self._prefer_original,
                get_text_edit_geometry=self._get_text_geo,
                set_text_edit_geometry=self._set_text_geo,
                fetch_browse_users=self._fetch_browse_users,
                preview_scale=self._preview_scale,
                get_set_names=self._own_set_names,
                on_set_names=self._remember_set_names,
                on_log=on_log,
            )
            self._tamed_panel.pack(fill=tk.BOTH, expand=True)
        if fetch_primed is not None and post_grid is not None:
            from link_bridge.primed import PrimedPanel

            self._primed_panel = PrimedPanel(
                self._tamed_primed_body,
                fetch_primed=fetch_primed,
                post_grid=post_grid,
                open_omni=open_omni,
                open_omni_ui=open_omni_ui,
                register_cup=register_cup,
                dm_craft=dm_craft,
                should_focus_telegram=should_focus_telegram,
                get_post_target=self._get_post_target,
                set_post_target=self._on_target_from_child,
                prefer_original_open=self._prefer_original,
                get_text_edit_geometry=self._get_text_geo,
                set_text_edit_geometry=self._set_text_geo,
                fetch_browse_users=self._fetch_browse_users,
                preview_scale=self._preview_scale,
                scroll_speed=self._scroll_speed,
                get_left_click_omni=self._get_left_click_omni,
                set_left_click_omni=self._set_left_click_omni,
                get_set_names=self._own_set_names,
                on_set_names=self._remember_set_names,
                on_log=on_log,
            )
            self._primed_panel.pack(fill=tk.BOTH, expand=True)
        self._show_tamed_sub("pairs")

        self._market_body = ttk.Frame(self)
        self._market_panel = None
        if fetch_market is not None and buy_market is not None:
            from link_bridge.market import MarketPanel

            self._market_panel = MarketPanel(
                self._market_body,
                fetch_page=fetch_market,
                buy_listing=buy_market,
                prefer_original_open=self._prefer_original,
                preview_scale=self._preview_scale,
                scroll_speed=self._scroll_speed,
                grid_view=self._market_grid_view,
                min_price=self._market_min_price,
                max_price=self._market_max_price,
                save_market_prices=self._save_market_prices,
                full_image_get=self._full_image_get,
                full_image_set=self._full_image_set,
                get_lot_window_geo=self._get_market_lot_geo,
                set_lot_window_geo=self._set_market_lot_geo,
                on_log=on_log,
            )
            self._market_panel.pack(fill=tk.BOTH, expand=True)

        self._set_nav(False)

    def set_market_grid_view(self, enabled: bool) -> None:
        flag = bool(enabled)
        if flag == self._market_grid_view:
            return
        self._market_grid_view = flag
        if self._market_panel is not None:
            self._market_panel.set_grid_view(flag)

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
        if self._primed_panel is not None:
            self._primed_panel.set_preview_scale(self._preview_scale)
        if self._market_panel is not None:
            self._market_panel.set_preview_scale(self._preview_scale)
        for g in self._pane_gallery.values():
            if g is not None:
                try:
                    g.set_preview_scale(self._preview_scale)
                except Exception:
                    pass
        for view in self._live_views.values():
            g = view.get("gallery")
            if g is not None:
                try:
                    g.set_preview_scale(self._preview_scale)
                except Exception:
                    pass

    def set_scroll_speed(self, speed: float) -> None:
        self._scroll_speed = max(0.25, min(6.0, float(speed or 3.0)))
        for g in self._pane_gallery.values():
            if g is not None and hasattr(g, "set_scroll_speed"):
                try:
                    g.set_scroll_speed(self._scroll_speed)
                except Exception:
                    pass
        if self._market_panel is not None and hasattr(self._market_panel, "set_scroll_speed"):
            try:
                self._market_panel.set_scroll_speed(self._scroll_speed)
            except Exception:
                pass
        if self._primed_panel is not None and hasattr(
            self._primed_panel, "set_scroll_speed"
        ):
            try:
                self._primed_panel.set_scroll_speed(self._scroll_speed)
            except Exception:
                pass

    def remove_char_from_view(self, char_id: int) -> None:
        """Pull a card out of the current page (e.g. Done while on Undone)."""
        cid = int(char_id)
        before = len(self._items)
        self._items = [it for it in self._items if int(it.get("id") or 0) != cid]
        if len(self._items) == before:
            return
        self._total = max(0, int(self._total) - 1)
        g = self._pane_gallery.get(self._roster_mode_key())
        if g is not None and hasattr(g, "remove_char") and g.remove_char(cid):
            shown = len(self._items)
            self.meta_var.set(
                f"{self._mode}: {shown} on page · {self._total} total"
            )
            return
        self._render_grid(reuse_bytes=True)

    def note_char_media(self, char_id: int, media: dict[str, Any]) -> None:
        """Remember preview fields from omni until the window closes."""
        cid = int(char_id)
        cur = dict(self._pending_media.get(cid) or {})
        for key in ("preview_url", "image_url", "file_url", "post_url", "name"):
            if key in media and media.get(key) is not None:
                cur[key] = media.get(key)
        self._pending_media[cid] = cur

    def flush_omni_media(self) -> None:
        """Apply pending omni preview updates to the visible grid (no reorder)."""
        pending = dict(self._pending_media)
        self._pending_media.clear()
        if not pending:
            return
        for cid, media in pending.items():
            self._apply_char_media(int(cid), media)
        # Drop stale page-cache entries that still have old URLs.
        stale_keys = []
        for key, body in self._page_cache.items():
            ids = set(_item_ids(body))
            if ids & set(pending.keys()):
                stale_keys.append(key)
        for key in stale_keys:
            self._page_cache.pop(key, None)

    def _apply_char_media(self, char_id: int, media: dict[str, Any]) -> None:
        cid = int(char_id)
        preview = str(media.get("preview_url") or "").strip()
        if not preview:
            return
        for it in self._items:
            if int(it.get("id") or 0) != cid:
                continue
            it["preview_url"] = preview
            for key in ("image_url", "file_url", "post_url", "name"):
                if media.get(key) is not None:
                    it[key] = media.get(key)
            break
        for mode, g in self._pane_gallery.items():
            if g is None or not hasattr(g, "update_char_preview"):
                continue
            try:
                g.update_char_preview(
                    cid,
                    preview_url=preview,
                    post_url=str(media.get("post_url") or "") or None,
                )
            except Exception:
                logger.debug("gallery preview update failed", exc_info=True)

    def _sync_gallery_previews(self, items: list[dict[str, Any]]) -> None:
        g = self._pane_gallery.get(self._roster_mode_key())
        if g is None or not hasattr(g, "update_char_preview"):
            return
        for it in items:
            cid = int(it.get("id") or 0)
            url = str(it.get("preview_url") or "").strip()
            if cid <= 0 or not url:
                continue
            try:
                g.update_char_preview(
                    cid,
                    preview_url=url,
                    post_url=str(it.get("post_url") or "") or None,
                    item=it,
                )
            except Exception:
                pass

    def _target_label(self) -> str:
        t = (self._get_post_target() or "group").strip().lower()
        return "Middle-click → DM" if t == "dm" else "Middle-click → Group"

    def _lmb_omni_label(self) -> str:
        return (
            "LMB → Omni: on"
            if self._get_left_click_omni()
            else "LMB → Omni: off"
        )

    def _lmb_flavour_label(self) -> str:
        return (
            "LMB → Flavour: on"
            if self._get_left_click_flavour()
            else "LMB → Flavour: off"
        )

    def _hide_in_any_set_label(self) -> str:
        return (
            "Hide in-set: on"
            if self._get_hide_in_any_set()
            else "Hide in-set: off"
        )

    def _visible_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._get_hide_in_any_set():
            return list(items)
        return [
            it
            for it in items
            if not str(it.get("set") or it.get("set_name") or "").strip()
        ]

    def _visible_ids(self, body: dict[str, Any]) -> tuple[int, ...]:
        out: list[int] = []
        for it in self._visible_items(list(body.get("items") or [])):
            try:
                out.append(int(it.get("id") or 0))
            except Exception:
                out.append(0)
        return tuple(out)

    def _toggle_left_click_omni(self) -> None:
        cur = bool(self._get_left_click_omni())
        nxt = not cur
        if self._set_left_click_omni is not None:
            self._set_left_click_omni(nxt)
        self.sync_lmb_omni_button()

    def _toggle_left_click_flavour(self) -> None:
        cur = bool(self._get_left_click_flavour())
        nxt = not cur
        if self._set_left_click_flavour is not None:
            self._set_left_click_flavour(nxt)
        else:
            self.sync_lmb_flavour_button()

    def sync_lmb_omni_button(self) -> None:
        try:
            self._lmb_omni_btn.configure(text=self._lmb_omni_label())
        except Exception:
            pass
        if self._primed_panel is not None and hasattr(
            self._primed_panel, "sync_lmb_omni_button"
        ):
            self._primed_panel.sync_lmb_omni_button()
        if self._tamed_panel is not None and hasattr(
            self._tamed_panel, "sync_lmb_omni_button"
        ):
            self._tamed_panel.sync_lmb_omni_button()

    def sync_lmb_flavour_button(self) -> None:
        try:
            self._lmb_flavour_btn.configure(text=self._lmb_flavour_label())
        except Exception:
            pass

    def _sync_flavour_toolbar(self) -> None:
        """LMB → Flavour toggle only on Flavoured / Unflavoured tabs."""
        try:
            btn = self._lmb_flavour_btn
        except AttributeError:
            return
        show = self._mode in ("flavoured", "unflavoured")
        if show:
            if not btn.winfo_ismapped():
                btn.pack(side=tk.LEFT, padx=(6, 0))
            self.sync_lmb_flavour_button()
        else:
            btn.pack_forget()

    def _toggle_hide_in_any_set(self) -> None:
        cur = bool(self._get_hide_in_any_set())
        nxt = not cur
        if self._set_hide_in_any_set is not None:
            self._set_hide_in_any_set(nxt)
        self.sync_hide_in_any_set_button()
        if self._mode in _ROSTER_GRID_MODES:
            # Force gallery rebuild with the new filter.
            mode = self._roster_mode_key()
            self._pane_ids[mode] = ()
            self.load_page(self._page)

    def sync_hide_in_any_set_button(self) -> None:
        try:
            self._hide_in_set_btn.configure(text=self._hide_in_any_set_label())
        except Exception:
            pass

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
        if self._primed_panel is not None and hasattr(
            self._primed_panel, "sync_target_button"
        ):
            self._primed_panel.sync_target_button()

    def _own_set_names(self) -> list[str]:
        return list(self._set_names)

    def _remember_set_names(self, names: list[str]) -> None:
        self._set_names = [str(x).strip() for x in names if str(x).strip()]
        self._set_names_loaded = True

    def _note_set_used(self, name: str) -> None:
        n = " ".join((name or "").split())
        if not n:
            return
        key = n.casefold()
        if any(x.casefold() == key for x in self._set_names):
            return
        self._set_names.append(n)

    def _prefetch_set_names(self) -> None:
        if self._list_sets is None or self._set_names_loaded:
            return

        def on_ok(body: dict) -> None:
            if body.get("op") == "sets_list_ok":
                self._remember_set_names(list(body.get("sets") or []))

        self._list_sets("", on_ok, lambda _e: None)

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

    def _build_roster_chrome(self, parent: ttk.Frame) -> None:
        search_row = ttk.Frame(parent)
        search_row.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(search_row, text="Search").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.search_entry.bind("<Return>", lambda _e: self._search_now())
        self._ignore_search_trace = False
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
        bar.pack(fill=tk.X, pady=(2, 0))
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
        self._lmb_omni_btn = ttk.Button(
            bar, text=self._lmb_omni_label(), command=self._toggle_left_click_omni
        )
        self._lmb_omni_btn.pack(side=tk.LEFT, padx=(6, 0))
        self._lmb_flavour_btn = ttk.Button(
            bar, text=self._lmb_flavour_label(), command=self._toggle_left_click_flavour
        )
        self._hide_in_set_btn = ttk.Button(
            bar,
            text=self._hide_in_any_set_label(),
            command=self._toggle_hide_in_any_set,
        )
        self._hide_in_set_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.meta_var = tk.StringVar(value="Connect to load roster.")
        ttk.Label(bar, textvariable=self.meta_var).pack(side=tk.LEFT, padx=(12, 0))

        self.grid_fr = ttk.Frame(parent)
        self.grid_fr.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        for mode in _ROSTER_GRID_MODES:
            fr = ttk.Frame(self.grid_fr)
            self._pane_fr[mode] = fr
        self._pane_fr["undone"].pack(fill=tk.BOTH, expand=True)
        self.grid_fr.bind("<Configure>", self._on_grid_resize)
        self._sync_flavour_toolbar()

    def _browse_pick_roster(self, username: str) -> None:
        """Load this user's roster immediately (Search field is just a mirror)."""
        uname = (username or "").strip().lstrip("@")
        q = f"@{uname}" if uname else ""
        self._ignore_search_trace = True
        try:
            self.search_var.set(q)
        finally:
            self._ignore_search_trace = False
        self._cancel_search_timer()
        self._query = q
        self.load_page(0)

    def _browse_pick_member(self, username: str) -> None:
        if self._mode == "sets":
            if self._sets_panel is not None:
                self._sets_panel._browse_pick_user(username)
            return
        if self._mode == "tamed":
            if self._tamed_sub_var.get() == "primed":
                if self._primed_panel is not None:
                    self._primed_panel._browse_pick_user(username)
            elif self._tamed_panel is not None:
                self._tamed_panel._browse_pick_user(username)
            return
        if self._mode == "market":
            return
        self._browse_pick_roster(username)

    def _members_browse_kind(self) -> str:
        if self._mode == "sets":
            return "sets"
        if self._mode == "tamed":
            if self._tamed_sub_var.get() == "primed":
                return "primed"
            return "tamed"
        if self._mode == "flavoured":
            return "roster_flavoured"
        if self._mode == "unflavoured":
            return "roster_unflavoured"
        if self._mode == "done":
            return "roster_done"
        return "roster_undone"

    def _sync_roster_browse_labels(self) -> None:
        panel = getattr(self, "_roster_browse", None)
        if panel is None:
            return
        if self._mode == "sets":
            panel.set_labels(title="sets", unit="sets")
        elif self._mode == "tamed":
            if self._tamed_sub_var.get() == "primed":
                panel.set_labels(title="primed", unit="primed")
            else:
                panel.set_labels(title="tamed", unit="tamed")
        elif self._mode == "market":
            panel.set_labels(title="market", unit="lots")
        elif self._mode == "flavoured":
            panel.set_labels(title="flavoured", unit="flavoured")
        elif self._mode == "unflavoured":
            panel.set_labels(title="unflavoured", unit="unflavoured")
        elif self._mode == "done":
            panel.set_labels(title="done", unit="done")
        else:
            panel.set_labels(title="undone", unit="undone")
        if self._mode == "market":
            panel.clear_cache()
            panel.close()
            return
        if getattr(panel, "_expanded", False):
            panel.reload()
        else:
            panel.clear_cache()
            panel.close()

    def _roster_mode_key(self) -> str:
        if self._mode in _ROSTER_GRID_MODES:
            return self._mode
        return "undone"

    def _roster_fetch_kind(self) -> str:
        if self._mode == "flavoured":
            return "roster_flavoured"
        if self._mode == "unflavoured":
            return "roster_unflavoured"
        return ""

    def _roster_done_arg(self) -> int:
        if self._mode in ("flavoured", "unflavoured"):
            return -1
        return 1 if self._mode == "done" else 0

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

    def mount_balance_chip(self, chip: tk.Misc) -> None:
        """Top-right on the mode tab row — no grid height consumed."""
        chip.pack(in_=self._balance_slot, side=tk.RIGHT)

    def _shrink_tab_bar(self) -> None:
        return

    def _on_mode_changed(self) -> None:
        mode = str(self._mode_var.get() or "undone")
        if mode not in _TAB_MODES:
            mode = "undone"
            self._mode_var.set(mode)
        self._mode = mode
        if mode == "sets":
            self._show_sets_mode()
            self._sync_roster_browse_labels()
            return
        if mode == "tamed":
            self._show_tamed_mode()
            self._sync_roster_browse_labels()
            return
        if mode == "market":
            self._show_market_mode()
            self._sync_roster_browse_labels()
            return
        self._done = self._roster_done_arg()
        self._show_roster_mode()
        self._sync_flavour_toolbar()
        self._sync_roster_browse_labels()
        self.load_page(0)

    def _on_tab_changed(self, _event=None) -> None:
        # Back-compat alias — mode strip uses _on_mode_changed.
        self._on_mode_changed()

    def _show_roster_mode(self) -> None:
        self._sets_body.pack_forget()
        self._tamed_body.pack_forget()
        self._market_body.pack_forget()
        if not self._roster_body.winfo_ismapped():
            self._roster_body.pack(fill=tk.BOTH, expand=True)

    def _show_sets_mode(self) -> None:
        self._roster_body.pack_forget()
        self._tamed_body.pack_forget()
        self._market_body.pack_forget()
        if not self._sets_body.winfo_ismapped():
            self._sets_body.pack(fill=tk.BOTH, expand=True)
        if self._sets_panel is not None:
            self._sets_panel.refresh_sets()

    def _show_tamed_sub(self, which: str | None = None) -> None:
        sub = (which or self._tamed_sub_var.get() or "pairs").strip()
        if sub not in ("pairs", "primed"):
            sub = "pairs"
        self._tamed_sub_var.set(sub)
        if sub == "primed":
            self._tamed_pairs_body.pack_forget()
            if not self._tamed_primed_body.winfo_ismapped():
                self._tamed_primed_body.pack(fill=tk.BOTH, expand=True)
        else:
            self._tamed_primed_body.pack_forget()
            if not self._tamed_pairs_body.winfo_ismapped():
                self._tamed_pairs_body.pack(fill=tk.BOTH, expand=True)

    def _on_tamed_sub_changed(self) -> None:
        self._show_tamed_sub()
        self._sync_roster_browse_labels()
        if self._tamed_sub_var.get() == "primed":
            if self._primed_panel is not None:
                if not self._primed_panel.has_cached_view():
                    self._primed_panel.load_page(0)
        elif self._tamed_panel is not None:
            self._tamed_panel.load_page(0)

    def _show_tamed_mode(self) -> None:
        self._roster_body.pack_forget()
        self._sets_body.pack_forget()
        self._market_body.pack_forget()
        if not self._tamed_body.winfo_ismapped():
            self._tamed_body.pack(fill=tk.BOTH, expand=True)
        self._show_tamed_sub()
        if self._tamed_sub_var.get() == "primed":
            if self._primed_panel is not None:
                if not self._primed_panel.has_cached_view():
                    self._primed_panel.load_page(0)
        elif self._tamed_panel is not None:
            self._tamed_panel.load_page(0)

    def _show_market_mode(self) -> None:
        self._roster_body.pack_forget()
        self._sets_body.pack_forget()
        self._tamed_body.pack_forget()
        if not self._market_body.winfo_ismapped():
            self._market_body.pack(fill=tk.BOTH, expand=True)
        if self._market_panel is not None:
            try:
                self._market_panel._claim_wheel()
            except Exception:
                pass
            if not self._market_panel.has_cached_view():
                self._market_panel.load_page(0)

    def _on_grid_resize(self, _event=None) -> None:
        if self._resize_after is not None:
            try:
                self.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.after(RESIZE_DEBOUNCE_MS, self._apply_resize)

    def _apply_resize(self) -> None:
        self._resize_after = None
        if self._mode not in _ROSTER_GRID_MODES or not self._items:
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
        if getattr(self, "_ignore_search_trace", False):
            return
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
            if self._tamed_sub_var.get() == "primed":
                if self._primed_panel is not None:
                    self._primed_panel.refresh()
            elif self._tamed_panel is not None:
                self._tamed_panel.refresh()
            return
        if self._mode == "market":
            if self._market_panel is not None:
                self._market_panel.refresh()
            return
        self._query = (self.search_var.get() or "").strip()
        # Bust page cache so Refresh actually picks up new preview URLs.
        mode = self._roster_mode_key()
        cache_key = (mode, (self._query or "").strip().lower(), int(self._page))
        self._page_cache.pop(cache_key, None)
        self.load_page(self._page)

    def prev_page(self) -> None:
        if self._page > 0:
            self.load_page(self._page - 1)

    def next_page(self) -> None:
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        if self._page + 1 < pages:
            self.load_page(self._page + 1)

    def load_page(self, page: int = 0) -> None:
        if self._mode not in _ROSTER_GRID_MODES:
            return
        mode = self._roster_mode_key()
        self._show_pane(mode)
        self._busy = True
        self._gen_by_mode[mode] += 1
        gen = self._gen_by_mode[mode]
        self._gen = gen
        self._prefetch_set_names()
        q = self._query
        done = self._roster_done_arg()
        kind = self._roster_fetch_kind()
        label = mode
        hint = f" “{q}”" if q else ""
        cache_key = (mode, (q or "").strip().lower(), int(page))
        cached = self._page_cache.get(cache_key)
        if cached is not None:
            ids = self._visible_ids(cached)
            same_view = (
                self._pane_gallery.get(mode) is not None
                and self._pane_ids.get(mode) == ids
                and self._pane_query.get(mode) == q
                and self._pane_page.get(mode) == int(page)
            )
            if same_view:
                # Keep live widgets — no blank flash.
                self._items = self._visible_items(list(cached.get("items") or []))
                self._apply_roster_meta(cached, kind=label, q=q)
                self._busy = False
                self._set_nav(True)
            else:
                self._apply_roster_body(cached, gen, kind=label, q=q, from_cache=True)
        else:
            self.meta_var.set(f"Loading {label} page {page + 1}{hint}…")
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
            ids = self._visible_ids(body)
            if (
                self._pane_gallery.get(mode) is not None
                and self._pane_ids.get(mode) == ids
                and self._pane_query.get(mode) == q
                and self._pane_page.get(mode) == int(body.get("page") or page)
            ):
                self._items = self._visible_items(list(body.get("items") or []))
                self._apply_roster_meta(body, kind=label, q=q)
                self._sync_gallery_previews(self._items)
                self._set_nav(True)
                return
            self._apply_roster_body(body, gen, kind=label, q=q, from_cache=False)

        def on_err(exc: BaseException) -> None:
            self._busy = False
            if gen != self._gen_by_mode.get(mode, -1):
                return
            if cached is None:
                self.meta_var.set(f"Roster error: {exc}")
            self._on_log(f"Roster error: {exc}")
            self._set_nav(True)

        self._fetch_page(int(page), q, done, "", on_ok, on_err, kind=kind)

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
        hide_bit = " · hide in-set" if self._get_hide_in_any_set() else ""
        self.meta_var.set(
            f"{kind.capitalize()}{scope_bit} · page {self._page + 1}/{pages} · "
            f"{self._total} cards{q_bit}{hide_bit}"
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
        self._items = self._visible_items(list(body.get("items") or []))
        self._thumb = compute_thumb(
            max(1, self.grid_fr.winfo_width()),
            max(1, self.grid_fr.winfo_height()),
        )
        self._render_grid(reuse_bytes=False)
        self._set_nav(True)
        if from_cache:
            self._busy = False

    def clear(self) -> None:
        for mode in _ROSTER_GRID_MODES:
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
        if self._primed_panel is not None:
            self._primed_panel.clear()

    def _view_key(
        self, mode: str, ids: tuple[int, ...]
    ) -> tuple[Any, ...]:
        return (mode, (self._query or "").strip().lower(), int(self._page), ids)

    def _hide_mode_views(self, mode: str) -> None:
        for key, view in self._live_views.items():
            if key[0] != mode:
                continue
            host = view.get("host")
            if host is None:
                continue
            try:
                host.pack_forget()
            except Exception:
                pass

    def _destroy_live_view(self, view: dict[str, Any]) -> None:
        g = view.get("gallery")
        if g is not None:
            try:
                g.destroy()
            except Exception:
                pass
        release_photos(view.get("photos") or [])
        host = view.get("host")
        if host is not None:
            try:
                host.destroy()
            except Exception:
                pass

    def _evict_live_views(self) -> None:
        while len(self._live_views) > self._max_live_views:
            _key, view = self._live_views.popitem(last=False)
            self._destroy_live_view(view)

    def _drop_live_views(self, mode: str) -> None:
        drop = [k for k in list(self._live_views) if k[0] == mode]
        for key in drop:
            self._destroy_live_view(self._live_views.pop(key))

    def _clear_pane(self, mode: str) -> None:
        self._drop_live_views(mode)
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
        for mode in _ROSTER_GRID_MODES:
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
        ids = tuple(int(it.get("id") or 0) for it in self._items)
        if self._natural_thumbs:
            key = self._view_key(mode, ids)
            self._hide_mode_views(mode)
            hit = self._live_views.get(key)
            if hit is not None:
                self._live_views.move_to_end(key)
                try:
                    hit["host"].pack(fill=tk.BOTH, expand=True)
                except Exception:
                    pass
                self._gallery = hit["gallery"]
                self._photos = hit["photos"]
                self._pane_gallery[mode] = hit["gallery"]
                self._pane_photos[mode] = hit["photos"]
                self._pane_ids[mode] = ids
                self._pane_query[mode] = self._query
                self._pane_page[mode] = self._page
                g = hit["gallery"]
                if g is not None and hasattr(g, "retry_missing"):
                    try:
                        g.retry_missing()
                    except Exception:
                        pass
                return
            host = ttk.Frame(self._pane_fr[mode])
            host.pack(fill=tk.BOTH, expand=True)
            photos: list[Any] = []
            from link_bridge.gallery import JustifiedGallery

            gallery = JustifiedGallery(
                host,
                photos=photos,
                bind_thumb=self._bind_thumb,
                gen_fn=lambda: 0,
                preview_scale=self._preview_scale,
                scroll_speed=self._scroll_speed,
            )
            self._live_views[key] = {
                "host": host,
                "gallery": gallery,
                "photos": photos,
            }
            self._evict_live_views()
            self._pane_gallery[mode] = gallery
            self._pane_photos[mode] = photos
            self._gallery = gallery
            self._photos = photos
            gallery.render(self._items)
            self._pane_ids[mode] = ids
            self._pane_query[mode] = self._query
            self._pane_page[mode] = self._page
            return
        self._clear_pane(mode)
        host = self._pane_fr[mode]
        photos = self._pane_photos[mode]
        self._photos = photos
        for c in range(COLS):
            host.columnconfigure(c, weight=1, uniform="col")
        for r in range(ROWS):
            host.rowconfigure(r, weight=1, uniform="row")
        thumb = self._thumb
        for i, item in enumerate(self._items):
            r, c = divmod(i, COLS)
            cell = ttk.Frame(host)
            cell.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
            box = tk.Frame(cell, width=thumb, height=thumb, bd=0, highlightthickness=0)
            box.pack_propagate(False)
            box.pack(expand=True)
            from link_bridge.theme import surface_for

            surf = surface_for(self).get("canvas", "#1e1f22")
            box.configure(bg=surf)
            thumb_lbl = tk.Label(
                box,
                text="…",
                relief=tk.FLAT,
                cursor="hand2",
                bd=0,
                highlightthickness=0,
                bg=surf,
                fg=surface_for(self).get("muted", "#b5bac1"),
            )
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
                label.configure(text="×")
                label.bind(
                    "<Button-1>",
                    lambda _e: self._retry_square_thumb(
                        label, url, gen, post_url=post_url, char_id=char_id
                    ),
                )

            try:
                label.after(0, fail)
            except Exception:
                pass

        schedule_thumb_fetch(url, on_data=on_data, on_err=on_err)

    def _retry_square_thumb(
        self,
        label: tk.Label,
        url: str,
        gen: int,
        *,
        post_url: str = "",
        char_id: int = 0,
    ) -> None:
        if gen != self._gen or not label.winfo_exists():
            return
        label.configure(text="…", image="")
        self._load_thumb(
            label, url, gen, post_url=post_url, char_id=char_id
        )

    def _bind_thumb(self, label: tk.Label, char_id: int, post_url: str) -> None:
        label.bind("<Button-1>", lambda _e, x=char_id: self._click_primary(x))
        label.bind("<Button-2>", lambda _e, x=char_id: self._click_post(x))
        label.bind(
            "<Button-3>",
            lambda e, x=char_id, u=post_url: self._thumb_context_menu(e, x, u),
        )

    def apply_silent_craft(self, char_id: int, craft: str) -> None:
        from link_bridge.thumb_menu import apply_silent_craft_item

        apply_silent_craft_item(self._item_by_id(int(char_id)), str(craft or ""))

    def _item_by_id(self, char_id: int) -> dict[str, Any] | None:
        for it in self._items:
            try:
                if int(it.get("id") or 0) == int(char_id):
                    return it
            except Exception:
                continue
        return None

    def _open_url_for_item(self, item: dict[str, Any], *, side: str = "") -> str:
        prefer = bool(self._prefer_original())
        if side == "before":
            file_u = (item.get("before_file_url") or "").strip()
            img_u = (item.get("before_image_url") or "").strip()
            prev_u = (item.get("before_preview_url") or "").strip()
        elif side == "after":
            file_u = (item.get("after_file_url") or "").strip()
            img_u = (
                (item.get("after_image_url") or "").strip()
                or (item.get("image_url") or "").strip()
            )
            prev_u = (
                (item.get("after_preview_url") or "").strip()
                or (item.get("preview_url") or "").strip()
            )
        else:
            file_u = (item.get("file_url") or "").strip()
            img_u = (item.get("image_url") or "").strip()
            prev_u = (item.get("preview_url") or "").strip()
        if prefer:
            return file_u or img_u or prev_u
        return img_u or file_u or prev_u

    def _click_open_image(self, char_id: int) -> None:
        from link_bridge.open_image import open_full_image

        item = self._item_by_id(char_id) or {}
        url = self._open_url_for_item(item)
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
        name = str(item.get("name") or "").strip()
        if name:
            self.meta_var.set(f"#{char_id} · {name}")
        else:
            self.meta_var.set(f"#{char_id}")
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
            on_edit_flavour=self._edit_flavour,
            on_edit_note=self._edit_note,
            can_tame=bool(item.get("can_tame")),
            is_tamed=bool(item.get("tamed")),
            has_checkpoint=bool(item.get("has_checkpoint")),
            checkpoint_image_url=str(item.get("checkpoint_image_url") or ""),
            char_name=name,
            set_names=self._own_set_names(),
            current_set=str(item.get("set") or ""),
            on_add_to_set=self._add_to_set,
            on_new_set=self._add_to_new_set,
            on_remove_from_set=self._remove_from_set,
            can_edit_sets=bool(item.get("mine", True)),
            can_cycle_name=bool(item.get("can_cycle_name")),
            is_done=bool(item.get("done")),
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
        if action_id == "omni" and self._open_omni_ui is not None:
            self._open_omni_ui(int(char_id))
            return
        if action_id == "omni_dm":
            # Telegram DM omnicraft (legacy), not the in-client panel.
            action_id = "omni"
        if action_id == "mi_omni":
            open_omni_after_mirror = True
            action_id = "mi"
        else:
            open_omni_after_mirror = False
        # Prefer dedicated dm_craft; fall back to open_omni for plain omni.
        if self._dm_craft is None:
            if action_id == "omni":
                self._click_omni(char_id)
            else:
                self.meta_var.set(f"Craft “{action_id}” needs a connected update")
            return
        self._busy = True
        label = action_id if action_id != "omni" else "Omnicraft (DM)"
        self.meta_var.set(f"DM craft #{char_id}: {label}…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "dm_craft_ok":
                detail = str(body.get("detail") or "ok").strip()
                silent = bool(body.get("silent"))
                notice = detail if detail and detail != "ok" else f"{label} ✓"
                self.meta_var.set(f"#{char_id}: {notice}")
                self._on_log(f"Craft {action_id} char {char_id}: {notice}")
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
                        if self._get_hide_in_any_set():
                            self.remove_char_from_view(int(char_id))
                    if action_id in ("tr", "ptr"):
                        self.remove_char_from_view(int(char_id))
                    elif action_id == "dn" and self._mode == "undone":
                        self.remove_char_from_view(int(char_id))
                    elif action_id == "ud" and self._mode == "done":
                        self.remove_char_from_view(int(char_id))
                elif self._should_focus():
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
        if (
            self._mode in ("flavoured", "unflavoured")
            and self._get_left_click_flavour()
        ):
            self._edit_flavour(char_id)
            return
        if self._get_left_click_omni() and self._open_omni_ui is not None:
            self._open_omni_ui(int(char_id))
            return
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
            msg = str(exc or "failed")
            if "disconnect" in msg.lower() or "not connected" in msg.lower():
                self.meta_var.set(
                    f"Post failed: disconnected (close other Link Bridge copies)"
                )
            else:
                self.meta_var.set(f"Post failed: {msg}")
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
