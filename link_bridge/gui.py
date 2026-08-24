"""Tkinter GUI — pair with Telegram, connect, tray companion."""

from __future__ import annotations

import asyncio
import logging
import threading
import webbrowser
from datetime import datetime

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from link_bridge import __version__
from link_bridge.config import BridgeConfig, load_config, save_config
from link_bridge.ws_client import BridgeClient

logger = logging.getLogger(__name__)


class UpdateProgressDialog(tk.Toplevel):
    """Small modal window with a real progress bar during exe download."""

    def __init__(self, parent: tk.Misc, version: str) -> None:
        super().__init__(parent)
        self.title("Downloading update")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        frame = ttk.Frame(self, padding=16)
        frame.pack()
        ttk.Label(
            frame,
            text=f"Downloading Harem Link Bridge v{version}",
            font=("", 10, "bold"),
        ).pack(anchor=tk.W)
        self.detail_var = tk.StringVar(value="Starting…")
        ttk.Label(frame, textvariable=self.detail_var, wraplength=380).pack(
            anchor=tk.W, pady=(8, 6)
        )
        self.bar = ttk.Progressbar(frame, mode="determinate", length=380, maximum=100)
        self.bar.pack(fill=tk.X)
        self.update_idletasks()
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{px}+{py}")

    def set_status(self, msg: str) -> None:
        self.detail_var.set(msg)
        self.update_idletasks()

    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            pct = min(100, int(100 * done / total))
            self.bar.configure(value=pct)
            mb = done / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            self.detail_var.set(f"{pct}% — {mb:.1f} / {total_mb:.1f} MB")
        elif done > 0:
            mb = done / (1024 * 1024)
            self.detail_var.set(f"{mb:.1f} MB downloaded…")
        else:
            self.detail_var.set("Connecting…")
        self.update_idletasks()


