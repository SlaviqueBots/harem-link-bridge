"""Right-click context menu for roster thumbs.

Omnicraft covers reshape / multi / slopify / portal / author / title /
set-flavour / undo / checkpoint-save / done / hide / refine.
This menu keeps Omnicraft as one entry, then nests extras it does not cover.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any


def apply_silent_craft_item(item: dict[str, Any] | None, action_id: str) -> None:
    """Update a cached roster item after a quiet craft (no Telegram post)."""
    if not item:
        return
    aid = str(action_id)
    if ":" in aid:
        op, raw = aid.split(":", 1)
        if op == "flset":
            item["flavour"] = raw
        elif op == "ntset":
            item["note"] = raw
        elif op == "stadd":
            from link_bridge.set_names import encode_set_names, parse_set_names

            existing = parse_set_names(item.get("set"))
            item["set"] = encode_set_names([*existing, raw])
        elif op == "strem":
            from link_bridge.set_names import encode_set_names, parse_set_names

            wanted = (raw or "").strip().casefold()
            kept = [
                n
                for n in parse_set_names(item.get("set"))
                if n.casefold() != wanted
            ]
            item["set"] = encode_set_names(kept)
        return
    if aid == "rfl":
        item["flavour"] = ""
    elif aid == "rnt":
        item["note"] = ""
    elif aid == "dn":
        item["done"] = True
    elif aid == "ud":
        item["done"] = False


def popup_thumb_menu(
    widget: tk.Misc,
    event: Any,
    *,
    char_id: int,
    post_url: str,
    on_open_post: Callable[[str], None],
    on_craft: Callable[[int, str], None],
    on_register_cup: Callable[[int], None] | None = None,
    on_show_checkpoint: Callable[[str], None] | None = None,
    on_edit_flavour: Callable[[int], None] | None = None,
    on_edit_note: Callable[[int], None] | None = None,
    can_tame: bool = False,
    is_tamed: bool = False,
    has_checkpoint: bool = False,
    checkpoint_image_url: str = "",
    char_name: str = "",
    set_names: list[str] | None = None,
    current_set: str = "",
    on_add_to_set: Callable[[int, str], None] | None = None,
    on_new_set: Callable[[int], None] | None = None,
    on_remove_from_set: Callable[[int, str], None] | None = None,
    can_edit_sets: bool = True,
    can_cycle_name: bool = False,
    is_done: bool = False,
) -> None:
    """Show nested bridge craft menu at the pointer."""
    menu = tk.Menu(widget, tearoff=0)

    def craft(action_id: str) -> None:
        on_craft(int(char_id), action_id)

    name = (char_name or "").strip()
    if name:
        label = name if len(name) <= 48 else name[:45] + "…"
        menu.add_command(label=f"#{char_id} · {label}", state=tk.DISABLED)
        menu.add_separator()

    menu.add_command(label="Open Omnicraft…", command=lambda: craft("omni"))
    menu.add_separator()

    checkpoint = tk.Menu(menu, tearoff=0)
    checkpoint.add_command(label="Save checkpoint", command=lambda: craft("cp"))
    if has_checkpoint:
        checkpoint.add_command(label="Load checkpoint", command=lambda: craft("ld"))
        cp_url = (checkpoint_image_url or "").strip()
        if cp_url and on_show_checkpoint is not None:
            checkpoint.add_command(
                label="Show checkpoint",
                command=lambda u=cp_url: on_show_checkpoint(u),
            )
        elif on_show_checkpoint is not None:
            checkpoint.add_command(label="Show checkpoint", state=tk.DISABLED)
    menu.add_cascade(label="Checkpoint", menu=checkpoint)

    flavour = tk.Menu(menu, tearoff=0)
    if on_edit_flavour is not None:
        flavour.add_command(
            label="Set flavour…",
            command=lambda: on_edit_flavour(int(char_id)),
        )
    else:
        flavour.add_command(label="Set flavour…", command=lambda: craft("fl"))
    flavour.add_command(label="Remove flavour", command=lambda: craft("rfl"))
    menu.add_cascade(label="Flavour", menu=flavour)

    note = tk.Menu(menu, tearoff=0)
    if on_edit_note is not None:
        note.add_command(
            label="Set note…",
            command=lambda: on_edit_note(int(char_id)),
        )
    else:
        note.add_command(label="Set note…", command=lambda: craft("nt"))
    note.add_command(label="Remove note", command=lambda: craft("rnt"))
    menu.add_cascade(label="Note", menu=note)

    if can_edit_sets and (
        on_add_to_set is not None
        or on_new_set is not None
        or on_remove_from_set is not None
    ):
        from link_bridge.set_names import parse_set_names

        set_m = tk.Menu(menu, tearoff=0)
        # ``current_set`` may be multi-set encoded (unit-separator joined).
        member_names = parse_set_names(current_set)
        member_keys = {n.casefold() for n in member_names}
        names = [str(x).strip() for x in (set_names or []) if str(x).strip()]
        for sname in names:
            label = f"Add to {sname}"
            if sname.casefold() in member_keys:
                set_m.add_command(label=label, state=tk.DISABLED)
            elif on_add_to_set is not None:
                set_m.add_command(
                    label=label,
                    command=lambda n=sname: on_add_to_set(int(char_id), n),
                )
        if member_names and on_remove_from_set is not None:
            if names:
                set_m.add_separator()
            for sname in member_names:
                set_m.add_command(
                    label=f"Remove from {sname}",
                    command=lambda n=sname: on_remove_from_set(int(char_id), n),
                )
        if (names or member_names) and on_new_set is not None:
            set_m.add_separator()
        if on_new_set is not None:
            set_m.add_command(
                label="Add to new set…",
                command=lambda: on_new_set(int(char_id)),
            )
        menu.add_cascade(label="Set", menu=set_m)

    if can_tame or is_tamed:
        tame = tk.Menu(menu, tearoff=0)
        if can_tame:
            tame.add_command(label="Mark as tamed", command=lambda: craft("tm"))
        if is_tamed:
            tame.add_command(label="Untame", command=lambda: craft("ut"))
            tame.add_command(
                label="Post tamed album (DM)", command=lambda: craft("td")
            )
        menu.add_cascade(label="Tame", menu=tame)

    copy_m = tk.Menu(menu, tearoff=0)
    copy_m.add_command(label="Mirror card", command=lambda: craft("mi"))
    copy_m.add_command(
        label="Mirror card & start Omnicraft",
        command=lambda: craft("mi_omni"),
    )
    menu.add_cascade(label="Copy", menu=copy_m)

    extra = tk.Menu(menu, tearoff=0)
    if can_cycle_name:
        extra.add_command(
            label="Cycle character name…",
            command=lambda: craft("cr"),
        )
    extra.add_command(label="Open Variant…", command=lambda: craft("vr"))
    extra.add_command(label="Title swap…", command=lambda: craft("tswap"))
    extra.add_command(label="Open omni in bot DMs", command=lambda: craft("omni_dm"))
    menu.add_cascade(label="Extra crafts", menu=extra)

    menu.add_command(
        label="Mark undone" if is_done else "Mark done",
        command=lambda: craft("ud" if is_done else "dn"),
    )

    status = tk.Menu(menu, tearoff=0)
    status.add_command(label="Hide card", command=lambda: craft("hi"))
    status.add_command(label="Show card", command=lambda: craft("sh"))
    menu.add_cascade(label="Status", menu=status)

    def confirm_trash(kind: str) -> None:
        from tkinter import messagebox

        top = widget.winfo_toplevel()
        if kind == "tr":
            ok = messagebox.askyesno(
                "Trash",
                f"Trash #{char_id} to the market?\n"
                "You get +5. Others can buy it after the grace period.",
                parent=top,
            )
        else:
            ok = messagebox.askyesno(
                "Perma trash",
                f"Permanently delete #{char_id}?\n"
                "You get +5. Cannot restore from trashbin.",
                parent=top,
            )
        if ok:
            craft(kind)

    danger = tk.Menu(menu, tearoff=0)
    danger.add_command(
        label="Trash (market)…",
        command=lambda: confirm_trash("tr"),
    )
    danger.add_command(
        label="Perma trash…",
        command=lambda: confirm_trash("ptr"),
    )
    menu.add_cascade(label="Trash", menu=danger)

    menu.add_separator()
    url = (post_url or "").strip()
    if url:
        menu.add_command(
            label="Open post in browser",
            command=lambda: on_open_post(url),
        )
    else:
        menu.add_command(label="Open post in browser", state=tk.DISABLED)

    if on_register_cup is not None:
        menu.add_command(
            label="Register for daily cup…",
            command=lambda: on_register_cup(int(char_id)),
        )

    try:
        menu.tk_popup(int(event.x_root), int(event.y_root))
    finally:
        try:
            menu.grab_release()
        except Exception:
            pass
