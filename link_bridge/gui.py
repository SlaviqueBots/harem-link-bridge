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


class LinkBridgeApp(tk.Tk):
    def __init__(self, cfg: BridgeConfig | None = None) -> None:
        super().__init__()
        self.title(f"Harem Link Bridge  v{__version__}")
        self.minsize(560, 500)
        self.geometry("640x560")

        self.cfg = cfg or load_config()
        self.cfg.ensure_device_id()
        self._client: BridgeClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._tray = None
        self._quitting = False
        self._pair_stop = threading.Event()

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close_to_tray)
        self.after(200, self._setup_tray)
        if self.cfg.start_hidden:
            self.after(300, self.withdraw)
        if self.cfg.can_connect():
            self.after(400, self.start_bridge)
        if self.cfg.check_updates:
            self.after(1500, lambda: self.check_updates(silent=True))

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

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
            text="Start with Windows",
            variable=self.autostart_var,
            command=self._on_autostart_toggle,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            opts2,
            text="Start in tray",
            variable=self.hidden_var,
            command=self._on_start_hidden_toggle,
        ).pack(side=tk.LEFT, padx=(12, 0))

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

        ttk.Label(root, text="Status").pack(anchor=tk.W)
        self.status_var = tk.StringVar(value=self._idle_status_text())
        ttk.Label(root, textvariable=self.status_var, wraplength=600).pack(
            anchor=tk.W, fill=tk.X
        )

        ttk.Label(root, text="Log").pack(anchor=tk.W, pady=(10, 0))
        self.log = tk.Text(root, height=10, wrap=tk.WORD, font=("Consolas", 10))
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
        self.cfg.open_browser = bool(self.open_var.get())
        self.cfg.start_hidden = bool(self.hidden_var.get())
        self.cfg.autostart = bool(self.autostart_var.get())
        self.cfg.ensure_device_id()
        return True

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
            self._ui(lambda: (self.status_var.set(msg), self._append_log(msg)))

        def on_open(url: str) -> None:
            self._ui(lambda: self._handle_open(url))

        client = BridgeClient(self.cfg, on_status=on_status, on_open_url=on_open)
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
                )
            )

    def _handle_open(self, url: str) -> None:
        self._append_log(f"Open: {url}")
        if self.open_var.get():
            try:
                from link_bridge.browser_open import open_url

                open_url(url)
            except Exception as exc:
                self._append_log(f"browser open failed: {exc}")

    def _on_pause_toggle(self) -> None:
        paused = bool(self.paused_var.get())
        self.cfg.paused = paused
        save_config(self.cfg)
        client = self._client
        loop = self._loop
        if client is None or loop is None or not loop.is_running():
            return

        def _go() -> None:
            asyncio.create_task(client.set_paused(paused))

        loop.call_soon_threadsafe(_go)

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
                "Download and install now?\n\n"
                "The app will close. After that, start HaremLinkBridge.exe yourself "
                "(the install folder will open).",
            )
            if not ok:
                return
            self.status_var.set(f"Downloading v{info.version}…")
            threading.Thread(
                target=self._download_update_thread, args=(info,), daemon=True
            ).start()

        self._ui(_ask)

    def _download_update_thread(self, info) -> None:
        from link_bridge.updater import run_update
        import sys

        try:
            run_update(self.cfg, info)
        except Exception as exc:
            self._ui(
                lambda: (
                    self.status_var.set(f"Update failed: {exc}"),
                    self._append_log(f"Update failed: {exc}"),
                    messagebox.showerror("Harem Link Bridge", f"Update failed:\n{exc}"),
                )
            )
            return
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

    def _tray_toggle_pause(self) -> None:
        self.paused_var.set(not self.paused_var.get())
        self._on_pause_toggle()

    def _on_close_to_tray(self) -> None:
        if self._tray is not None:
            self.withdraw()
            self.status_var.set("Hidden in tray.")
            return
        self.quit_app()

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._pair_stop.set()
        self.stop_bridge()
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        self.destroy()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = LinkBridgeApp()
    app.mainloop()
