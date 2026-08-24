"""Settings window for API keys (portable Windows distribution)."""

from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from conjure_finder import settings as settings_mod

DANBOORU_KEYS_URL = "https://danbooru.donmai.us/api_keys"
DANBOORU_PROFILE_URL = "https://danbooru.donmai.us/profile"
RULE34_OPTIONS_URL = "https://rule34.xxx/index.php?page=account&s=options"


def _link_label(parent: tk.Misc, url: str) -> ttk.Label:
    lbl = ttk.Label(
        parent,
        text=url,
        foreground="#06c",
        cursor="hand2",
        font=("Consolas", 8),
    )
    lbl.bind("<Button-1>", lambda _e: webbrowser.open(url))
    return lbl


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, on_saved=None) -> None:
        super().__init__(master)
        self.title("Conjure Finder — Settings")
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        self._on_saved = on_saved

        self._vars: dict[str, tk.StringVar] = {}
        current = settings_mod.read_settings()

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame,
            text="API keys for this PC. Saved to conjure_finder.env (not shared).",
            wraplength=460,
        ).pack(anchor=tk.W, pady=(0, 8))

        form = ttk.Frame(frame)
        form.pack(fill=tk.X)

        entries: list[ttk.Entry] = []
        for row, (key, label, _hint) in enumerate(settings_mod.SETTING_FIELDS):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky=tk.W, pady=4, padx=(0, 8))
            var = tk.StringVar(value=current.get(key, ""))
            self._vars[key] = var
            show = "*" if "KEY" in key or "TOKEN" in key else ""
            entry = ttk.Entry(form, textvariable=var, width=48, show=show)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=4)
            entries.append(entry)
            if show:
                reveal = tk.BooleanVar(value=False)

                def _toggle(
                    e: ttk.Entry = entry, v: tk.BooleanVar = reveal, _s: str = show
                ) -> None:
                    e.configure(show="" if v.get() else _s)

                chk = ttk.Checkbutton(
                    form, text="Show", variable=reveal, command=_toggle
                )
                chk.grid(row=row, column=2, padx=(6, 0))

        form.columnconfigure(1, weight=1)

        from conjure_finder.clipboard_bindings import install_clipboard_bindings

        install_clipboard_bindings(*entries)

        status = settings_mod.settings_status()
        bits = [
            "Danbooru: OK" if status["danbooru"] else "Danbooru: missing",
            "Rule34: OK" if status["rule34"] else "Rule34: missing",
            "Using conjure_finder.env" if status["file"] else "Using shared .env (testing)",
        ]
        ttk.Label(frame, text=" · ".join(bits), foreground="#444").pack(
            anchor=tk.W, pady=(10, 0)
        )

        help_box = ttk.LabelFrame(frame, text="How to get keys", padding=8)
        help_box.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(
            help_box,
            text="Danbooru: log in → API Keys → create a key "
            "(username = your login; paste the key below).",
            wraplength=440,
        ).pack(anchor=tk.W)
        db_links = ttk.Frame(help_box)
        db_links.pack(anchor=tk.W, pady=(2, 2))
        ttk.Button(
            db_links,
            text="Open Danbooru API Keys",
            command=lambda: webbrowser.open(DANBOORU_KEYS_URL),
        ).pack(side=tk.LEFT)
        ttk.Button(
            db_links,
            text="Profile",
            command=lambda: webbrowser.open(DANBOORU_PROFILE_URL),
        ).pack(side=tk.LEFT, padx=(6, 0))
        _link_label(help_box, DANBOORU_KEYS_URL).pack(anchor=tk.W)

        ttk.Label(
            help_box,
            text="Rule34: log in → Account Options → API Access Credentials → "
            "check Generate New Key → Save. Copy api_key and user_id.",
            wraplength=440,
        ).pack(anchor=tk.W, pady=(10, 0))
        r34_links = ttk.Frame(help_box)
        r34_links.pack(anchor=tk.W, pady=(2, 2))
        ttk.Button(
            r34_links,
            text="Open Rule34 Account Options",
            command=lambda: webbrowser.open(RULE34_OPTIONS_URL),
        ).pack(side=tk.LEFT)
        _link_label(help_box, RULE34_OPTIONS_URL).pack(anchor=tk.W)

        btns = ttk.Frame(frame)
        btns.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Save", command=self._save).pack(side=tk.RIGHT, padx=(0, 8))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, self._center)

    def _center(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = self.master.winfo_rootx() + (self.master.winfo_width() - w) // 2
        y = self.master.winfo_rooty() + (self.master.winfo_height() - h) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _save(self) -> None:
        values = {k: v.get() for k, v in self._vars.items()}
        r34_key = values.get("RULE34_API_KEY", "").strip()
        r34_uid = values.get("RULE34_USER_ID", "").strip()
        if r34_key and not r34_uid.isdigit():
            messagebox.showerror(
                "Settings",
                "Rule34 user id must be a number (from your Rule34 account page).",
                parent=self,
            )
            return
        path = settings_mod.save_settings(values)
        cb = self._on_saved
        messagebox.showinfo(
            "Settings",
            f"Saved to:\n{path}\n\nNew searches will use these keys (no restart needed).",
            parent=self,
        )
        self.destroy()
        if cb is not None:
            try:
                cb()
            except Exception:
                pass
