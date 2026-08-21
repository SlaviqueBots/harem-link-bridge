"""Simple tkinter GUI for the conjure finder (English UI)."""

from __future__ import annotations

import asyncio
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog, ttk
from typing import Any

from conjure_finder import __version__
from conjure_finder.bulk import BulkResult
from conjure_finder.urls import flatten_wishlist_urls, split_url_jobs


def _format_elapsed(sec: float) -> str:
    if sec < 10:
        return f"{sec:.1f}s"
    if sec < 60:
        return f"{sec:.0f}s"
    m, s = divmod(int(sec), 60)
    return f"{m}m{s:02d}s"


def _format_result(index: int, total: int, result: Any) -> str:
    """Compact one-block summary (no notes / warnings section)."""
    ids = tuple(getattr(result, "post_ids", None) or ()) or (result.post_id,)
    id_part = "|".join(f"#{i}" for i in ids)
    head = f"[{index}/{total}] {result.source} {id_part}"
    if len(ids) > 1:
        head += f"  any-of {len(ids)}"
    if result.file_ext:
        head += f"  .{result.file_ext}"
    head += f"  tags={result.tags_on_post}  checked={result.checked}"
    elapsed = getattr(result, "elapsed_sec", 0) or 0
    if elapsed > 0:
        head += f"  in {_format_elapsed(elapsed)}"
    if not result.best:
        return head + "\n  (no usable conjure option)\n"
    opt = result.best
    g = "YES" if opt.guaranteed else "no"
    path = getattr(opt, "path", "conjure") or "conjure"
    lines = [head]
    for cmd_line in (opt.command or "").splitlines():
        lines.append(f"  {cmd_line}")
    lines.append(
        f"  path {path}  cost {opt.cost}  pool {opt.pool_size}  guarantee {g}  "
        f"~{opt.expected_sessions:.1f} steps / ~{opt.expected_currency:.0f} cur"
    )
    return "\n".join(lines) + "\n"


def _format_error(index: int, total: int, label: str, message: str) -> str:
    short = label if len(label) <= 72 else label[:69] + "…"
    return f"[{index}/{total}] ERROR  {short}\n  {message}\n"


