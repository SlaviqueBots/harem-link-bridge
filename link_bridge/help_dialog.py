"""In-app help for Harem Link Bridge."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


def _dialog_palette(parent: tk.Misc) -> dict[str, str]:
    from link_bridge.theme import palette, surface_for

    pal = surface_for(parent)
    if not pal.get("bg"):
        pal = palette("dark")
    bg = pal.get("bg") or "#1e1f22"
    return {
        "bg": bg,
        "bg2": pal.get("bg2") or "#2b2d31",
        "fg": pal.get("fg") or "#f2f3f5",
        "muted": pal.get("muted") or "#b5bac1",
        "entry_bg": pal.get("log_bg") or pal.get("entry") or "#111214",
        "select": pal.get("select") or "#404249",
    }


def _help_text(userscript_path: Path) -> str:
    us = str(userscript_path)
    return f"""Harem Link Bridge — quick guide

What Bridge does
────────────────
• Connects to your harem Telegram bot over a secure WebSocket (pair once in Setup).
• Opens Danbooru / Rule34 links from the bot in your desktop browser — no extra taps in Telegram.
• Roster: browse your cards, open Omnicraft, post to chat, craft from the right-click menu.
• Market: browse @buy listings, filter by min/max price (saved between sessions), hide lots, inspect and buy.
• Taming tab: Tamed (before|after pairs) and Primed (origin previews ready to tame).
• Conjure tab: recent Conjure Finder hits from the bot or browser.
• Tray icon: double-click to show the window; right-click for Show / Quit.

Typical clicks (Roster)
───────────────────────
• Left — open full image (or Omnicraft if enabled in Setup)
• Middle — post card to the main group
• Right — craft menu (Omnicraft, reshape, portal, daily cup, …)

Flavoured / Unflavoured tabs
────────────────────────────
• LMB → Flavour (on by default) — left-click opens the flavour editor for that card.
• Takes priority over LMB → Omni when both are enabled.

Primed / Market hide
────────────────────
• Hide mode (LMB) — click a thumbnail to hide it client-side (persists locally).
• Show hidden — view only hidden cards/lots (not mixed with visible ones).
• LMB → Omni on Tamed (after tile) and Primed when enabled; hide mode wins if both are on.

Setup tips
──────────
• Pair with Telegram once; settings live in harem_link_bridge.json beside the exe.
• Dev runs use harem_link_bridge.dev.json (separate saved filters and geometry).
• “Pause when PC locked” stops link buttons while Win+L is active.
• Market grid view and price filters are remembered between sessions.


Browser userscript (important)
──────────────────────────────
While browsing Danbooru or Rule34 you can send the open post straight into Bridge
and the bot — without copying URLs.

What it does
  On supported post pages a small button bar appears (top-right):
    checkres   conjure
    [    both    ]   craft

  • checkres — max-resolution diagnostic to your bot DMs
  • conjure  — run Conjure Finder on this post
  • both     — checkres + Conjure Finder together
  • craft    — add the post to your open Omnicraft crafting plan

  Bridge must be running (tray is fine). It listens on http://127.0.0.1:8767/send
  (toggle “Browser hook” in advanced settings if you disabled it).

How to install
  1. Install a userscript manager:
       Firefox — Violentmonkey (recommended)
       Chrome / Edge — Tampermonkey or Violentmonkey
     Chromium cannot use plain fetch to localhost from HTTPS booru tabs —
     the manager’s GM_xmlhttpRequest is required.

  2. Import the script file:
       {us}

     After an app update, a fresh copy is saved next to HaremLinkBridge.exe.
     In your manager: Create new script → Import from file → pick that .user.js file.

  3. Keep Bridge connected. Open any Danbooru or Rule34 post — the bar should appear.
     Click an action; results show in Bridge (Conjure tab) and/or Telegram DMs.

  Troubleshooting
  • No bar? Confirm the URL matches /posts/… (Danbooru) or page=post (Rule34).
  • Action fails? Check Bridge status is Connected and browser hook port is 8767.
  • Re-import the userscript after updates if your manager did not auto-update.

More: link_bridge/README.md in the source repo (Source code button in Setup).
"""


def show_help(parent: tk.Misc, *, userscript_path: Path) -> None:
    pal = _dialog_palette(parent)
    bg = pal["bg"]
    fg = pal["fg"]
    muted = pal["muted"]
    entry_bg = pal["entry_bg"]
    select = pal["select"]

    win = tk.Toplevel(parent)
    win.title("Bridge help")
    win.transient(parent)
    win.geometry("720x560")
    win.minsize(520, 400)
    win.configure(bg=bg)

    head = ttk.Frame(win, padding=(10, 10, 10, 0))
    head.pack(fill=tk.X)
    ttk.Label(head, text="Harem Link Bridge", font=("Segoe UI", 12, "bold")).pack(
        anchor=tk.W
    )
    ttk.Label(
        head,
        text="Basics below · browserscript section at the bottom",
        foreground=muted,
    ).pack(anchor=tk.W, pady=(2, 0))

    text_fr = ttk.Frame(win, padding=(10, 8, 10, 0))
    text_fr.pack(fill=tk.BOTH, expand=True)
    body = tk.Text(
        text_fr,
        wrap=tk.WORD,
        font=("Segoe UI", 10),
        bg=entry_bg,
        fg=fg,
        insertbackground=fg,
        selectbackground=select,
        padx=8,
        pady=8,
        borderwidth=0,
        highlightthickness=0,
    )
    scroll = ttk.Scrollbar(
        text_fr,
        orient=tk.VERTICAL,
        command=body.yview,
        style="Vertical.TScrollbar",
    )
    body.configure(yscrollcommand=scroll.set)
    body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    body.insert("1.0", _help_text(userscript_path))
    body.configure(state=tk.DISABLED)

    def _wheel(event) -> str:
        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            body.yview_scroll(int(-delta / 120), "units")
        return "break"

    body.bind("<MouseWheel>", _wheel)
    text_fr.bind("<MouseWheel>", _wheel)

    btns = ttk.Frame(win, padding=(10, 0, 10, 10))
    btns.pack(fill=tk.X)

    def copy_path() -> None:
        path = str(userscript_path)
        try:
            win.clipboard_clear()
            win.clipboard_append(path)
            win.update_idletasks()
        except Exception:
            messagebox.showinfo("Userscript path", path, parent=win)
            return
        messagebox.showinfo("Copied", f"Path copied:\n{path}", parent=win)

    def open_folder() -> None:
        folder = userscript_path.parent
        try:
            import os
            import subprocess
            import sys

            if sys.platform == "win32":
                os.startfile(str(folder))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", str(folder)], check=False)
            else:
                subprocess.run(["xdg-open", str(folder)], check=False)
        except Exception as exc:
            messagebox.showerror("Open folder", str(exc), parent=win)

    ttk.Button(btns, text="Copy userscript path", command=copy_path).pack(side=tk.LEFT)
    ttk.Button(btns, text="Open script folder", command=open_folder).pack(
        side=tk.LEFT, padx=(8, 0)
    )
    ttk.Button(btns, text="Close", command=win.destroy).pack(side=tk.RIGHT)

    from link_bridge.window_keys import bind_q_close

    bind_q_close(win)
    win.grab_set()
    win.focus_force()
