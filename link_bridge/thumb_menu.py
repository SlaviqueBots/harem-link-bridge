"""Right-click context menu for roster thumbs.

Omnicraft covers reshape / multi / slopify / portal / author / title /
set-flavour / undo / checkpoint-save / done / hide / refine.
This menu keeps Omnicraft as one entry, then nests extras it does not cover.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any


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
    can_tame: bool = False,
    is_tamed: bool = False,
    has_checkpoint: bool = False,
    checkpoint_image_url: str = "",
) -> None:
    """Show nested bridge craft menu at the pointer."""
    menu = tk.Menu(widget, tearoff=0)

    def craft(action_id: str) -> None:
        on_craft(int(char_id), action_id)

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
    flavour.add_command(label="Set flavour…", command=lambda: craft("fl"))
    flavour.add_command(label="Remove flavour", command=lambda: craft("rfl"))
    menu.add_cascade(label="Flavour", menu=flavour)

    note = tk.Menu(menu, tearoff=0)
    note.add_command(label="Set note…", command=lambda: craft("nt"))
    note.add_command(label="Remove note", command=lambda: craft("rnt"))
    menu.add_cascade(label="Note", menu=note)

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
    menu.add_cascade(label="Copy", menu=copy_m)

    extra = tk.Menu(menu, tearoff=0)
    extra.add_command(label="Open Variant…", command=lambda: craft("vr"))
    extra.add_command(label="Title swap…", command=lambda: craft("tswap"))
    menu.add_cascade(label="Extra crafts", menu=extra)

    status = tk.Menu(menu, tearoff=0)
    status.add_command(label="Mark done", command=lambda: craft("dn"))
    status.add_command(label="Mark undone", command=lambda: craft("ud"))
    status.add_command(label="Hide card", command=lambda: craft("hi"))
    status.add_command(label="Show card", command=lambda: craft("sh"))
    menu.add_cascade(label="Status", menu=status)

    danger = tk.Menu(menu, tearoff=0)
    danger.add_command(label="Trash (market)…", command=lambda: craft("tr"))
    danger.add_command(label="Perma trash…", command=lambda: craft("ptr"))
    menu.add_cascade(label="Danger", menu=danger)

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