class LinkBridgeApp(tk.Tk):
    def __init__(self, cfg: BridgeConfig | None = None) -> None:
        from link_bridge.dpi import apply_ui_scale, enable_dpi_awareness

        enable_dpi_awareness()
        super().__init__()
        self.title(f"Harem Link Bridge  v{__version__}")
        self.minsize(720, 560)
        from link_bridge.roster import DEFAULT_GEOMETRY

        self.cfg = cfg or load_config()
        self.cfg.ensure_device_id()
        saved = (self.cfg.window_geometry or "").strip()
        self.geometry(saved if saved else DEFAULT_GEOMETRY)

        from link_bridge.theme import apply_app_theme, normalize_theme

        self.cfg.ui_theme = normalize_theme(self.cfg.ui_theme)
        from link_bridge.config import _clamp_ui_scale

        self.cfg.ui_scale = _clamp_ui_scale(getattr(self.cfg, "ui_scale", 1.0))
        apply_app_theme(self, self.cfg.ui_theme)
        apply_ui_scale(self, self.cfg.ui_scale)
        self.after_idle(lambda: apply_ui_scale(self, self.cfg.ui_scale))

        self._client: BridgeClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._tray = None
        self._quitting = False
        self._pair_stop = threading.Event()
        self._lock_ctrl = None
        self._lock_watcher = None
        self._roster = None
        self._sets = None
        self._omni_wins: list = []
        self._themes = None
        self._themes_tab = None
        self._themes_tab_index = None
        self._conjure_tab = None
        self._conjure = None
        self._browser_hook = None
        self._geo_save_after: str | None = None
        self._geo_ready = False
        self._want_zoomed = (self.cfg.window_state or "normal").strip().lower() == "zoomed"
        try:
            from link_bridge.thumb_grid import enable_default_disk_cache

            enable_default_disk_cache()
        except Exception:
            pass

        self._build()
        from link_bridge.theme import apply_app_theme, schedule_theme_refresh

        apply_app_theme(self, self.cfg.ui_theme)
        schedule_theme_refresh(self, self.cfg.ui_theme, delay_ms=200)
        self.protocol("WM_DELETE_WINDOW", self._on_close_to_tray)
        # Windows often drops early state('zoomed') while widgets pack — restore later.
        self.after_idle(self._restore_window_state)
        self.after(120, self._restore_window_state)
        self.bind("<Configure>", self._on_window_configure, add="+")
        # Ignore Configure noise until maximize restore has a chance to stick.
        self.after(500, self._enable_geo_persist)
        self.after(200, self._setup_tray)
        if self.cfg.start_hidden:
            self.after(300, self.withdraw)
        if self.cfg.can_connect():
            self.after(400, self.start_bridge)
        self.after(700, self._start_browser_hook)
        if self.cfg.pause_on_lock:
            self.after(500, self._sync_pause_on_lock_watcher)
        if self.cfg.check_updates:
            self.after(1500, lambda: self.check_updates(silent=True))
        self.bind_all("<Control-0>", lambda _e: self._reset_ui_scale())
        self.bind_all("<Control-Key-0>", lambda _e: self._reset_ui_scale())
        self.bind_all("<Control-minus>", lambda _e: self._nudge_ui_scale(-0.05) or "break")
        self.bind_all("<Control-plus>", lambda _e: self._nudge_ui_scale(0.05) or "break")
        self.bind_all("<Control-equal>", lambda _e: self._nudge_ui_scale(0.05) or "break")

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}
        root = ttk.Frame(self, padding=8)
        root.pack(fill=tk.BOTH, expand=True)

        self._main_nb = ttk.Notebook(root)
        self._main_nb.pack(fill=tk.BOTH, expand=True)
        roster_tab = ttk.Frame(self._main_nb, padding=2)
        setup_tab = ttk.Frame(self._main_nb, padding=8)
        self._main_nb.add(roster_tab, text="Roster")
        self._main_nb.add(setup_tab, text="Setup")

        self.status_var = tk.StringVar(value=self._idle_status_text())

        from link_bridge.roster import RosterPanel

        self._roster = RosterPanel(
            roster_tab,
            fetch_page=self._roster_fetch_page,
            open_omni=self._roster_open_omni,
            open_omni_ui=self._open_omni_ui,
            post_grid=self._roster_post_grid,
            register_cup=self._roster_register_cup,
            dm_craft=self._roster_dm_craft,
            list_sets=self._sets_list,
            rename_set=self._sets_rename,
            delete_set=self._sets_delete,
            present_set=self._sets_present,
            fetch_tamed=self._roster_fetch_tamed,
            fetch_market=self._market_fetch_page,
            buy_market=self._market_buy,
            should_focus_telegram=lambda: bool(self.cfg.focus_telegram),
            get_post_target=lambda: self.cfg.middle_click_target,
            set_post_target=self._set_middle_click_target,
            prefer_original_open=lambda: bool(self.cfg.prefer_original_open),
            get_left_click_omni=lambda: bool(self.cfg.left_click_omni),
            set_left_click_omni=self._set_left_click_omni,
            get_hide_in_any_set=lambda: bool(self.cfg.hide_in_any_set),
            set_hide_in_any_set=self._set_hide_in_any_set,
            status_var=self.status_var,
            get_text_edit_geometry=lambda: str(self.cfg.text_edit_geometry or ""),
            set_text_edit_geometry=self._save_text_edit_geometry,
            fetch_browse_users=self._browse_users,
            natural_thumbs=bool(self.cfg.natural_thumbs),
            preview_scale=float(self.cfg.preview_scale or 1.5),
            scroll_speed=float(self.cfg.scroll_speed or 3.0),
            on_log=self._append_log,
        )
        self._roster.pack(fill=tk.BOTH, expand=True)
        self._sets = None

        self._build_setup_tab(setup_tab, pad)
        self._ensure_conjure_tab()

    def _build_setup_tab(self, root: ttk.Frame, pad: dict) -> None:
        steps = ttk.LabelFrame(root, text="Setup (once)", padding=8)
        steps.pack(fill=tk.X, **pad)
        ttk.Label(
            steps,
            justify=tk.LEFT,
            wraplength=600,
            text=(
                "1. Click  Pair with Telegram  below (opens Telegram — confirm).\n"
                "   Alternate Method: in Telegram DM send /bridge, then  Enter code…  here.\n"
                "2. Status should say Connected. Close this window anytime — it stays in the tray."
            ),
        ).pack(anchor=tk.W)

        pair_fr = ttk.LabelFrame(root, text="Telegram pairing", padding=8)
        pair_fr.pack(fill=tk.X, **pad)
        self.pair_status = tk.StringVar(value=self._pair_status_text())
        ttk.Label(pair_fr, textvariable=self.pair_status, wraplength=580).pack(
            anchor=tk.W
        )
        pair_btns = ttk.Frame(pair_fr)
        pair_btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(
            pair_btns, text="Pair with Telegram", command=self.start_pair_deep_link
        ).pack(side=tk.LEFT)
        ttk.Button(
            pair_btns, text="Enter code…", command=self.start_pair_code
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(pair_btns, text="Unpair", command=self.unpair).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        opts = ttk.Frame(root)
        opts.pack(fill=tk.X, **pad)
        self.paused_var = tk.BooleanVar(value=self.cfg.paused)
        self.pause_on_lock_var = tk.BooleanVar(value=self.cfg.pause_on_lock)
        self.open_var = tk.BooleanVar(value=self.cfg.open_browser)
        self.hidden_var = tk.BooleanVar(value=self.cfg.start_hidden)
        self.autostart_var = tk.BooleanVar(value=self._autostart_initial())
        ttk.Checkbutton(
            opts,
            text="Paused (no link buttons)",
            variable=self.paused_var,
            command=self._on_pause_toggle,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(opts, text="Open in browser", variable=self.open_var).pack(
            side=tk.LEFT, padx=(12, 0)
        )
        opts2 = ttk.Frame(root)
        opts2.pack(fill=tk.X, **pad)
        ttk.Checkbutton(
            opts2,
            text="Pause when PC locked (Win+L)",
            variable=self.pause_on_lock_var,
            command=self._on_pause_on_lock_toggle,
        ).pack(side=tk.LEFT)
        opts3 = ttk.Frame(root)
        opts3.pack(fill=tk.X, **pad)
        ttk.Checkbutton(
            opts3,
            text="Start with Windows",
            variable=self.autostart_var,
            command=self._on_autostart_toggle,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            opts3,
            text="Start in tray",
            variable=self.hidden_var,
            command=self._on_start_hidden_toggle,
        ).pack(side=tk.LEFT, padx=(12, 0))
        opts4 = ttk.Frame(root)
        opts4.pack(fill=tk.X, **pad)
        self.focus_tg_var = tk.BooleanVar(value=self.cfg.focus_telegram)
        ttk.Checkbutton(
            opts4,
            text="Focus Telegram after open",
            variable=self.focus_tg_var,
            command=self._on_focus_tg_toggle,
        ).pack(side=tk.LEFT)
        opts5 = ttk.Frame(root)
        opts5.pack(fill=tk.X, **pad)
        self.natural_thumbs_var = tk.BooleanVar(value=self.cfg.natural_thumbs)
        ttk.Checkbutton(
            opts5,
            text="Tight gallery (default). Uncheck for square crop grid",
            variable=self.natural_thumbs_var,
            command=self._on_natural_thumbs_toggle,
        ).pack(side=tk.LEFT)
        opts6 = ttk.Frame(root)
        opts6.pack(fill=tk.X, **pad)
        ttk.Label(opts6, text="Preview size").pack(side=tk.LEFT)
        self.preview_scale_var = tk.DoubleVar(
            value=float(self.cfg.preview_scale or 1.5)
        )
        self.preview_scale_label = tk.StringVar(
            value=f"{int(round(float(self.cfg.preview_scale or 1.5) * 100))}%"
        )
        self._preview_scale_after: str | None = None
        scale = ttk.Scale(
            opts6,
            from_=0.5,
            to=2.0,
            orient=tk.HORIZONTAL,
            variable=self.preview_scale_var,
            command=self._on_preview_scale_slide,
        )
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        ttk.Label(opts6, textvariable=self.preview_scale_label, width=5).pack(
            side=tk.LEFT
        )
        ttk.Button(
            opts6, text="Default", width=8, command=self._reset_preview_scale
        ).pack(side=tk.LEFT, padx=(6, 0))

        opts6b = ttk.Frame(root)
        opts6b.pack(fill=tk.X, **pad)
        ttk.Label(opts6b, text="Scroll speed").pack(side=tk.LEFT)
        self.scroll_speed_var = tk.DoubleVar(
            value=float(self.cfg.scroll_speed or 3.0)
        )
        self.scroll_speed_label = tk.StringVar(
            value=f"{float(self.cfg.scroll_speed or 3.0):.2f}×"
        )
        self._scroll_speed_after: str | None = None
        ttk.Scale(
            opts6b,
            from_=0.25,
            to=6.0,
            orient=tk.HORIZONTAL,
            variable=self.scroll_speed_var,
            command=self._on_scroll_speed_slide,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        ttk.Label(opts6b, textvariable=self.scroll_speed_label, width=5).pack(
            side=tk.LEFT
        )
        ttk.Button(
            opts6b, text="Default", width=8, command=self._reset_scroll_speed
        ).pack(side=tk.LEFT, padx=(6, 0))

        opts7 = ttk.Frame(root)
        opts7.pack(fill=tk.X, **pad)
        self.prefer_original_var = tk.BooleanVar(
            value=bool(self.cfg.prefer_original_open)
        )
        ttk.Checkbutton(
            opts7,
            text="Left-click opens original file URL (PC downloads from source)",
            variable=self.prefer_original_var,
            command=self._on_prefer_original_toggle,
        ).pack(side=tk.LEFT)

        theme_row = ttk.Frame(root)
        theme_row.pack(fill=tk.X, **pad)
        ttk.Label(theme_row, text="Appearance").pack(side=tk.LEFT)
        from link_bridge.theme import normalize_theme

        self.theme_var = tk.StringVar(value=normalize_theme(self.cfg.ui_theme))
        ttk.Radiobutton(
            theme_row,
            text="Dark",
            value="dark",
            variable=self.theme_var,
            command=self._on_theme_change,
        ).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Radiobutton(
            theme_row,
            text="Light",
            value="light",
            variable=self.theme_var,
            command=self._on_theme_change,
        ).pack(side=tk.LEFT, padx=(8, 0))

        scale_row = ttk.Frame(root)
        scale_row.pack(fill=tk.X, **pad)
        ttk.Label(scale_row, text="UI scale").pack(side=tk.LEFT)
        from link_bridge.config import _clamp_ui_scale
        from link_bridge.dpi import UI_SCALE_MAX, UI_SCALE_MIN, UI_SCALE_STEP

        self.ui_scale_var = tk.DoubleVar(
            value=_clamp_ui_scale(getattr(self.cfg, "ui_scale", 1.0))
        )
        self.ui_scale_label = tk.StringVar(
            value=f"{int(round(float(self.ui_scale_var.get()) * 100))}%"
        )
        self._ui_scale_after: str | None = None
        ttk.Button(
            scale_row, text="−", width=3, command=lambda: self._nudge_ui_scale(-UI_SCALE_STEP)
        ).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Label(scale_row, textvariable=self.ui_scale_label, width=5).pack(side=tk.LEFT)
        ttk.Button(
            scale_row, text="+", width=3, command=lambda: self._nudge_ui_scale(UI_SCALE_STEP)
        ).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Button(scale_row, text="Reset", width=6, command=self._reset_ui_scale).pack(
            side=tk.LEFT
        )
        ttk.Label(
            scale_row,
            text=f"{int(UI_SCALE_MIN * 100)}–{int(UI_SCALE_MAX * 100)}%   Ctrl+0 resets",
        ).pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(
            root,
            text=(
                "Clicks: Left = open full image · "
                "Middle = post to main group · "
                "Right = craft menu (Omnicraft, reshape, portal, daily cup, …)\n"
                "Search: name filter · @username [name] · all [name] (2+ letters)\n"
                "Images are never proxied by the bot — the PC fetches URLs directly."
            ),
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(10, 0))

        btn_row = ttk.Frame(root)
        btn_row.pack(fill=tk.X, **pad)
        self.connect_btn = ttk.Button(btn_row, text="Connect", command=self.start_bridge)
        self.connect_btn.pack(side=tk.LEFT)
        self.disconnect_btn = ttk.Button(
            btn_row, text="Disconnect", command=self.stop_bridge, state=tk.DISABLED
        )
        self.disconnect_btn.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_row, text="Save settings", command=self.save_settings).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(
            btn_row, text="Check for updates", command=lambda: self.check_updates(silent=False)
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_row, text="Source code", command=self._open_source).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        adv = ttk.LabelFrame(root, text="Server (usually leave as-is)", padding=8)
        adv.pack(fill=tk.X, **pad)
        ttk.Label(adv, text="Host").pack(anchor=tk.W)
        self.host_var = tk.StringVar(value=self.cfg.host)
        ttk.Entry(adv, textvariable=self.host_var).pack(fill=tk.X, pady=(0, 6))
        ttk.Label(adv, text="Port").pack(anchor=tk.W)
        self.port_var = tk.StringVar(value=str(self.cfg.port))
        ttk.Entry(adv, textvariable=self.port_var).pack(fill=tk.X)

        ttk.Label(root, text="Log").pack(anchor=tk.W, pady=(10, 0))
        self.log = tk.Text(root, height=8, wrap=tk.WORD, font="TkFixedFont")
        self.log.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.log.configure(state=tk.DISABLED)

    def _idle_status_text(self) -> str:
        if self.cfg.is_paired() or self.cfg.can_legacy_connect():
            return "Ready — Connect if not already connected."
        return "Not paired yet — follow Setup above."

    def _open_source(self) -> None:
        from link_bridge import SOURCE_URL
        from link_bridge.browser_open import open_url

        open_url(SOURCE_URL)
        self._append_log(f"Source: {SOURCE_URL}")

    def _autostart_initial(self) -> bool:
        try:
            from link_bridge import autostart

            live = autostart.is_enabled()
            if live != bool(self.cfg.autostart):
                self.cfg.autostart = live
            return live
        except Exception:
            return bool(self.cfg.autostart)

    def _pair_status_text(self) -> str:
        if self.cfg.is_paired():
            return f"Paired · device {self.cfg.device_id[:8]}…"
        if self.cfg.can_legacy_connect():
            return "Legacy token mode (your old install) — Pair to switch to device auth."
        return "Not paired."

    def _append_log(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"[{stamp}] {line}\n")
        # Cap the log so a long session does not bloat the Text widget.
        try:
            end_line = int(float(self.log.index("end-1c").split(".")[0]))
            if end_line > 250:
                self.log.delete("1.0", f"{end_line - 200}.0")
        except Exception:
            pass
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _ui(self, fn) -> None:
        self.after(0, fn)

    def _read_form_into_cfg(self) -> bool:
        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get().strip() or "0")
        except ValueError:
            messagebox.showerror("Harem Link Bridge", "Port must be a number.")
            return False
        if not host or port <= 0:
            messagebox.showinfo("Harem Link Bridge", "Host and port are required.")
            return False
        self.cfg.host = host
        self.cfg.port = port
        self.cfg.paused = bool(self.paused_var.get())
        self.cfg.pause_on_lock = bool(self.pause_on_lock_var.get())
        self.cfg.open_browser = bool(self.open_var.get())
        self.cfg.start_hidden = bool(self.hidden_var.get())
        self.cfg.autostart = bool(self.autostart_var.get())
        self.cfg.focus_telegram = bool(self.focus_tg_var.get())
        self.cfg.natural_thumbs = bool(self.natural_thumbs_var.get())
        self.cfg.prefer_original_open = bool(self.prefer_original_var.get())
        from link_bridge.config import _clamp_preview_scale, _clamp_scroll_speed
        from link_bridge.theme import normalize_theme

        self.cfg.preview_scale = _clamp_preview_scale(self.preview_scale_var.get())
        self.cfg.scroll_speed = _clamp_scroll_speed(self.scroll_speed_var.get())
        if hasattr(self, "ui_scale_var"):
            from link_bridge.config import _clamp_ui_scale

            self.cfg.ui_scale = _clamp_ui_scale(self.ui_scale_var.get())
        if hasattr(self, "theme_var"):
            self.cfg.ui_theme = normalize_theme(self.theme_var.get())
        self.cfg.ensure_device_id()
        return True

    def _on_theme_change(self) -> None:
        from link_bridge.theme import apply_app_theme, normalize_theme, schedule_theme_refresh

        mode = normalize_theme(self.theme_var.get())
        self.cfg.ui_theme = mode
        apply_app_theme(self, mode)
        from link_bridge.dpi import apply_ui_scale

        apply_ui_scale(self, getattr(self.cfg, "ui_scale", 1.0))
        schedule_theme_refresh(self, mode, delay_ms=120)
        host = getattr(self, "_omni_host", None)
        if host is not None:
            try:
                if host.winfo_exists():
                    host.apply_ui_theme(mode)
            except Exception:
                pass
        conjure = getattr(self, "_conjure", None)
        if conjure is not None:
            try:
                from link_bridge.theme import palette

                conjure.apply_ui_theme(palette(mode))
            except Exception:
                pass
        try:
            save_config(self.cfg)
        except Exception:
            pass
        self._append_log(f"Appearance → {mode}")

    def _nudge_ui_scale(self, delta: float) -> None:
        from link_bridge.config import _clamp_ui_scale

        nxt = _clamp_ui_scale(float(self.ui_scale_var.get() or 1.0) + float(delta))
        self.ui_scale_var.set(nxt)
        self._commit_ui_scale()

    def _reset_ui_scale(self) -> None:
        self.ui_scale_var.set(1.0)
        self._commit_ui_scale()

    def _commit_ui_scale(self) -> None:
        self._ui_scale_after = None
        from link_bridge.config import _clamp_ui_scale
        from link_bridge.dpi import apply_ui_scale

        scale = _clamp_ui_scale(self.ui_scale_var.get())
        self.ui_scale_var.set(scale)
        self.ui_scale_label.set(f"{int(round(scale * 100))}%")
        if abs(scale - float(getattr(self.cfg, "ui_scale", 1.0) or 1.0)) < 0.005:
            return
        self.cfg.ui_scale = scale
        from link_bridge.theme import apply_app_theme, schedule_theme_refresh

        apply_app_theme(self, self.cfg.ui_theme)
        apply_ui_scale(self, scale)
        schedule_theme_refresh(self, self.cfg.ui_theme, delay_ms=80)
        save_config(self.cfg)

    def _on_preview_scale_slide(self, _value=None) -> None:
        from link_bridge.config import _clamp_preview_scale

        scale = _clamp_preview_scale(self.preview_scale_var.get())
        self.preview_scale_label.set(f"{int(round(scale * 100))}%")
        if self._preview_scale_after is not None:
            try:
                self.after_cancel(self._preview_scale_after)
            except Exception:
                pass
        self._preview_scale_after = self.after(180, self._commit_preview_scale)

    def _reset_preview_scale(self) -> None:
        self.preview_scale_var.set(1.5)
        self._on_preview_scale_slide()
        self._commit_preview_scale()

    def _commit_preview_scale(self) -> None:
        self._preview_scale_after = None
        from link_bridge.config import _clamp_preview_scale

        scale = _clamp_preview_scale(self.preview_scale_var.get())
        self.preview_scale_var.set(scale)
        self.preview_scale_label.set(f"{int(round(scale * 100))}%")
        if abs(scale - float(self.cfg.preview_scale or 1.5)) < 0.01:
            return
        self.cfg.preview_scale = scale
        save_config(self.cfg)
        if self._roster is not None:
            self._roster.set_preview_scale(scale)

    def _on_scroll_speed_slide(self, _value=None) -> None:
        from link_bridge.config import _clamp_scroll_speed

        speed = _clamp_scroll_speed(self.scroll_speed_var.get())
        self.scroll_speed_label.set(f"{speed:.2f}×")
        if self._scroll_speed_after is not None:
            try:
                self.after_cancel(self._scroll_speed_after)
            except Exception:
                pass
        self._scroll_speed_after = self.after(180, self._commit_scroll_speed)

    def _reset_scroll_speed(self) -> None:
        self.scroll_speed_var.set(3.0)
        self._on_scroll_speed_slide()
        self._commit_scroll_speed()

    def _commit_scroll_speed(self) -> None:
        self._scroll_speed_after = None
        from link_bridge.config import _clamp_scroll_speed

        speed = _clamp_scroll_speed(self.scroll_speed_var.get())
        self.scroll_speed_var.set(speed)
        self.scroll_speed_label.set(f"{speed:.2f}×")
        if abs(speed - float(self.cfg.scroll_speed or 3.0)) < 0.01:
            return
        self.cfg.scroll_speed = speed
        save_config(self.cfg)
        if self._roster is not None:
            self._roster.set_scroll_speed(speed)

    def _on_focus_tg_toggle(self) -> None:
        self.cfg.focus_telegram = bool(self.focus_tg_var.get())
        save_config(self.cfg)
        self._append_log(
            "Focus Telegram: on." if self.cfg.focus_telegram else "Focus Telegram: off."
        )

    def _on_natural_thumbs_toggle(self) -> None:
        self.cfg.natural_thumbs = bool(self.natural_thumbs_var.get())
        save_config(self.cfg)
        try:
            self._roster.set_natural_thumbs(self.cfg.natural_thumbs)
        except Exception:
            pass
        self._append_log(
            "Thumbs: natural aspect."
            if self.cfg.natural_thumbs
            else "Thumbs: square crop."
        )

    def _on_prefer_original_toggle(self) -> None:
        self.cfg.prefer_original_open = bool(self.prefer_original_var.get())
        save_config(self.cfg)
        self._append_log(
            "Left-click: original file URL."
            if self.cfg.prefer_original_open
            else "Left-click: chat/sample URL."
        )

    def _save_text_edit_geometry(self, geo: str) -> None:
        text = (geo or "").strip()
        if not text or text == (self.cfg.text_edit_geometry or ""):
            return
        self.cfg.text_edit_geometry = text
        try:
            save_config(self.cfg)
        except Exception:
            pass

    def save_settings(self) -> None:
        if not self._read_form_into_cfg():
            return
        path = save_config(self.cfg)
        self.pair_status.set(self._pair_status_text())
        self.status_var.set(f"Saved {path.name}")
        self._append_log(f"Saved settings → {path}")

    def start_bridge(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self._read_form_into_cfg():
            return
        if not self.cfg.can_connect():
            messagebox.showinfo(
                "Harem Link Bridge",
                "Pair with Telegram first (or keep a legacy token config).",
            )
            return
        save_config(self.cfg)
        self.connect_btn.configure(state=tk.DISABLED)
        self.disconnect_btn.configure(state=tk.NORMAL)
        self.status_var.set("Starting…")
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop_bridge(self) -> None:
        client = self._client
        loop = self._loop
        if client is not None:
            client.request_stop()
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(lambda: None)
        self.connect_btn.configure(state=tk.NORMAL)
        self.disconnect_btn.configure(state=tk.DISABLED)
        self.status_var.set("Disconnecting…")

    def unpair(self) -> None:
        self.stop_bridge()
        self.cfg.device_token = ""
        # Keep device_id stable so re-pair is clean; rotate if desired later.
        save_config(self.cfg)
        self.pair_status.set(self._pair_status_text())
        self._append_log("Unpaired (device token cleared).")
        self.status_var.set("Unpaired. Pair again to connect.")

    def start_pair_deep_link(self) -> None:
        if not self._read_form_into_cfg():
            return
        save_config(self.cfg)
        self._pair_stop.clear()
        self._append_log("Starting Pair with Telegram…")
        threading.Thread(target=self._pair_deep_link_thread, daemon=True).start()

    def start_pair_code(self) -> None:
        if not self._read_form_into_cfg():
            return
        code = simpledialog.askstring(
            "Enter code",
            "Paste the code from /bridge in Telegram:",
            parent=self,
        )
        if not code:
            return
        save_config(self.cfg)
        threading.Thread(
            target=self._pair_claim_thread, args=(code.strip(),), daemon=True
        ).start()

    def _pair_deep_link_thread(self) -> None:
        async def _run() -> None:
            client = BridgeClient(self.cfg)
            pending = await client.pair_begin()
            if pending.get("op") != "pair_pending":
                raise RuntimeError(f"pair_begin failed: {pending}")
            code = pending["code"]
            link = pending.get("deep_link") or ""
            self._ui(
                lambda: (
                    self._append_log(f"Code {code} — open Telegram"),
                    self.status_var.set("Confirm in Telegram, waiting…"),
                )
            )
            if link.startswith("http"):
                webbrowser.open(link)
            for _ in range(90):
                if self._pair_stop.is_set():
                    return
                polled = await client.pair_poll(code)
                if polled.get("op") == "pair_ok" and polled.get("device_token"):
                    self.cfg.device_token = polled["device_token"]
                    self.cfg.device_id = polled.get("device_id") or self.cfg.device_id
                    # Drop legacy secrets once paired.
                    self.cfg.token = ""
                    self.cfg.user_id = 0
                    save_config(self.cfg)
                    self._ui(
                        lambda: (
                            self.pair_status.set(self._pair_status_text()),
                            self.status_var.set("Paired! Connecting…"),
                            self._append_log("Pair OK"),
                            self.start_bridge(),
                        )
                    )
                    return
                await asyncio.sleep(2.0)
            raise RuntimeError("Timed out waiting for Telegram confirm")

        try:
            asyncio.run(_run())
        except Exception as exc:
            self._ui(
                lambda: (
                    self.status_var.set(f"Pair failed: {exc}"),
                    self._append_log(f"Pair failed: {exc}"),
                )
            )

    def _pair_claim_thread(self, code: str) -> None:
        async def _run() -> None:
            client = BridgeClient(self.cfg)
            result = await client.pair_claim(code)
            if result.get("op") != "pair_ok" or not result.get("device_token"):
                raise RuntimeError(result.get("error") or repr(result))
            self.cfg.device_token = result["device_token"]
            self.cfg.device_id = result.get("device_id") or self.cfg.device_id
            self.cfg.token = ""
            self.cfg.user_id = 0
            save_config(self.cfg)
            self._ui(
                lambda: (
                    self.pair_status.set(self._pair_status_text()),
                    self.status_var.set("Paired! Connecting…"),
                    self._append_log("Pair OK (code)"),
                    self.start_bridge(),
                )
            )

        try:
            asyncio.run(_run())
        except Exception as exc:
            self._ui(
                lambda: (
                    self.status_var.set(f"Pair failed: {exc}"),
                    self._append_log(f"Pair failed: {exc}"),
                )
            )

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        def on_status(msg: str) -> None:
            def _apply() -> None:
                self.status_var.set(msg)
                self._append_log(msg)
                if msg.startswith("Connected") and self._roster is not None:
                    self._roster.load_page(0)
                    self._sync_themes_tab()

            self._ui(_apply)

        def on_open(url: str) -> None:
            self._ui(lambda: self._handle_open(url))

        def on_message(body: dict) -> None:
            if body.get("op") == "hello_ok":
                self._ui(self._sync_themes_tab)

        client = BridgeClient(
            self.cfg, on_status=on_status, on_open_url=on_open, on_message=on_message
        )
        self._client = client
        try:
            loop.run_until_complete(client.run_forever())
        finally:
            self._client = None
            self._loop = None
            try:
                loop.close()
            except Exception:
                pass
            self._ui(
                lambda: (
                    self.connect_btn.configure(state=tk.NORMAL),
                    self.disconnect_btn.configure(state=tk.DISABLED),
                    self._roster.clear() if self._roster else None,
                    self._hide_themes_tab(),
                )
            )

    def _schedule_coro(self, coro_factory, on_ok, on_err) -> None:
        client = self._client
        loop = self._loop
        if client is None or loop is None or not loop.is_running():
            on_err(RuntimeError("not connected"))
            return

        def _go() -> None:
            async def _run() -> None:
                try:
                    result = await coro_factory(client)
                except Exception as exc:
                    err = exc
                    self._ui(lambda e=err: on_err(e))
                    return
                self._ui(lambda r=result: on_ok(r))

            asyncio.create_task(_run())

        loop.call_soon_threadsafe(_go)

    def _roster_fetch_page(
        self, page: int, q: str, done: int, set_name: str, on_ok, on_err, *, kind: str = ""
    ) -> None:
        from link_bridge.roster import PAGE_SIZE

        query = (q or "").strip()
        done_flag = int(done)
        set_n = (set_name or "").strip()
        kind_s = (kind or "").strip()
        self._schedule_coro(
            lambda c: c.request_roster_page(
                page,
                PAGE_SIZE,
                q=query,
                done=done_flag,
                set_name=set_n,
                kind=kind_s,
            ),
            on_ok,
            on_err,
        )

    def _roster_fetch_tamed(self, page: int, q: str, on_ok, on_err) -> None:
        from link_bridge.tamed import TAMED_PAGE_SIZE

        query = (q or "").strip()
        self._schedule_coro(
            lambda c: c.request_roster_page(
                page, TAMED_PAGE_SIZE, q=query, done=0, kind="tamed"
            ),
            on_ok,
            on_err,
        )

    def _market_fetch_page(
        self, page: int, q: str, min_price: str, max_price: str, on_ok, on_err
    ) -> None:
        from link_bridge.roster import PAGE_SIZE

        self._schedule_coro(
            lambda c: c.request_market_page(
                page,
                PAGE_SIZE,
                q=(q or "").strip(),
                min_price=(min_price or "").strip(),
                max_price=(max_price or "").strip(),
            ),
            on_ok,
            on_err,
        )

    def _market_buy(self, listing_id: int, on_ok, on_err) -> None:
        self._schedule_coro(
            lambda c: c.request_market_buy(int(listing_id)),
            on_ok,
            on_err,
        )

    def _sets_list(self, user: str, on_ok, on_err) -> None:
        self._schedule_coro(
            lambda c: c.request_sets_list(user=user or ""),
            on_ok,
            on_err,
        )

    def _sets_rename(self, old: str, new: str, on_ok, on_err) -> None:
        self._schedule_coro(
            lambda c: c.request_sets_rename(old, new),
            on_ok,
            on_err,
        )

    def _sets_delete(self, name: str, on_ok, on_err) -> None:
        self._schedule_coro(
            lambda c: c.request_sets_delete(name),
            on_ok,
            on_err,
        )

    def _sets_present(self, name: str, on_ok, on_err) -> None:
        target = (self.cfg.middle_click_target or "group").strip().lower()
        self._schedule_coro(
            lambda c: c.request_sets_present(name, target=target),
            on_ok,
            on_err,
        )

    def _browse_users(self, kind: str, on_ok, on_err) -> None:
        k = (kind or "roster").strip().lower()
        self._schedule_coro(lambda c: c.request_browse_users(k), on_ok, on_err)

    def _set_middle_click_target(self, target: str) -> None:
        from link_bridge.config import save_config

        dest = (target or "group").strip().lower()
        self.cfg.middle_click_target = "dm" if dest == "dm" else "group"
        try:
            save_config(self.cfg)
        except Exception:
            pass
        if self._roster is not None and hasattr(self._roster, "sync_target_buttons"):
            self._roster.sync_target_buttons()

    def _set_left_click_omni(self, enabled: bool) -> None:
        from link_bridge.config import save_config

        self.cfg.left_click_omni = bool(enabled)
        try:
            save_config(self.cfg)
        except Exception:
            pass
        if self._roster is not None and hasattr(self._roster, "sync_lmb_omni_button"):
            self._roster.sync_lmb_omni_button()

    def _set_hide_in_any_set(self, enabled: bool) -> None:
        from link_bridge.config import save_config

        self.cfg.hide_in_any_set = bool(enabled)
        try:
            save_config(self.cfg)
        except Exception:
            pass
        if self._roster is not None and hasattr(
            self._roster, "sync_hide_in_any_set_button"
        ):
            self._roster.sync_hide_in_any_set_button()

    def _roster_open_omni(self, char_id: int, on_ok, on_err) -> None:
        self._open_omni_ui(int(char_id))
        on_ok({"op": "open_omni_ok", "char_id": int(char_id)})

    def _omni_fetch_state(self, char_id: int, on_ok, on_err, mode: str = "omni") -> None:
        self._schedule_coro(
            lambda c: c.request_omni_state(int(char_id), mode=mode),
            on_ok,
            on_err,
        )

    def _omni_tap(
        self, char_id: int, craft: str, arg: str | None, on_ok, on_err, mode: str = "omni"
    ) -> None:
        self._schedule_coro(
            lambda c: c.request_omni_tap(int(char_id), craft, arg, mode=mode),
            on_ok,
            on_err,
        )

    def _omni_dm_preview(self, char_id: int, on_ok, on_err) -> None:
        """Post plain max-res card into bot DMs (checkres-style, no omni keyboard)."""
        self._roster_dm_craft(int(char_id), "card", on_ok, on_err)

    def _open_omni_ui(self, char_id: int, mode: str = "omni") -> None:
        from link_bridge.omni import OmniHost

        host = getattr(self, "_omni_host", None)
        if host is None or not host.winfo_exists():
            host = OmniHost(
                self,
                fetch_state=self._omni_fetch_state,
                tap=self._omni_tap,
                beep_get=lambda: bool(self.cfg.omni_beep),
                beep_set=self._set_omni_beep,
                prefer_original=lambda: bool(self.cfg.prefer_original_open),
                full_image_get=lambda: bool(self.cfg.omni_full_image),
                full_image_set=self._set_omni_full_image,
                get_window_geo=lambda: str(self.cfg.omni_window_geometry or ""),
                set_window_geo=self._save_omni_window_geometry,
                get_text_geo=lambda: str(self.cfg.text_edit_geometry or ""),
                set_text_geo=self._save_text_edit_geometry,
                on_log=self._append_log,
                on_done_changed=self._on_omni_done_changed,
                fetch_undone=self._omni_fetch_undone,
                dm_preview=self._omni_dm_preview,
                on_media_changed=self._on_omni_media_changed,
            )
            self._omni_host = host

            def _drop(event=None) -> None:
                if event is not None and getattr(event, "widget", None) is not host:
                    return
                try:
                    if self._roster is not None:
                        self._roster.flush_omni_media()
                except Exception:
                    logger.debug("omni media flush failed", exc_info=True)
                self._omni_host = None

            host.bind("<Destroy>", _drop)
        host.open_card(int(char_id), mode=mode)

    def _omni_fetch_undone(self, on_ok, on_err, *, exclude_id: int = 0) -> None:
        from link_bridge.roster import PAGE_SIZE

        def _ok(body: dict) -> None:
            # Drop the just-finished card client-side if still listed.
            if exclude_id and isinstance(body.get("items"), list):
                body = dict(body)
                body["items"] = [
                    it
                    for it in body["items"]
                    if int(it.get("id") or 0) != int(exclude_id)
                ]
            on_ok(body)

        self._schedule_coro(
            lambda c: c.request_roster_page(
                0,
                PAGE_SIZE,
                q="",
                done=0,
                set_name="",
                kind="undone",
            ),
            _ok,
            on_err,
        )

    def _on_omni_done_changed(self, char_id: int, done: bool) -> None:
        if self._roster is None:
            return
        try:
            if done and getattr(self._roster, "_mode", "") == "undone":
                self._roster.remove_char_from_view(int(char_id))
            elif (not done) and getattr(self._roster, "_mode", "") == "done":
                self._roster.remove_char_from_view(int(char_id))
        except Exception:
            logger.debug("omni done sync failed", exc_info=True)

    def _on_omni_media_changed(self, char_id: int, media: dict) -> None:
        if self._roster is None:
            return
        try:
            self._roster.note_char_media(int(char_id), dict(media or {}))
        except Exception:
            logger.debug("omni media note failed", exc_info=True)

    def _set_omni_beep(self, flag: bool) -> None:
        self.cfg.omni_beep = bool(flag)
        try:
            save_config(self.cfg)
        except Exception:
            pass

    def _set_omni_full_image(self, flag: bool) -> None:
        self.cfg.omni_full_image = bool(flag)
        try:
            save_config(self.cfg)
        except Exception:
            pass

    def _save_omni_window_geometry(self, geo: str) -> None:
        text = (geo or "").strip()
        if not text or text == (self.cfg.omni_window_geometry or ""):
            return
        self.cfg.omni_window_geometry = text
        try:
            save_config(self.cfg)
        except Exception:
            pass

    def _roster_post_grid(self, char_id: int, on_ok, on_err) -> None:
        target = (self.cfg.middle_click_target or "group").strip().lower()
        self._schedule_coro(
            lambda c: c.request_post_grid(char_id, target=target),
            on_ok,
            on_err,
        )

    def _roster_register_cup(self, char_id: int, on_ok, on_err) -> None:
        self._schedule_coro(
            lambda c: c.request_register_cup(char_id),
            on_ok,
            on_err,
        )

    def _roster_dm_craft(self, char_id: int, craft: str, on_ok, on_err) -> None:
        action = (craft or "omni").strip() or "omni"
        self._schedule_coro(
            lambda c: c.request_dm_craft(char_id, action),
            on_ok,
            on_err,
        )

    def _themes_fetch(self, on_ok, on_err) -> None:
        self._schedule_coro(lambda c: c.request_themes_list(), on_ok, on_err)

    def _themes_save(self, main, secondary, on_ok, on_err) -> None:
        self._schedule_coro(
            lambda c: c.request_themes_save(main, secondary),
            on_ok,
            on_err,
        )

    def _sync_themes_tab(self) -> None:
        client = self._client
        allowed = bool(client and getattr(client, "themes_admin", False))
        if allowed:
            self._ensure_themes_tab()
        else:
            self._hide_themes_tab()

    def _ensure_themes_tab(self) -> None:
        if self._themes_tab is not None:
            return
        from link_bridge.themes_admin import ThemesAdminPanel

        tab = ttk.Frame(self._main_nb, padding=6)
        panel = ThemesAdminPanel(
            tab,
            fetch=self._themes_fetch,
            save=self._themes_save,
            on_log=self._append_log,
        )
        panel.pack(fill=tk.BOTH, expand=True)
        # Keep Conjure after Themes when both exist.
        if self._conjure_tab is not None:
            try:
                self._main_nb.insert(self._conjure_tab, tab, text="Themes")
            except Exception:
                self._main_nb.add(tab, text="Themes")
        else:
            self._main_nb.add(tab, text="Themes")
        self._themes_tab = tab
        self._themes = panel
        try:
            self._themes_tab_index = self._main_nb.index(tab)
        except Exception:
            self._themes_tab_index = None
        self._append_log("Themes admin tab unlocked.")
        panel.reload()

    def _hide_themes_tab(self) -> None:
        tab = self._themes_tab
        if tab is None:
            return
        try:
            self._main_nb.forget(tab)
        except Exception:
            pass
        try:
            tab.destroy()
        except Exception:
            pass
        self._themes_tab = None
        self._themes = None
        self._themes_tab_index = None

    def _ensure_conjure_tab(self) -> None:
        if self._conjure_tab is not None:
            return
        try:
            from conjure_finder.bootstrap import apply_env, set_app_root
            from conjure_finder.gui import ConjureFinderApp
            from conjure_finder.settings import apply_settings_file, settings_status
            from link_bridge.config import app_dir
        except Exception as exc:
            self._append_log(f"Conjure Finder tab unavailable: {exc}")
            return

        try:
            from link_bridge.ssl_certs import ensure_ssl_certs

            ensure_ssl_certs()
            set_app_root(app_dir())
            apply_env()
            apply_settings_file()
        except Exception as exc:
            self._append_log(f"Conjure Finder env load failed: {exc}")

        tab = ttk.Frame(self._main_nb, padding=2)
        try:
            panel = ConjureFinderApp(tab, embedded=True)
            panel.pack(fill=tk.BOTH, expand=True)
        except Exception as exc:
            self._append_log(f"Conjure Finder failed to start: {exc}")
            try:
                tab.destroy()
            except Exception:
                pass
            return

        self._main_nb.add(tab, text="Conjure")
        self._conjure_tab = tab
        self._conjure = panel
        panel.set_browser_result_handler(self._send_conjure_result_dm)
        try:
            from link_bridge.theme import normalize_theme, palette

            panel.apply_ui_theme(palette(normalize_theme(self.cfg.ui_theme)))
        except Exception:
            try:
                panel._sync_bridge_theme()
            except Exception:
                pass
        try:
            st = settings_status()
            if not (st.get("danbooru") or st.get("rule34")):
                panel.status_var.set(
                    "No API keys found — open Settings… (uses conjure_finder.env "
                    "next to this Bridge config)."
                )
        except Exception:
            pass
        self._append_log("Conjure Finder tab ready.")

    def _start_browser_hook(self) -> None:
        if not bool(getattr(self.cfg, "browser_hook_enabled", True)):
            return
        if self._browser_hook is not None and self._browser_hook.running:
            return
        from link_bridge.browser_hook import BrowserHookServer

        port = int(getattr(self.cfg, "browser_hook_port", 8767) or 8767)
        self._browser_hook = BrowserHookServer(port, self._on_browser_post_url)
        try:
            self._browser_hook.start()
            self._append_log(f"Browser hook listening on http://127.0.0.1:{port}/send")
        except OSError as exc:
            self._append_log(f"Browser hook failed on port {port}: {exc}")

    def _on_browser_post_url(self, url: str, meta: dict | None = None) -> None:
        text = (url or "").strip()
        if not text:
            return
        meta = meta or {}
        from_browser = str(meta.get("source") or "").strip().lower() == "browser"
        action = str(meta.get("action") or "both").strip().lower()
        if action not in ("checkres", "conjure", "both"):
            action = "both"
        do_checkres = action in ("checkres", "both")
        do_conjure = action in ("conjure", "both")

        def _go() -> None:
            self._append_log(f"Browser [{action}] → {text[:88]}")
            if do_conjure:
                self._ensure_conjure_tab()
                if self._conjure_tab is not None:
                    try:
                        self._main_nb.select(self._conjure_tab)
                    except Exception:
                        pass
                if self._conjure is not None:
                    try:
                        self._conjure.process_incoming_url(
                            text, from_browser=from_browser and do_conjure
                        )
                    except Exception as exc:
                        self._append_log(f"Conjure Finder failed: {exc}")
                else:
                    self._append_log(
                        "Conjure Finder skipped — open the Conjure tab or check the log "
                        "for startup errors."
                    )

            if do_checkres:

                def on_ok(body: dict) -> None:
                    self._append_log(f"checkres: {body.get('detail') or 'ok'}")

                def on_err(exc: BaseException) -> None:
                    self._append_log(f"checkres failed: {exc}")

                self._schedule_coro(
                    lambda c: c.request_checkres_url(text),
                    on_ok,
                    on_err,
                )

        self._ui(_go)

    def _send_conjure_result_dm(self, url: str, text: str, command: str = "") -> None:
        body = (text or "").strip()
        if not body:
            return
        cmd = (command or "").strip()

        def on_ok(resp: dict) -> None:
            self._append_log(f"conjure DM: {resp.get('detail') or 'ok'}")

        def on_err(exc: BaseException) -> None:
            self._append_log(f"conjure DM failed: {exc}")

        self._schedule_coro(
            lambda c: c.request_conjure_result_dm(url, body, command=cmd),
            on_ok,
            on_err,
        )

    def _restore_window_state(self) -> None:
        if not self._want_zoomed:
            return
        try:
            if str(self.state()) != "zoomed":
                self.state("zoomed")
        except Exception:
            pass

    def _enable_geo_persist(self) -> None:
        self._geo_ready = True
        self._persist_window_geometry()

    def _on_window_configure(self, _event=None) -> None:
        # Persist maximize/restore without waiting for quit (force-kill used to lose it).
        if not self._geo_ready or self._quitting or not self.winfo_viewable():
            return
        if self._geo_save_after is not None:
            try:
                self.after_cancel(self._geo_save_after)
            except Exception:
                pass
        self._geo_save_after = self.after(400, self._persist_window_geometry)

    def _persist_window_geometry(self) -> None:
        self._geo_save_after = None
        try:
            state = str(self.state() or "normal")
        except Exception:
            state = "normal"
        # When zoomed, geometry() is often unreliable — keep last normal size.
        try:
            if state == "zoomed":
                geo = (self.cfg.window_geometry or "").strip() or self.geometry()
            else:
                geo = self.geometry()
        except Exception:
            return
        changed = False
        if geo and geo != self.cfg.window_geometry:
            self.cfg.window_geometry = geo
            changed = True
        norm_state = "zoomed" if state == "zoomed" else "normal"
        self._want_zoomed = norm_state == "zoomed"
        if norm_state != (self.cfg.window_state or "normal"):
            self.cfg.window_state = norm_state
            changed = True
        if changed:
            save_config(self.cfg)

    def _handle_open(self, url: str) -> None:
        self._append_log(f"Open: {url}")
        if self.open_var.get():
            try:
                from link_bridge.browser_open import open_url

                open_url(url)
            except Exception as exc:
                self._append_log(f"browser open failed: {exc}")

    def _on_pause_toggle(self) -> None:
        if self._lock_ctrl is not None:
            self._lock_ctrl.on_manual_change()
        paused = bool(self.paused_var.get())
        self.cfg.paused = paused
        save_config(self.cfg)
        self._apply_paused_to_client(paused)

    def _apply_paused_to_client(self, paused: bool) -> None:
        client = self._client
        loop = self._loop
        if client is None or loop is None or not loop.is_running():
            return

        def _go() -> None:
            asyncio.create_task(client.set_paused(paused))

        loop.call_soon_threadsafe(_go)

    def _on_pause_on_lock_toggle(self) -> None:
        enabled = bool(self.pause_on_lock_var.get())
        self.cfg.pause_on_lock = enabled
        save_config(self.cfg)
        self._sync_pause_on_lock_watcher()
        self._append_log(
            "Pause when locked: on." if enabled else "Pause when locked: off."
        )

    def _sync_pause_on_lock_watcher(self) -> None:
        from link_bridge.session_lock import (
            LockPauseController,
            SessionLockWatcher,
        )

        enabled = bool(self.cfg.pause_on_lock)
        if not enabled:
            if self._lock_watcher is not None:
                self._lock_watcher.stop()
                self._lock_watcher = None
            if self._lock_ctrl is not None and self._lock_ctrl.auto_paused:
                self._lock_ctrl.auto_paused = False
                self.paused_var.set(False)
                self.cfg.paused = False
                save_config(self.cfg)
                self._apply_paused_to_client(False)
                self._append_log("Resumed (pause-when-locked turned off).")
            self._lock_ctrl = None
            return

        if self._lock_ctrl is None:
            self._lock_ctrl = LockPauseController()
        if self._lock_watcher is None or not self._lock_watcher.running:
            if self._lock_watcher is not None:
                self._lock_watcher.stop()
            self._lock_watcher = SessionLockWatcher(
                on_lock=lambda: self._ui(self._on_session_locked),
                on_unlock=lambda: self._ui(self._on_session_unlocked),
            )
            if not self._lock_watcher.start():
                self._append_log("Pause when locked unavailable on this OS.")
                self.pause_on_lock_var.set(False)
                self.cfg.pause_on_lock = False
                save_config(self.cfg)
                self._lock_watcher = None
                return
        # Do not probe OpenInputDesktop here — it false-positives and sticky-pauses.
        # Only WTS lock/unlock notifications drive auto-pause.

    def _on_session_locked(self) -> None:
        if self._lock_ctrl is None or not self.cfg.pause_on_lock:
            return
        desired = self._lock_ctrl.on_lock(bool(self.paused_var.get()))
        if desired is None:
            return
        self.paused_var.set(desired)
        self.cfg.paused = desired
        save_config(self.cfg)
        self._apply_paused_to_client(desired)
        self._append_log("Paused (PC locked).")

    def _on_session_unlocked(self) -> None:
        if self._lock_ctrl is None or not self.cfg.pause_on_lock:
            return
        desired = self._lock_ctrl.on_unlock()
        if desired is None:
            return
        self.paused_var.set(desired)
        self.cfg.paused = desired
        save_config(self.cfg)
        self._apply_paused_to_client(desired)
        self._append_log("Resumed (PC unlocked).")

    def _on_start_hidden_toggle(self) -> None:
        self.cfg.start_hidden = bool(self.hidden_var.get())
        save_config(self.cfg)

    def _on_autostart_toggle(self) -> None:
        enabled = bool(self.autostart_var.get())
        try:
            from link_bridge import autostart

            autostart.set_enabled(enabled)
        except Exception as exc:
            self.autostart_var.set(not enabled)
            messagebox.showerror(
                "Harem Link Bridge", f"Could not change Windows startup:\n{exc}"
            )
            return
        self.cfg.autostart = enabled
        if enabled and not self.hidden_var.get():
            # Boot quietly into tray by default.
            self.hidden_var.set(True)
            self.cfg.start_hidden = True
        save_config(self.cfg)
        self._append_log(
            "Starts with Windows." if enabled else "Removed from Windows startup."
        )

    def check_updates(self, *, silent: bool = False) -> None:
        if not self._read_form_into_cfg():
            return
        threading.Thread(
            target=self._check_updates_thread, args=(silent,), daemon=True
        ).start()

    def _check_updates_thread(self, silent: bool) -> None:
        from link_bridge import __version__
        from link_bridge.updater import check_for_update, run_update

        try:
            info = check_for_update(self.cfg)
        except Exception as exc:
            if not silent:
                self._ui(
                    lambda: messagebox.showerror(
                        "Harem Link Bridge", f"Update check failed:\n{exc}"
                    )
                )
            return
        if info is None:
            if not silent:
                self._ui(
                    lambda: (
                        self.status_var.set(f"Up to date (v{__version__})"),
                        self._append_log(f"Up to date (v{__version__})"),
                        messagebox.showinfo(
                            "Harem Link Bridge", f"You're on the latest version (v{__version__})."
                        ),
                    )
                )
            return

        def _ask() -> None:
            self._append_log(f"Update available: v{info.version}")
            ok = messagebox.askyesno(
                "Harem Link Bridge",
                f"Version {info.version} is available (you have {__version__}).\n\n"
                "Download and install now? (~1–2 min on a normal connection)\n\n"
                "A progress window will show % and MB. The app will close "
                "when ready — then start HaremLinkBridge.exe yourself "
                "(the install folder will open).",
            )
            if not ok:
                return
            threading.Thread(
                target=self._download_update_thread, args=(info,), daemon=True
            ).start()

        self._ui(_ask)

    def _download_update_thread(self, info) -> None:
        from link_bridge.updater import run_update
        import sys

        dialog_holder: list[UpdateProgressDialog | None] = [None]
        ready = threading.Event()

        def _open_dialog() -> None:
            dialog_holder[0] = UpdateProgressDialog(self, info.version)
            ready.set()

        self._ui(_open_dialog)
        ready.wait(timeout=10.0)
        dlg = dialog_holder[0]

        last_pct = [-1]

        def _progress(done: int, total: int) -> None:
            if dlg is None:
                return
            if total > 0:
                pct = min(99, int(100 * done / total))
                if pct == last_pct[0] and done > 0:
                    return
                last_pct[0] = pct
            self._ui(lambda d=done, t=total: dlg.set_progress(d, t))

        def _status(msg: str) -> None:
            if dlg is None:
                return
            self._ui(lambda m=msg: dlg.set_status(m))

        try:
            run_update(self.cfg, info, on_progress=_progress, on_status=_status)
        except Exception as exc:
            if dlg is not None:
                self._ui(dlg.destroy)
            self._ui(
                lambda: (
                    self.status_var.set(f"Update failed: {exc}"),
                    self._append_log(f"Update failed: {exc}"),
                    messagebox.showerror("Harem Link Bridge", f"Update failed:\n{exc}"),
                )
            )
            return
        if dlg is not None:
            self._ui(dlg.destroy)
        if getattr(sys, "frozen", False):
            # Give the updater script a moment to attach before we tear down.
            def _done() -> None:
                self.status_var.set(f"Installed v{info.version} — start again.")
                self._append_log(f"Installed v{info.version}; waiting for manual start")
                messagebox.showinfo(
                    "Harem Link Bridge",
                    f"Version {info.version} is ready.\n\n"
                    "This window will close. Then double-click HaremLinkBridge.exe "
                    "in the folder that opens (or use your usual shortcut).",
                )
                self.after(400, self.quit_app)

            self._ui(_done)
        else:
            self._ui(
                lambda: messagebox.showinfo(
                    "Harem Link Bridge",
                    f"Downloaded v{info.version} (dev mode — not applying).",
                )
            )

    def _setup_tray(self) -> None:
        from link_bridge.tray import start_tray

        self._tray = start_tray(
            on_show=lambda: self._ui(self._show_window),
            on_toggle_pause=lambda: self._ui(self._tray_toggle_pause),
            on_quit=lambda: self._ui(self.quit_app),
            on_check_updates=lambda: self._ui(lambda: self.check_updates(silent=False)),
        )
        if self._tray is None:
            self._append_log("Tray unavailable (install pystray+Pillow on this PC).")

    def _show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()
        self.after_idle(self._restore_window_state)

    def _tray_toggle_pause(self) -> None:
        self.paused_var.set(not self.paused_var.get())
        self._on_pause_toggle()

    def _on_close_to_tray(self) -> None:
        self._persist_window_geometry()
        if self._tray is not None:
            self.withdraw()
            self.status_var.set("Hidden in tray.")
            return
        self.quit_app()

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._persist_window_geometry()
        self._pair_stop.set()
        if self._lock_watcher is not None:
            try:
                self._lock_watcher.stop()
            except Exception:
                pass
            self._lock_watcher = None
        if self._browser_hook is not None:
            try:
                self._browser_hook.stop()
            except Exception:
                pass
            self._browser_hook = None
        self.stop_bridge()
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        self.destroy()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from link_bridge.dpi import enable_dpi_awareness

    enable_dpi_awareness()
    app = LinkBridgeApp()
    app.mainloop()