class ConjureFinderApp(ttk.Frame):
    """Find / Bulk wishlist UI — standalone window or Bridge tab."""

    def __init__(self, master: tk.Misc, *, embedded: bool = False) -> None:
        super().__init__(master)
        self._embedded = bool(embedded)
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._last_commands: list[str] = []
        self._last_bulk: BulkResult | None = None
        self._update_busy = False
        self._photo_refs: list[Any] = []

        self._build()
        self._install_clipboard_bindings()
        self._apply_mode()
        if self._embedded:
            self.after_idle(self._sync_bridge_theme)
        if not self._embedded:
            from conjure_finder.updater import load_update_config

            if load_update_config().check_updates:
                self.after(1500, lambda: self.check_updates(silent=True))

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        mode_row = ttk.Frame(root)
        mode_row.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(mode_row, text="Mode").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="find")
        ttk.Radiobutton(
            mode_row,
            text="Find",
            variable=self.mode_var,
            value="find",
            command=self._apply_mode,
        ).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Radiobutton(
            mode_row,
            text="Bulk wishlist",
            variable=self.mode_var,
            value="bulk",
            command=self._apply_mode,
        ).pack(side=tk.LEFT, padx=(8, 0))

        self.hint_var = tk.StringVar()
        self.hint_label = ttk.Label(root, textvariable=self.hint_var)
        self.hint_label.pack(anchor=tk.W, pady=(8, 0))

        url_frame = ttk.Frame(root)
        url_frame.pack(fill=tk.BOTH, **pad)
        self.url_text = tk.Text(url_frame, height=5, wrap=tk.WORD, font=("Consolas", 10))
        self.url_text.pack(fill=tk.BOTH, expand=True)
        self.url_scroll = None
        if self._embedded:
            # Themes-style: no scrollbar widget (Windows trough stays too bright).
            self.url_text.bind("<MouseWheel>", self._on_url_wheel)
        else:
            self.url_scroll = self._make_vscroll(url_frame, self.url_text.yview)
            self.url_scroll.pack(side=tk.RIGHT, fill=tk.Y)
            self.url_text.pack_forget()
            self.url_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.url_text.configure(yscrollcommand=self.url_scroll.set)
        self.url_text.bind("<Control-Return>", lambda _e: self.start_search())

        self.own_row = ttk.Frame(root)
        self.own_character_var = tk.BooleanVar(value=False)
        self.own_author_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.own_row,
            text="Already have this character",
            variable=self.own_character_var,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            self.own_row,
            text="Already have this author",
            variable=self.own_author_var,
        ).pack(side=tk.LEFT, padx=(12, 0))

        btn_row = ttk.Frame(root)
        btn_row.pack(fill=tk.X, **pad)
        self.btn_row = btn_row
        self.find_btn = ttk.Button(btn_row, text="Find cheapest conjure", command=self.start_search)
        self.find_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(
            btn_row, text="Cancel", command=self.cancel_search, state=tk.DISABLED
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.settings_btn = ttk.Button(btn_row, text="Settings…", command=self.open_settings)
        self.settings_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.update_btn = ttk.Button(
            btn_row, text="Check for updates", command=lambda: self.check_updates(silent=False)
        )
        if not self._embedded:
            self.update_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.save_btn = ttk.Button(
            btn_row, text="Save result…", command=self.save_result, state=tk.DISABLED
        )
        self.load_btn = ttk.Button(btn_row, text="Load saved…", command=self.load_saved)
        self.copy_btn = ttk.Button(
            btn_row, text="Copy command(s)", command=self.copy_command, state=tk.DISABLED
        )
        self.copy_btn.pack(side=tk.RIGHT)
        self.load_btn.pack(side=tk.RIGHT, padx=(0, 8))
        self.save_btn.pack(side=tk.RIGHT, padx=(0, 8))

        ttk.Label(root, text="Status").pack(anchor=tk.W)
        self.status_var = tk.StringVar(value="Paste one or more post URLs and click Find.")
        self.status_text = tk.Text(
            root, height=2, wrap=tk.WORD, font=("Segoe UI", 9), relief=tk.FLAT, borderwidth=0
        )
        self.status_text.pack(anchor=tk.W, fill=tk.X, pady=(2, 0))
        self.status_text.insert("1.0", self.status_var.get())
        self.status_text.configure(state=tk.DISABLED)
        self.status_var.trace_add("write", self._sync_status_text)

        ttk.Label(root, text="Result").pack(anchor=tk.W, pady=(10, 0))
        self.result_host = ttk.Frame(root)
        self.result_host.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self.result = tk.Text(self.result_host, height=18, wrap=tk.WORD, font=("Consolas", 10))
        self.result.pack(fill=tk.BOTH, expand=True)
        self.result.configure(state=tk.DISABLED)

        self.bulk_wrap = ttk.Frame(self.result_host)
        self.bulk_canvas = tk.Canvas(self.bulk_wrap, highlightthickness=0)
        self.bulk_scroll = self._make_vscroll(
            self.bulk_wrap, self.bulk_canvas.yview
        )
        self.bulk_inner = ttk.Frame(self.bulk_canvas)
        self.bulk_inner.bind(
            "<Configure>",
            lambda _e: self.bulk_canvas.configure(scrollregion=self.bulk_canvas.bbox("all")),
        )
        self._bulk_window = self.bulk_canvas.create_window((0, 0), window=self.bulk_inner, anchor="nw")
        self.bulk_canvas.configure(yscrollcommand=self.bulk_scroll.set)
        self.bulk_canvas.bind("<Configure>", self._on_bulk_canvas_configure)
        self.bulk_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.bulk_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.bulk_canvas.bind("<MouseWheel>", self._on_bulk_mousewheel)
        self.bulk_inner.bind("<MouseWheel>", self._on_bulk_mousewheel)

    def _on_url_wheel(self, event: tk.Event) -> str:  # type: ignore[type-arg]
        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            self.url_text.yview_scroll(-1 if delta > 0 else 1, "units")
        return "break"

    def _make_vscroll(self, parent: tk.Misc, command) -> tk.Misc:
        """Themed vertical scrollbar (tk.Scrollbar in Bridge — ttk stays white on Win)."""
        if self._embedded:
            return tk.Scrollbar(
                parent,
                orient=tk.VERTICAL,
                command=command,
                bg="#2b2d31",
                troughcolor="#1e1f22",
                activebackground="#404249",
                highlightthickness=0,
                bd=0,
                relief=tk.FLAT,
                width=12,
            )
        return ttk.Scrollbar(parent, orient=tk.VERTICAL, command=command)

    def _bridge_palette(self) -> dict[str, str] | None:
        try:
            host = self.winfo_toplevel()
            pal = getattr(host, "_bridge_palette", None)
            if isinstance(pal, dict) and pal.get("bg"):
                return pal
        except Exception:
            pass
        return None

    def _sync_bridge_theme(self) -> None:
        pal = self._bridge_palette()
        if pal:
            self.apply_ui_theme(pal)

    def apply_ui_theme(self, pal: dict[str, str]) -> None:
        """Match Bridge dark/light chrome for Text fields + scrollbars."""
        if not pal:
            return
        bg = pal.get("log_bg") or pal.get("entry") or pal.get("bg") or "#111214"
        fg = pal.get("fg") or "#f2f3f5"
        select = pal.get("select") or "#404249"
        muted = pal.get("muted") or fg
        canvas = pal.get("canvas") or bg
        sb_bg = pal.get("bg2") or "#2b2d31"
        trough = pal.get("bg") or "#1e1f22"
        active = pal.get("select") or "#404249"
        for widget in (self.url_text, self.status_text, self.result):
            try:
                widget.configure(
                    bg=bg,
                    fg=fg,
                    insertbackground=fg,
                    selectbackground=select,
                    selectforeground=fg,
                    highlightthickness=0,
                    relief=tk.FLAT,
                    borderwidth=0,
                )
            except Exception:
                pass
        try:
            self.bulk_canvas.configure(bg=canvas, highlightthickness=0)
        except Exception:
            pass
        for sb in (getattr(self, "url_scroll", None), getattr(self, "bulk_scroll", None)):
            if sb is None:
                continue
            try:
                if isinstance(sb, tk.Scrollbar):
                    sb.configure(
                        bg=sb_bg,
                        troughcolor=trough,
                        activebackground=active,
                        highlightthickness=0,
                        bd=0,
                        relief=tk.FLAT,
                    )
            except Exception:
                pass
        # Hint label uses ttk — ok under app theme.
        try:
            self.hint_label.configure(foreground=muted)
        except Exception:
            pass

    def _on_bulk_canvas_configure(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        self.bulk_canvas.itemconfigure(self._bulk_window, width=event.width)

    def _on_bulk_mousewheel(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        if self.mode_var.get() != "bulk":
            return
        if not self.bulk_wrap.winfo_ismapped():
            return
        self.bulk_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _is_bulk(self) -> bool:
        return self.mode_var.get() == "bulk"

    def _apply_mode(self) -> None:
        if self._worker and self._worker.is_alive():
            # Radiobutton already flipped the var — revert until search finishes.
            self.mode_var.set("find" if self._is_bulk() else "bulk")
            messagebox.showinfo(
                "Conjure Finder",
                "Wait for the current search to finish (or cancel it) before switching mode.",
            )
            return
        if self._is_bulk():
            self.hint_var.set(
                "Bulk wishlist — paste many post URLs (same character/artist). "
                "Ranks cheap paths that hit any of them; shared tags help."
            )
            self.own_row.pack(anchor=tk.W, fill=tk.X, padx=10, pady=(0, 0), before=self.btn_row)
            self.find_btn.configure(text="Rank cheapest paths")
            self.save_btn.configure(state=tk.NORMAL if self._last_bulk else tk.DISABLED)
            self.result.pack_forget()
            self.bulk_wrap.pack(fill=tk.BOTH, expand=True)
            if not self._last_bulk:
                self.status_var.set("Paste wishlist URLs, set ownership if needed, then Rank.")
        else:
            self.hint_var.set(
                "Post URLs — one per line = separate searches; "
                "same line (space or |) = any-of group"
            )
            self.own_row.pack_forget()
            self.find_btn.configure(text="Find cheapest conjure")
            self.save_btn.configure(state=tk.DISABLED)
            self.bulk_wrap.pack_forget()
            self.result.pack(fill=tk.BOTH, expand=True)
            if not self._last_commands:
                self.status_var.set("Paste one or more post URLs and click Find.")

    def _sync_status_text(self, *_args: object) -> None:
        text = self.status_var.get()
        self.status_text.configure(state=tk.NORMAL)
        self.status_text.delete("1.0", tk.END)
        self.status_text.insert("1.0", text)
        self.status_text.configure(state=tk.DISABLED)

    def _install_clipboard_bindings(self) -> None:
        from conjure_finder.clipboard_bindings import install_clipboard_bindings

        install_clipboard_bindings(self.url_text, self.result, self.status_text)

    def _set_result(self, text: str) -> None:
        self.result.configure(state=tk.NORMAL)
        self.result.delete("1.0", tk.END)
        self.result.insert(tk.END, text)
        self.result.configure(state=tk.DISABLED)

    def _append_result(self, text: str) -> None:
        self.result.configure(state=tk.NORMAL)
        self.result.insert(tk.END, text)
        self.result.see(tk.END)
        self.result.configure(state=tk.DISABLED)

    def _clear_bulk_panel(self) -> None:
        for child in self.bulk_inner.winfo_children():
            child.destroy()
        self._photo_refs.clear()

    def _render_bulk_result(self, result: BulkResult) -> None:
        self._clear_bulk_panel()
        self._last_bulk = result
        self._last_commands = [bp.option.command for bp in result.paths if bp.option.command]
        head = ttk.Label(
            self.bulk_inner,
            text=(
                f"{result.source}  wishlist={result.wishlist_size}  "
                f"paths={len(result.paths)}  checked={result.checked}  "
                f"in {_format_elapsed(result.elapsed_sec)}"
            ),
            font=("Segoe UI", 9, "bold"),
        )
        head.pack(anchor=tk.W, pady=(0, 6))
        if not result.paths:
            ttk.Label(self.bulk_inner, text="(no usable paths)").pack(anchor=tk.W)
            return
        for i, bp in enumerate(result.paths, 1):
            self._add_bulk_path_block(i, bp)

    def _add_bulk_path_block(self, index: int, bp: Any) -> None:
        opt = bp.option
        frame = ttk.LabelFrame(self.bulk_inner, text=f"#{index}  ~{opt.expected_currency:.0f} cur")
        frame.pack(fill=tk.X, pady=(0, 8), padx=2)
        g = "YES" if opt.guaranteed else "no"
        for cmd_line in (opt.command or "").splitlines():
            ttk.Label(frame, text=cmd_line, font=("Consolas", 10)).pack(anchor=tk.W, padx=6)
        ttk.Label(
            frame,
            text=(
                f"path {opt.path}  cost {opt.cost}  pool {opt.pool_size}  "
                f"guarantee {g}  covers {len(bp.covered)}  "
                f"~{opt.expected_sessions:.1f} steps"
            ),
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, padx=6, pady=(2, 4))
        thumbs = ttk.Frame(frame)
        thumbs.pack(anchor=tk.W, padx=6, pady=(0, 6))
        for cov in bp.covered:
            cell = ttk.Frame(thumbs)
            cell.pack(side=tk.LEFT, padx=(0, 6))
            photo = None
            if cov.preview_url:
                from conjure_finder.previews import make_photoimage

                photo = make_photoimage(cov.preview_url)
            if photo is not None:
                self._photo_refs.append(photo)
                btn = tk.Label(cell, image=photo, cursor="hand2", borderwidth=1, relief=tk.SOLID)
                btn.pack()
                btn.bind("<Button-1>", lambda _e, u=cov.page_url: webbrowser.open(u))
            else:
                link = ttk.Label(
                    cell,
                    text=f"#{cov.post_id}",
                    foreground="#0563C1",
                    cursor="hand2",
                )
                link.pack()
                link.bind("<Button-1>", lambda _e, u=cov.page_url: webbrowser.open(u))
            ttk.Label(cell, text=f"#{cov.post_id}", font=("Segoe UI", 8)).pack()

    def start_search(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        if self._is_bulk():
            urls = flatten_wishlist_urls(self.url_text.get("1.0", "end"))
            if not urls:
                messagebox.showinfo(
                    "Conjure Finder",
                    "Paste wishlist post URLs first (same character or artist).",
                )
                return
            self._cancel.clear()
            self._last_commands = []
            self._last_bulk = None
            self.copy_btn.configure(state=tk.DISABLED)
            self.save_btn.configure(state=tk.DISABLED)
            self.find_btn.configure(state=tk.DISABLED)
            self.cancel_btn.configure(state=tk.NORMAL)
            self.status_var.set(f"Bulk ranking {len(urls)} URL(s)…")
            self._clear_bulk_panel()
            own_a = bool(self.own_author_var.get())
            own_c = bool(self.own_character_var.get())
            self._worker = threading.Thread(
                target=self._run_bulk_worker,
                args=(urls, own_a, own_c),
                daemon=True,
            )
            self._worker.start()
            return

        jobs = split_url_jobs(self.url_text.get("1.0", "end"))
        if not jobs:
            messagebox.showinfo(
                "Conjure Finder",
                "Paste one or more Danbooru / Rule34 post URLs first.\n\n"
                "Tip: put related variants on the same line for an any-of search.",
            )
            return
        self._cancel.clear()
        self._last_commands = []
        self.copy_btn.configure(state=tk.DISABLED)
        self.find_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        n_urls = sum(len(j) for j in jobs)
        any_of = sum(1 for j in jobs if len(j) > 1)
        if any_of:
            self.status_var.set(
                f"Queued {len(jobs)} job(s) ({n_urls} URLs, {any_of} any-of)…"
            )
        else:
            self.status_var.set(f"Queued {len(jobs)} URL(s)…")
        self._set_result("")
        self._worker = threading.Thread(target=self._run_worker, args=(jobs,), daemon=True)
        self._worker.start()

    def open_settings(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo(
                "Conjure Finder",
                "Wait for the current search to finish (or cancel it) before changing keys.",
            )
            return
        from conjure_finder.settings_ui import SettingsDialog

        SettingsDialog(self)

    def cancel_search(self) -> None:
        self._cancel.set()
        self.status_var.set("Cancelling…")

    def copy_command(self) -> None:
        if not self._last_commands:
            return
        text = "\n".join(self._last_commands)
        self.clipboard_clear()
        self.clipboard_append(text)
        n = len(self._last_commands)
        self.status_var.set(f"Copied {n} command(s).")

    def save_result(self) -> None:
        if not self._last_bulk:
            messagebox.showinfo("Conjure Finder", "No bulk result to save yet.")
            return
        name = simpledialog.askstring(
            "Save bulk result",
            "Name for this save (optional):",
            parent=self,
        )
        if name is None:
            return
        from conjure_finder.saves import save_bulk_result

        path = save_bulk_result(self._last_bulk, name or "")
        self.status_var.set(f"Saved: {path.name}")
        messagebox.showinfo("Conjure Finder", f"Saved to:\n{path}")

    def load_saved(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Conjure Finder", "Wait for the current search to finish.")
            return
        from conjure_finder.saves import list_saves, load_bulk_result

        saves = list_saves()
        if not saves:
            messagebox.showinfo("Conjure Finder", "No saved bulk results yet.")
            return
        picker = tk.Toplevel(self)
        picker.title("Load saved bulk result")
        picker.transient(self)
        picker.grab_set()
        picker.geometry("520x320")
        ttk.Label(picker, text="Select a saved result:").pack(anchor=tk.W, padx=10, pady=8)
        listbox = tk.Listbox(picker, font=("Consolas", 10))
        listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        for s in saves:
            listbox.insert(
                tk.END,
                f"{s.name}  |  {s.wishlist_size} posts  |  {s.path_count} paths  |  {s.saved_at}",
            )
        if saves:
            listbox.selection_set(0)

        def _do_load() -> None:
            sel = listbox.curselection()
            if not sel:
                return
            meta = saves[sel[0]]
            try:
                result = load_bulk_result(meta.path)
            except Exception as exc:
                messagebox.showerror("Conjure Finder", f"Failed to load:\n{exc}")
                return
            picker.destroy()
            self.mode_var.set("bulk")
            self._apply_mode()
            self.own_author_var.set(result.own_author)
            self.own_character_var.set(result.own_character)
            if result.urls:
                self.url_text.delete("1.0", tk.END)
                self.url_text.insert("1.0", "\n".join(result.urls))
            self._render_bulk_result(result)
            self.copy_btn.configure(state=tk.NORMAL if self._last_commands else tk.DISABLED)
            self.save_btn.configure(state=tk.NORMAL)
            self.status_var.set(f"Loaded: {meta.name}")

        btn_row = ttk.Frame(picker)
        btn_row.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(btn_row, text="Load", command=_do_load).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="Cancel", command=picker.destroy).pack(side=tk.RIGHT, padx=(0, 8))

    def _run_bulk_worker(
        self, urls: list[str], own_author: bool, own_character: bool
    ) -> None:
        from conjure_finder.bulk import find_bulk_paths

        def on_progress(msg: str) -> None:
            self.after(0, lambda m=msg: self.status_var.set(m))

        try:
            result = asyncio.run(
                find_bulk_paths(
                    urls,
                    own_author=own_author,
                    own_character=own_character,
                    progress=on_progress,
                    cancel_check=self._cancel.is_set,
                )
            )
        except Exception as exc:
            self.after(0, lambda: self._on_error(str(exc)))
            return
        self.after(0, lambda: self._on_bulk_done(result))

    def _on_bulk_done(self, result: BulkResult) -> None:
        self.find_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        self._render_bulk_result(result)
        if self._last_commands:
            self.copy_btn.configure(state=tk.NORMAL)
            self.save_btn.configure(state=tk.NORMAL)
            self.status_var.set(
                f"Done. {len(result.paths)} path(s) ranked — ready to copy/save."
            )
        elif self._cancel.is_set():
            self.status_var.set("Cancelled.")
        else:
            self.save_btn.configure(state=tk.NORMAL if result.paths else tk.DISABLED)
            self.status_var.set("Done.")

    def _run_worker(self, jobs: list[list[str]]) -> None:
        from conjure_finder.batch import run_batch

        site_status: dict[str, str] = {}

        def on_progress(site: str, msg: str) -> None:
            site_status[site] = msg

            def _update() -> None:
                parts = [f"{s}: {m}" for s, m in site_status.items() if m]
                self.status_var.set(" | ".join(parts) if parts else msg)

            self.after(0, _update)

        def on_item_done(index: int, total: int, label: str, payload: Any) -> None:
            if isinstance(payload, Exception):
                block = _format_error(index, total, label, str(payload))
            else:
                block = _format_result(index, total, payload)
                if payload.best:
                    self._last_commands.append(payload.best.command)
            self.after(0, lambda b=block: self._append_result(b + "\n"))

        try:
            asyncio.run(
                run_batch(
                    jobs,
                    progress=on_progress,
                    cancel_check=self._cancel.is_set,
                    on_item_done=on_item_done,
                )
            )
        except Exception as exc:
            self.after(0, lambda: self._on_error(str(exc)))
            return
        self.after(0, self._on_batch_done)

    def _on_error(self, message: str) -> None:
        self.find_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        self.status_var.set("Error.")
        if self._is_bulk():
            ttk.Label(self.bulk_inner, text=f"Error:\n{message}").pack(anchor=tk.W)
        else:
            self._append_result(f"Error:\n{message}\n")
        messagebox.showerror("Conjure Finder", message)

    def _on_batch_done(self) -> None:
        self.find_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        if self._last_commands:
            self.copy_btn.configure(state=tk.NORMAL)
            self.status_var.set(f"Done. {len(self._last_commands)} command(s) ready to copy.")
        elif self._cancel.is_set():
            self.status_var.set("Cancelled.")
        else:
            self.status_var.set("Done.")

    def check_updates(self, *, silent: bool = False) -> None:
        if self._update_busy:
            return
        self._update_busy = True
        threading.Thread(
            target=self._check_updates_thread, args=(silent,), daemon=True
        ).start()

    def _check_updates_thread(self, silent: bool) -> None:
        from conjure_finder.updater import check_for_update, load_update_config

        try:
            info = check_for_update(load_update_config())
        except Exception as exc:
            self._update_busy = False
            if not silent:
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Conjure Finder", f"Update check failed:\n{exc}"
                    ),
                )
            return
        if info is None:
            self._update_busy = False
            if not silent:
                self.after(
                    0,
                    lambda: (
                        self.status_var.set(f"Up to date (v{__version__})"),
                        messagebox.showinfo(
                            "Conjure Finder",
                            f"You're on the latest version (v{__version__}).",
                        ),
                    ),
                )
            return

        def _ask() -> None:
            ok = messagebox.askyesno(
                "Conjure Finder",
                f"Version {info.version} is available (you have {__version__}).\n\n"
                "Download and install now?\n\n"
                "The app will close. After that, start Conjure Finder.exe yourself "
                "(the install folder will open).",
            )
            if not ok:
                self._update_busy = False
                return
            self.status_var.set(f"Downloading v{info.version}…")
            threading.Thread(
                target=self._download_update_thread, args=(info,), daemon=True
            ).start()

        self.after(0, _ask)

    def _download_update_thread(self, info: Any) -> None:
        from conjure_finder.updater import load_update_config, run_update

        try:
            run_update(load_update_config(), info)
        except Exception as exc:
            self._update_busy = False
            self.after(
                0,
                lambda: (
                    self.status_var.set(f"Update failed: {exc}"),
                    messagebox.showerror("Conjure Finder", f"Update failed:\n{exc}"),
                ),
            )
            return
        if getattr(sys, "frozen", False):

            def _done() -> None:
                self.status_var.set(f"Installed v{info.version} — start again.")
                messagebox.showinfo(
                    "Conjure Finder",
                    f"Version {info.version} is ready.\n\n"
                    "This window will close. Then double-click Conjure Finder.exe "
                    "in the folder that opens (or use your usual shortcut).",
                )
                self.after(400, self.destroy)

            self.after(0, _done)
        else:
            self._update_busy = False
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "Conjure Finder",
                    f"Downloaded v{info.version} (dev mode — not applying).",
                ),
            )


def main() -> None:
    app = ConjureFinderApp()
    app.mainloop()
