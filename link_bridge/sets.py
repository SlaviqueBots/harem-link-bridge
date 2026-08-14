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
ListSetsFn = Callable[[str, OkCb, ErrCb], None]
FetchPageFn = Callable[[int, str, int, str, OkCb, ErrCb], None]
PostGridFn = Callable[[int, OkCb, ErrCb], None]
OpenOmniFn = Callable[[int, OkCb, ErrCb], None]
RegisterCupFn = Callable[[int, OkCb, ErrCb], None]
DmCraftFn = Callable[[int, str, OkCb, ErrCb], None]
RenameSetFn = Callable[[str, str, OkCb, ErrCb], None]
DeleteSetFn = Callable[[str, OkCb, ErrCb], None]
GetSetNamesFn = Callable[[], list[str]]
OnSetNamesFn = Callable[[list[str]], None]
FocusPrefFn = Callable[[], bool]
TargetGetFn = Callable[[], str]
TargetSetFn = Callable[[str], None]
BrowseUsersFn = Callable[[str, OkCb, ErrCb], None]


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
        rename_set: RenameSetFn | None = None,
        delete_set: DeleteSetFn | None = None,
        get_set_names: GetSetNamesFn | None = None,
        on_set_names: OnSetNamesFn | None = None,
        should_focus_telegram: FocusPrefFn | None = None,
        get_post_target: TargetGetFn | None = None,
        set_post_target: TargetSetFn | None = None,
        prefer_original_open: Callable[[], bool] | None = None,
        get_text_edit_geometry: Callable[[], str] | None = None,
        set_text_edit_geometry: Callable[[str], None] | None = None,
        fetch_browse_users: BrowseUsersFn | None = None,
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
        self._rename_set = rename_set
        self._delete_set = delete_set
        self._get_set_names = get_set_names
        self._on_set_names = on_set_names
        self._should_focus = should_focus_telegram or (lambda: False)
        self._get_post_target = get_post_target or (lambda: "group")
        self._set_post_target = set_post_target
        self._prefer_original = prefer_original_open or (lambda: True)
        self._get_text_geo = get_text_edit_geometry or (lambda: "")
        self._set_text_geo = set_text_edit_geometry
        self._fetch_browse_users = fetch_browse_users
        self._natural_thumbs = bool(natural_thumbs)
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.5)))
        self._on_log = on_log or (lambda _s: None)
        self._selected = ""
        self._whose = ""
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
        self._pending_delete = ""

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
        self._rename_btn = ttk.Button(head, text="Rename", command=self._rename_selected)
        self._rename_btn.pack(side=tk.RIGHT, padx=(0, 6))
        self._member_browse = None

        foot = ttk.Frame(left)
        foot.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        self._delete_idle = ttk.Frame(foot)
        self._delete_idle.pack(fill=tk.X)
        self._delete_btn = ttk.Button(
            self._delete_idle, text="Delete set", command=self._arm_delete
        )
        self._delete_btn.pack(anchor=tk.W)
        self._delete_confirm = ttk.Frame(foot)
        self._delete_hint = tk.StringVar(value="")
        ttk.Label(
            self._delete_confirm,
            textvariable=self._delete_hint,
            wraplength=180,
        ).pack(anchor=tk.W)
        conf_row = ttk.Frame(self._delete_confirm)
        conf_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(conf_row, text="Cancel", command=self._disarm_delete).pack(
            side=tk.LEFT
        )
        self._delete_confirm_btn = ttk.Button(
            conf_row, text="Confirm delete", command=self._confirm_delete
        )
        self._delete_confirm_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._list = tk.Listbox(left, exportselection=False)
        self._list.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self._list.bind("<<ListboxSelect>>", self._on_select)
        self._list.bind("<Button-3>", self._popup_set_list_menu)
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
        self._sync_rename_button()
        self._disarm_delete()

    def _normalize_whose(self, raw: str) -> str:
        return (raw or "").strip().lstrip("@").strip()

    def _browse_pick_user(self, username: str) -> None:
        self._whose = self._normalize_whose(username)
        self.refresh_sets()

    def _owner_q(self) -> str:
        if self._whose:
            return f"@{self._whose}"
        return ""

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
        self._disarm_delete()
        self._whose = self._normalize_whose(self._whose)
        whose_bit = f" @{self._whose}" if self._whose else ""
        self.meta_var.set(f"Loading sets{whose_bit}…")

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
            scope = str(body.get("scope") or ("user" if self._whose else "own"))
            owner = str(body.get("user") or self._whose or "").strip()
            if scope != "user" and self._on_set_names is not None:
                self._on_set_names(list(self._names))
            self._sync_rename_button()
            if not self._names:
                self._selected = ""
                self.clear_grid()
                if owner:
                    self.meta_var.set(f"@{owner}: no sets.")
                else:
                    self.meta_var.set("No sets yet.")
                self._sync_rename_button()
                return
            label = f"@{owner}" if owner and scope == "user" else "Your sets"
            if self._selected in self._names:
                idx = self._names.index(self._selected)
                self._list.selection_set(idx)
                self._list.see(idx)
                self._open_set(self._selected)
            else:
                self._list.selection_set(0)
                self._open_set(self._names[0])
            self._on_log(f"{label}: {len(self._names)} sets")

        def on_err(exc: BaseException) -> None:
            self.meta_var.set(f"Sets error: {exc}")
            self._on_log(f"Sets error: {exc}")

        self._list_sets(self._whose, on_ok, on_err)

    def _can_rename_sets(self) -> bool:
        return (not self._whose) and self._rename_set is not None

    def _sync_rename_button(self) -> None:
        try:
            state = (
                tk.NORMAL
                if self._can_rename_sets() and bool(self._names)
                else tk.DISABLED
            )
            self._rename_btn.configure(state=state)
        except Exception:
            pass
        self._sync_delete_button()

    def _can_delete_sets(self) -> bool:
        return (not self._whose) and self._delete_set is not None

    def _sync_delete_button(self) -> None:
        if self._pending_delete:
            return
        try:
            state = (
                tk.NORMAL
                if self._can_delete_sets() and bool(self._names)
                else tk.DISABLED
            )
            self._delete_btn.configure(state=state)
        except Exception:
            pass

    def _arm_delete(self) -> None:
        if not self._can_delete_sets() or self._busy:
            return
        name = (self._selected or "").strip()
        if not name and self._list.curselection():
            idx = int(self._list.curselection()[0])
            if 0 <= idx < len(self._names):
                name = self._names[idx]
        if not name:
            self.meta_var.set("Pick a set to delete.")
            return
        self._pending_delete = name
        self._delete_hint.set(
            f"Delete “{name}”? Cards stay; only the set is removed."
        )
        try:
            self._delete_confirm_btn.configure(text=f"Confirm delete “{name}”")
        except Exception:
            pass
        try:
            self._delete_idle.pack_forget()
            self._delete_confirm.pack(fill=tk.X)
        except Exception:
            pass

    def _disarm_delete(self) -> None:
        self._pending_delete = ""
        try:
            self._delete_confirm.pack_forget()
            self._delete_idle.pack(fill=tk.X)
        except Exception:
            pass
        self._sync_delete_button()

    def _confirm_delete(self) -> None:
        name = (self._pending_delete or "").strip()
        delete = self._delete_set
        if not name or delete is None or self._busy:
            self._disarm_delete()
            return
        self._busy = True
        self.meta_var.set(f"Deleting set “{name}”…")

        def on_ok(body: dict) -> None:
            self._busy = False
            self._disarm_delete()
            if body.get("op") == "sets_delete_ok":
                gone = str(body.get("name") or name).strip() or name
                count = int(body.get("count") or 0)
                self._names = [
                    x for x in self._names if x.casefold() != gone.casefold()
                ]
                self._list.delete(0, tk.END)
                for nm in self._names:
                    self._list.insert(tk.END, nm)
                if self._on_set_names is not None:
                    self._on_set_names(list(self._names))
                self.meta_var.set(f"Deleted “{gone}” · {count} cards kept")
                self._on_log(f"Deleted set “{gone}” ({count} cards kept)")
                if gone.casefold() == (self._selected or "").casefold():
                    self._selected = ""
                if self._names:
                    nxt = self._names[0]
                    self._list.selection_set(0)
                    self._open_set(nxt)
                else:
                    self.clear_grid()
                    self.meta_var.set("No sets yet.")
            else:
                self.meta_var.set(
                    f"Delete failed: {body.get('error') or 'failed'}"
                )
            self._sync_rename_button()

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self._disarm_delete()
            self.meta_var.set(f"Delete failed: {exc}")
            self._sync_rename_button()

        delete(name, on_ok, on_err)

    def _popup_set_list_menu(self, event) -> None:
        if not self._can_rename_sets() or not self._names:
            return
        idx = self._list.nearest(event.y)
        if idx < 0 or idx >= len(self._names):
            return
        self._list.selection_clear(0, tk.END)
        self._list.selection_set(idx)
        self._list.activate(idx)
        menu = tk.Menu(self._list, tearoff=0)
        menu.add_command(label="Rename…", command=self._rename_selected)
        try:
            menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _rename_selected(self) -> None:
        if not self._can_rename_sets():
            return
        sel = self._list.curselection()
        if sel:
            idx = int(sel[0])
        elif self._selected in self._names:
            idx = self._names.index(self._selected)
        else:
            return
        if idx < 0 or idx >= len(self._names):
            return
        old = self._names[idx]
        from link_bridge.text_edit_dialog import ask_set_name

        new = ask_set_name(
            self,
            title=f"Rename set “{old}”",
            initial=old,
            geometry=self._get_text_geo(),
            on_geometry=self._set_text_geo,
        )
        if new is None or new.strip() == old:
            return
        self._busy = True
        self.meta_var.set(f"Renaming “{old}”…")

        def on_ok(body: dict) -> None:
            self._busy = False
            if body.get("op") == "sets_rename_ok":
                renamed = str(body.get("new") or new).strip() or new
                count = int(body.get("count") or 0)
                self._names = [
                    renamed if x.casefold() == old.casefold() else x
                    for x in self._names
                ]
                self._list.delete(0, tk.END)
                for name in self._names:
                    self._list.insert(tk.END, name)
                self._selected = renamed
                if renamed in self._names:
                    i = self._names.index(renamed)
                    self._list.selection_set(i)
                    self._list.see(i)
                if self._on_set_names is not None:
                    self._on_set_names(list(self._names))
                self.meta_var.set(f"Renamed to “{renamed}” · {count} cards")
                self._on_log(f"Renamed set “{old}” → “{renamed}” ({count})")
                self._open_set(renamed)
            else:
                self.meta_var.set(
                    f"Rename failed: {body.get('error') or 'failed'}"
                )
            self._sync_rename_button()

        def on_err(exc: BaseException) -> None:
            self._busy = False
            self.meta_var.set(f"Rename failed: {exc}")
            self._sync_rename_button()

        rename = self._rename_set
        if rename is None:
            return
        rename(old, new, on_ok, on_err)

    def _menu_set_names(self) -> list[str]:
        if self._get_set_names is not None:
            names = list(self._get_set_names())
            if names:
                return names
        return list(self._names)

    def _note_set_used(self, name: str) -> None:
        n = " ".join((name or "").split())
        if not n:
            return
        key = n.casefold()
        if not any(x.casefold() == key for x in self._names):
            self._names.append(n)
            self._names.sort(key=str.casefold)
            self._list.delete(0, tk.END)
            for nm in self._names:
                self._list.insert(tk.END, nm)
            self._sync_rename_button()
        if self._on_set_names is not None:
            cached = list(self._get_set_names()) if self._get_set_names else list(self._names)
            if not any(x.casefold() == key for x in cached):
                cached.append(n)
            self._on_set_names(cached)

    def _add_to_set(self, char_id: int, set_name: str) -> None:
        name = " ".join((set_name or "").split())
        if not name:
            return
        self._menu_craft(char_id, f"stadd:{name}")

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

    def clear(self) -> None:
        self._disarm_delete()
        self._names = []
        self._selected = ""
        self._list.delete(0, tk.END)
        self.clear_grid()
        self.meta_var.set("Pick a set on the left.")
        self._sync_rename_button()

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
        if self._pending_delete and name.casefold() != self._pending_delete.casefold():
            self._disarm_delete()
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
            whose_bit = f"@{self._whose} · " if self._whose else ""
            self.meta_var.set(
                f"{whose_bit}“{set_name}” · page {self._page + 1}/{pages} · {self._total} cards"
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

        self._fetch_page(int(page), self._owner_q(), -1, set_name, on_ok, on_err)

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
            set_names=self._menu_set_names(),
            current_set=str(item.get("set") or self._selected or ""),
            on_add_to_set=self._add_to_set,
            on_new_set=self._add_to_new_set,
            can_edit_sets=bool(item.get("mine", True)) and not self._whose,
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
                detail = str(body.get("detail") or "ok").strip()
                silent = bool(body.get("silent"))
                notice = detail if detail and detail != "ok" else f"{label} ✓"
                self.meta_var.set(f"#{char_id}: {notice}")
                self._on_log(f"Craft {action_id} char {char_id}: {notice}")
                if silent:
                    from link_bridge.thumb_menu import apply_silent_craft_item

                    apply_silent_craft_item(self._item_by_id(char_id), action_id)
                    if str(action_id).startswith("stadd:"):
                        new_set = str(action_id).split(":", 1)[1]
                        self._note_set_used(new_set)
                        if (
                            self._selected
                            and new_set.casefold() != self._selected.casefold()
                        ):
                            self.load_page(self._page)
                elif self._should_focus():
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
            msg = str(exc or "failed")
            if "disconnect" in msg.lower() or "not connected" in msg.lower():
                self.meta_var.set(
                    "Post failed: disconnected (close other Link Bridge copies)"
                )
            else:
                self.meta_var.set(f"Post failed: {msg}")
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
