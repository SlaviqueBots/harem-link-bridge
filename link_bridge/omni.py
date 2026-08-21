"""In-client omnicraft / refine — single centered host, tabs per card."""

from __future__ import annotations

import html
import io
import logging
import math
import re
import struct
import tempfile
import wave
from collections.abc import Callable
from typing import Any

import tkinter as tk
from tkinter import ttk

from link_bridge.thumb_grid import schedule_thumb_fetch

logger = logging.getLogger(__name__)

OkCb = Callable[[dict[str, Any]], None]
ErrCb = Callable[[BaseException], None]
StateFn = Callable[..., None]  # (char_id, on_ok, on_err, mode=)
TapFn = Callable[..., None]  # (char_id, op, arg, on_ok, on_err, mode=)

VIEW = 480
_KEYISH = ("omni.", "inline.", "reshape.", "refine.")
_FALLBACK = {
    "omni.btn_hide": "Hide",
    "omni.btn_show": "Show",
    "inline.btn_done": "Done",
    "inline.btn_undone": "Undone",
    "reshape.btn_flavour": "Flavour",
    "omni.btn_undo": "Undo",
    "omni.btn_checkpoint": "Checkpoint",
    "omni.btn_reshape": "Reshape",
    "omni.btn_portal": "Portal",
    "omni.btn_portal_a": "Portal A",
    "omni.btn_author": "Author",
    "omni.btn_author_m": "Author M",
    "omni.btn_title": "Title",
    "omni.btn_slopify": "Slopify",
    "refine.btn_all": "ALL",
    "omni.btn_refine": "Refine",
}

_BG = "#1c1c1c"
_FG = "#f2f2f2"
_CRAFT_BG = "#3a2f4a"
_CRAFT_BG2 = "#2f3d4a"
_STATUS_BG = "#3a3a3a"
_LINK_BG = "#1e4a5c"
_LIT_BG = "#1b8f4a"
_LIT_FG = "#ffffff"
_DIS_BG = "#2a2a2a"
_DIS_FG = "#777777"
_MODE_BG = "#5a3d2a"

# Rough wall-clock ETAs (seconds) for heavy image fetches — progress creeps to ~92%.
_FETCH_ETA_SEC: dict[str, float] = {
    "rs": 14.0,
    "rm": 16.0,
    "sl": 14.0,
    "sm": 16.0,
    "po": 12.0,
    "pa": 14.0,
    "au": 18.0,
    "am": 20.0,
    "ti": 16.0,
    "uo": 5.0,
    "ld": 4.0,
    "rj": 22.0,
    "mi": 3.0,
    "cp": 2.0,
}


def _plain_caption(raw: str) -> str:
    """Telegram HTML → readable plain text for the Tk caption pane."""
    s = str(raw or "")
    s = re.sub(r"(?is)<br\s*/?>", "\n", s)
    s = re.sub(r"(?is)</p\s*>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _soft_beep() -> None:
    try:
        import winsound
    except ImportError:
        return
    path = getattr(_soft_beep, "_path", "")
    if not path:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(tmp.name, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            n = int(22050 * 0.11)
            amp = 1400
            freq = 196.0
            frames = bytearray()
            for i in range(n):
                fade = 1.0
                if i < 400:
                    fade = i / 400.0
                elif i > n - 800:
                    fade = max(0.0, (n - i) / 800.0)
                sample = int(amp * fade * math.sin(2 * math.pi * freq * i / 22050))
                frames.extend(struct.pack("<h", sample))
            wf.writeframes(bytes(frames))
        tmp.close()
        _soft_beep._path = tmp.name  # type: ignore[attr-defined]
        path = tmp.name
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        logger.debug("omni beep failed", exc_info=True)


def _pretty_label(text: str, op: str, *, hidden: bool, done: bool) -> str:
    raw = (text or "").strip() or " "
    if raw in _FALLBACK:
        raw = _FALLBACK[raw]
    elif raw.startswith(_KEYISH):
        raw = _FALLBACK.get(raw, raw.rsplit(".", 1)[-1].replace("_", " ").title())
    if op in ("hi", "sh"):
        return "Show" if hidden else "Hide"
    if op in ("dn", "ud") and raw.startswith(_KEYISH):
        return "Undone" if done else "Done"
    return raw[:22]


def _coalesce_reshape_rows(
    crafts: list[list[dict[str, Any]]],
) -> list[list[dict[str, Any]]]:
    """Put lone Reshape + Reshape −solo on one row (R34)."""
    out: list[list[dict[str, Any]]] = []
    i = 0
    while i < len(crafts):
        row = crafts[i]
        nxt = crafts[i + 1] if i + 1 < len(crafts) else None
        if (
            len(row) == 1
            and nxt is not None
            and len(nxt) == 1
            and str(row[0].get("op") or "") == "rs"
            and str(nxt[0].get("op") or "") == "rm"
        ):
            out.append([row[0], nxt[0]])
            i += 2
            continue
        out.append(row)
        i += 1
    return out


def _center_window(win: tk.Toplevel, w: int, h: int) -> None:
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    win.geometry(f"{w}x{h}+{x}+{y}")


class OmniPanel(ttk.Frame):
    """One card's craft UI (embedded in the host notebook)."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        char_id: int,
        mode: str,
        fetch_state: StateFn,
        tap: TapFn,
        beep_get: Callable[[], bool],
        prefer_original: Callable[[], bool],
        get_text_geo: Callable[[], str],
        set_text_geo: Callable[[str], None],
        on_log: Callable[[str], None],
        on_done_changed: Callable[[int, bool], None] | None = None,
        on_title: Callable[[str], None] | None = None,
        on_open_refine: Callable[[int], None] | None = None,
        wip_get: Callable[[], bool] | None = None,
        on_wip_next: Callable[[int], None] | None = None,
        dm_preview: Callable[[int, OkCb, ErrCb], None] | None = None,
        on_media_changed: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._char_id = int(char_id)
        self._mode = "refine" if mode == "refine" else "omni"
        self._fetch_state = fetch_state
        self._tap = tap
        self._beep_get = beep_get
        self._prefer_original = prefer_original
        self._get_text_geo = get_text_geo
        self._set_text_geo = set_text_geo
        self._on_log = on_log
        self._on_done_changed = on_done_changed
        self._on_title = on_title
        self._on_open_refine = on_open_refine
        self._wip_get = wip_get or (lambda: False)
        self._on_wip_next = on_wip_next
        self._dm_preview = dm_preview
        self._on_media_changed = on_media_changed
        self._busy = False
        self._busy_gen = 0
        self._photo = None
        self._state: dict[str, Any] = {}
        self._btns: list[tk.Button] = []

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        left = ttk.Frame(body, width=VIEW + 8, height=VIEW + 8)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)
        self._img_lbl = tk.Label(
            left, text="…", width=VIEW, height=VIEW, bg="#111111", fg="#cccccc", cursor="hand2"
        )
        self._img_lbl.pack(fill=tk.BOTH, expand=True)
        self._img_lbl.bind("<Button-1>", self._open_original)

        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        head = ttk.Frame(right)
        head.pack(fill=tk.X)
        self._name_var = tk.StringVar(value=f"#{char_id}")
        ttk.Label(head, textvariable=self._name_var, font=("Segoe UI", 11, "bold")).pack(
            side=tk.LEFT, anchor=tk.W
        )
        ttk.Button(head, text="Refresh", command=self.reload, width=10).pack(side=tk.RIGHT)

        self._status_var = tk.StringVar(value="Loading…")
        ttk.Label(right, textvariable=self._status_var, wraplength=420).pack(
            anchor=tk.W, fill=tk.X, pady=(2, 2)
        )
        self._prog = ttk.Progressbar(right, mode="determinate", maximum=100)
        self._prog_after: str | None = None
        self._prog_t0 = 0.0
        self._prog_eta = 12.0
        # Hidden until a fetch starts.
        self._prog.pack_forget()

        craft_title = "Exclude tag" if self._mode == "refine" else "Alter image"
        self._craft_fr = ttk.LabelFrame(right, text=craft_title)
        self._craft_fr.pack(fill=tk.X, pady=(0, 4))
        self._status_fr = ttk.LabelFrame(right, text="Card")
        self._status_fr.pack(fill=tk.X, pady=(0, 4))
        self._link_fr = ttk.LabelFrame(right, text="Open posts / tags in browser")
        self._link_fr.pack(fill=tk.X, pady=(0, 4))

        self._cap = tk.Text(
            right,
            height=7,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#141414",
            fg=_FG,
            insertbackground=_FG,
            relief=tk.FLAT,
            padx=6,
            pady=4,
        )
        self._cap.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self.after(30, self.reload)

    def _set_caption(self, text: str) -> None:
        self._cap.configure(state=tk.NORMAL)
        self._cap.delete("1.0", tk.END)
        self._cap.insert("1.0", _plain_caption(text))
        self._cap.configure(state=tk.DISABLED)

    def _open_original(self, _event=None) -> None:
        from link_bridge.open_image import open_full_image

        st = self._state
        if self._prefer_original():
            url = (st.get("file_url") or st.get("image_url") or st.get("preview_url") or "").strip()
        else:
            url = (st.get("image_url") or st.get("file_url") or st.get("preview_url") or "").strip()
        if not url:
            self._status_var.set("No original URL")
            return
        open_full_image(url, on_err=lambda e: self._status_var.set(f"Open failed: {e}"))

    def _paint_preview(self, url: str) -> None:
        if not url:
            self._img_lbl.configure(image="", text="no preview")
            return

        def on_data(data: bytes) -> None:
            def ui() -> None:
                try:
                    from PIL import Image, ImageTk, ImageOps

                    im = Image.open(io.BytesIO(data))
                    try:
                        im.seek(0)
                    except Exception:
                        pass
                    im = im.convert("RGB")
                    im = ImageOps.contain(im, (VIEW, VIEW), method=Image.Resampling.BILINEAR)
                    photo = ImageTk.PhotoImage(im)
                    self._photo = photo
                    self._img_lbl.configure(image=photo, text="")
                except Exception as exc:
                    self._img_lbl.configure(image="", text="×")
                    logger.debug("omni preview decode failed: %s", exc)

            try:
                self.after(0, ui)
            except Exception:
                pass

        schedule_thumb_fetch(url, on_data=on_data, on_err=lambda _e: None)

    def _apply_state(self, body: dict[str, Any], *, acquired: bool = False) -> None:
        prev_done = bool(self._state.get("done"))
        self._state = body
        name = str(body.get("name") or "")
        title = f"#{self._char_id} · {name}".strip(" ·")
        if bool(body.get("is_original")):
            title = f"{title} · original"
        if self._mode == "refine":
            ex = str(body.get("exclude") or "")
            title = f"Refine {title}" + (f" −{ex}" if ex else "")
        self._name_var.set(title)
        if self._on_title is not None:
            short = f"#{self._char_id}"
            if self._mode == "refine":
                short = f"R {short}"
            self._on_title(short)
        self._set_caption(str(body.get("caption") or ""))
        prev = (body.get("preview_url") or "").strip()
        self._paint_preview(prev)
        if acquired and prev and self._beep_get():
            _soft_beep()
        if acquired and self._on_media_changed is not None:
            try:
                self._on_media_changed(
                    self._char_id,
                    {
                        "preview_url": prev,
                        "image_url": body.get("image_url") or "",
                        "file_url": body.get("file_url") or "",
                        "post_url": body.get("post_url") or "",
                        "name": body.get("name") or "",
                    },
                )
            except Exception:
                logger.debug("omni media notify failed", exc_info=True)
        self._stop_progress(done=True)
        self._render_buttons(body.get("buttons") or [])
        if bool(body.get("busy")):
            self._status_var.set("Working…")
        else:
            self._status_var.set(str(body.get("detail") or "Ready"))
        now_done = bool(body.get("done"))
        if self._on_done_changed is not None and now_done != prev_done:
            self._on_done_changed(self._char_id, now_done)
        # WIP: after marking Done on a non-mirror omni card, jump to a random undone.
        if (
            self._mode == "omni"
            and now_done
            and not prev_done
            and not bool(body.get("mirrored"))
            and self._wip_get()
            and self._on_wip_next is not None
        ):
            self.after(80, lambda: self._on_wip_next(self._char_id))

    def _start_progress(self, op: str) -> None:
        import time

        self._stop_progress(done=False)
        self._prog_eta = float(_FETCH_ETA_SEC.get(op, 12.0))
        self._prog_t0 = time.monotonic()
        try:
            self._prog.pack(fill=tk.X, pady=(0, 6))
        except Exception:
            pass
        self._prog["value"] = 2
        self._tick_progress()

    def _tick_progress(self) -> None:
        import time

        self._prog_after = None
        if not self._busy:
            return
        elapsed = max(0.0, time.monotonic() - self._prog_t0)
        eta = max(2.0, self._prog_eta)
        # Ease toward 92% over ETA; linger until the response arrives.
        frac = 1.0 - math.exp(-elapsed / (eta * 0.55))
        value = min(92.0, 2.0 + 90.0 * frac)
        try:
            self._prog["value"] = value
        except Exception:
            return
        self._prog_after = self.after(80, self._tick_progress)

    def _stop_progress(self, *, done: bool) -> None:
        if self._prog_after is not None:
            try:
                self.after_cancel(self._prog_after)
            except Exception:
                pass
            self._prog_after = None
        try:
            if done:
                self._prog["value"] = 100
            self._prog.pack_forget()
            self._prog["value"] = 0
        except Exception:
            pass

    def _clear_btns(self) -> None:
        for b in self._btns:
            try:
                b.destroy()
            except Exception:
                pass
        self._btns.clear()
        for fr in (self._craft_fr, self._status_fr, self._link_fr):
            for child in list(fr.winfo_children()):
                child.destroy()

    def _style_for(self, spec: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
        lit = bool(spec.get("lit"))
        kind = str(spec.get("kind") or "")
        op = str(spec.get("op") or "")
        url = str(spec.get("url") or "")
        if not kind:
            if url and not op:
                kind = "link"
            elif op in ("dn", "ud", "hi", "sh", "fl", "flset", "rfl", "mi"):
                kind = "status"
            else:
                kind = "craft"
        if not enabled:
            return {"bg": _DIS_BG, "fg": _DIS_FG, "activebackground": _DIS_BG, "relief": tk.FLAT}
        if lit:
            return {
                "bg": _LIT_BG,
                "fg": _LIT_FG,
                "activebackground": "#24a656",
                "relief": tk.SUNKEN,
                "font": ("Segoe UI", 9, "bold"),
            }
        if kind == "link":
            bg = _LINK_BG
        elif kind == "mode":
            bg = _MODE_BG
        elif kind == "status":
            bg = _STATUS_BG
        else:
            bg = _CRAFT_BG if op in ("au", "am", "rs", "rm", "sl", "sm", "ex") else _CRAFT_BG2
        return {
            "bg": bg,
            "fg": _FG,
            "activebackground": bg,
            "relief": tk.RAISED,
            "font": ("Segoe UI", 9),
        }

    def _place_row(self, parent: tk.Misc, specs: list[dict[str, Any]], row: int) -> None:
        """Equal-width block buttons in a grid row (Telegram-like geometry)."""
        hidden = bool(self._state.get("hidden"))
        done = bool(self._state.get("done"))
        n = max(1, len(specs))
        for c in range(n):
            parent.columnconfigure(c, weight=1, uniform=f"omni{id(parent)}")
        for c, spec in enumerate(specs):
            text = _pretty_label(
                str(spec.get("text") or " "),
                str(spec.get("op") or ""),
                hidden=hidden,
                done=done,
            )
            enabled = bool(spec.get("enabled")) and not self._busy
            op = str(spec.get("op") or "")
            arg = spec.get("arg")
            url = str(spec.get("url") or "")
            urls = [str(u).strip() for u in (spec.get("urls") or []) if str(u).strip()]
            style = self._style_for(spec, enabled=enabled)
            if urls:
                cmd = lambda us=urls: self._open_urls(us)
            elif url and not op:
                cmd = lambda u=url: self._open_url(u)
            elif op:
                cmd = lambda o=op, a=arg, t=text: self._click(o, a, t)
            else:
                cmd = lambda: None
                enabled = False
            # Fixed character width → squarish blocks; grid stretches evenly.
            btn = tk.Button(
                parent,
                text=text,
                command=cmd,
                bd=1,
                padx=4,
                pady=4,
                width=11,
                cursor="hand2" if enabled else "arrow",
                state=tk.NORMAL if enabled else tk.DISABLED,
                highlightthickness=2 if spec.get("lit") else 0,
                highlightbackground=_LIT_BG,
                highlightcolor=_LIT_BG,
                **style,
            )
            btn.grid(row=row, column=c, sticky="nsew", padx=2, pady=2)
            self._btns.append(btn)

    def _render_buttons(self, rows: list[Any]) -> None:
        self._clear_btns()
        crafts: list[list[dict[str, Any]]] = []
        status: list[list[dict[str, Any]]] = []
        links: list[list[dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, list):
                continue
            craft_row: list[dict[str, Any]] = []
            status_row: list[dict[str, Any]] = []
            link_row: list[dict[str, Any]] = []
            for btn in row:
                if not isinstance(btn, dict):
                    continue
                kind = str(btn.get("kind") or "")
                op = str(btn.get("op") or "")
                url = str(btn.get("url") or "")
                urls = btn.get("urls") or []
                if not kind:
                    if (url or urls) and not op:
                        kind = "link"
                    elif op == "rf":
                        kind = "mode"
                    elif op in ("dn", "ud", "hi", "sh", "fl", "flset", "rfl", "mi"):
                        kind = "status"
                    else:
                        kind = "craft"
                if kind == "link":
                    link_row.append(btn)
                elif kind == "status":
                    status_row.append(btn)
                else:
                    craft_row.append(btn)
            if craft_row:
                crafts.append(craft_row)
            if status_row:
                status.append(status_row)
            if link_row:
                links.append(link_row)
        crafts = _coalesce_reshape_rows(crafts)
        for i, row in enumerate(crafts):
            self._place_row(self._craft_fr, row, i)
        # Client-only: DM current card image (how Telegram will handle it).
        if self._mode == "omni" and self._dm_preview is not None:
            chip = {
                "text": "DM preview",
                "op": "dmp",
                "arg": "",
                "url": "",
                "kind": "status",
                "lit": False,
                "enabled": True,
            }
            if status:
                status[-1].append(chip)
            else:
                status.append([chip])
        for i, row in enumerate(status):
            self._place_row(self._status_fr, row, i)
        for i, row in enumerate(links):
            self._place_row(self._link_fr, row, i)
        if not crafts:
            ttk.Label(self._craft_fr, text="—").grid(row=0, column=0, sticky="w", padx=4)
        if not status:
            ttk.Label(self._status_fr, text="—").grid(row=0, column=0, sticky="w", padx=4)
        if not links:
            ttk.Label(self._link_fr, text="—").grid(row=0, column=0, sticky="w", padx=4)

    def _click_dm_preview(self) -> None:
        if self._busy or self._dm_preview is None:
            return
        self._busy = True
        self._busy_gen += 1
        gen = self._busy_gen
        self._status_var.set("DM preview…")
        self._render_buttons(self._state.get("buttons") or [])

        def on_ok(body: dict) -> None:
            if gen != self._busy_gen:
                return
            self._busy = False
            if body.get("op") == "dm_craft_ok":
                self._status_var.set("DM preview sent")
                self._on_log(f"omni DM preview #{self._char_id}")
            else:
                self._status_var.set(str(body.get("error") or "DM failed"))
            self._render_buttons(self._state.get("buttons") or [])

        def on_err(exc: BaseException) -> None:
            if gen != self._busy_gen:
                return
            self._busy = False
            self._status_var.set(f"DM failed: {exc}")
            self._render_buttons(self._state.get("buttons") or [])

        self._dm_preview(self._char_id, on_ok, on_err)

    def _open_url(self, url: str) -> None:
        from link_bridge.browser_open import open_url

        target = (url or "").strip()
        if target:
            open_url(target)

    def _open_urls(self, urls: list[str]) -> None:
        for u in urls:
            self._open_url(u)

    def _click(self, op: str, arg: Any, label: str) -> None:
        if self._busy:
            return
        if op == "rf":
            if self._on_open_refine is not None:
                self._on_open_refine(self._char_id)
            else:
                self._status_var.set("Refine unavailable")
            return
        if op == "dmp":
            self._click_dm_preview()
            return
        if op == "fl":
            self._edit_flavour()
            return
        arg_s = None if arg is None or str(arg).strip() == "" else str(arg).strip()
        self._busy = True
        self._busy_gen += 1
        gen = self._busy_gen
        self._status_var.set(f"{label}…")
        self._start_progress(op)
        self._render_buttons(self._state.get("buttons") or [])
        prev_before = (self._state.get("preview_url") or "").strip()

        def _unstick() -> None:
            if gen != self._busy_gen or not self._busy:
                return
            self._busy = False
            self._stop_progress(done=False)
            self._status_var.set("Still working — tap Refresh if stuck.")
            self._render_buttons(self._state.get("buttons") or [])

        self.after(150000, _unstick)

        def on_ok(body: dict) -> None:
            if gen != self._busy_gen:
                return
            self._busy = False
            op_name = str(body.get("op") or "")
            if op_name == "omni_tap_ok":
                acquired = (body.get("preview_url") or "").strip() != prev_before and op in (
                    "rs", "rm", "po", "pa", "sl", "sm", "au", "am", "ti", "uo", "ld", "rj",
                )
                self._apply_state(body, acquired=acquired)
                self._on_log(f"{self._mode} {op} #{self._char_id}: {body.get('detail') or 'ok'}")
            else:
                self._stop_progress(done=False)
                err = body.get("error") or body.get("detail") or "failed"
                self._status_var.set(str(err))
                if body.get("buttons"):
                    self._apply_state(body, acquired=False)
                else:
                    self._render_buttons(self._state.get("buttons") or [])

        def on_err(exc: BaseException) -> None:
            if gen != self._busy_gen:
                return
            self._busy = False
            self._stop_progress(done=False)
            self._status_var.set(f"Failed: {exc}")
            self._render_buttons(self._state.get("buttons") or [])

        self._tap(self._char_id, op, arg_s, on_ok, on_err, self._mode)

    def _edit_flavour(self) -> None:
        from link_bridge.text_edit_dialog import ask_text

        text = ask_text(
            self,
            title=f"Flavour #{self._char_id}",
            initial="",
            prompt="Public flavour text (saved quietly).",
            geometry=self._get_text_geo(),
            on_geometry=self._set_text_geo,
        )
        if text is None:
            return
        if not str(text).strip():
            self._click("rfl", None, "Remove flavour")
        else:
            self._click("flset", str(text).strip(), "Set flavour")

    def reload(self) -> None:
        self._busy = False
        self._busy_gen += 1
        self._status_var.set("Loading…")

        def on_ok(body: dict) -> None:
            if body.get("op") == "omni_state_ok":
                self._apply_state(body, acquired=False)
            else:
                self._status_var.set(str(body.get("error") or "failed"))

        def on_err(exc: BaseException) -> None:
            self._status_var.set(f"Load failed: {exc}")

        self._fetch_state(self._char_id, on_ok, on_err, self._mode)


class OmniHost(tk.Toplevel):
    """One centered window; each card/mode is a notebook tab."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        fetch_state: StateFn,
        tap: TapFn,
        beep_get: Callable[[], bool],
        beep_set: Callable[[bool], None],
        prefer_original: Callable[[], bool],
        get_text_geo: Callable[[], str],
        set_text_geo: Callable[[str], None],
        on_log: Callable[[str], None] | None = None,
        on_done_changed: Callable[[int, bool], None] | None = None,
        fetch_undone: Callable[..., None] | None = None,
        dm_preview: Callable[[int, OkCb, ErrCb], None] | None = None,
        on_media_changed: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Omnicraft")
        self.minsize(900, 620)
        self.configure(bg=_BG)
        self._fetch_state = fetch_state
        self._tap = tap
        self._beep_get = beep_get
        self._beep_set = beep_set
        self._prefer_original = prefer_original
        self._get_text_geo = get_text_geo
        self._set_text_geo = set_text_geo
        self._on_log = on_log or (lambda _m: None)
        self._on_done_changed = on_done_changed
        self._fetch_undone = fetch_undone
        self._dm_preview = dm_preview
        self._on_media_changed = on_media_changed
        self._tabs: dict[tuple[int, str], tuple[ttk.Frame, OmniPanel]] = {}
        self._wip_busy = False

        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill=tk.BOTH, expand=True)
        head = ttk.Frame(top)
        head.pack(fill=tk.X)
        self._beep_var = tk.BooleanVar(value=bool(self._beep_get()))
        ttk.Checkbutton(
            head,
            text="Beep on new image",
            variable=self._beep_var,
            command=lambda: self._beep_set(bool(self._beep_var.get())),
        ).pack(side=tk.LEFT, padx=(0, 12))
        self._wip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            head,
            text="WIP mode",
            variable=self._wip_var,
        ).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Button(head, text="Close tab", command=self._close_current, width=12).pack(
            side=tk.RIGHT
        )

        self._nb = ttk.Notebook(top)
        self._nb.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        _center_window(self, 980, 700)

    def open_card(self, char_id: int, *, mode: str = "omni") -> None:
        mode = "refine" if mode == "refine" else "omni"
        key = (int(char_id), mode)
        existing = self._tabs.get(key)
        if existing is not None:
            self._nb.select(existing[0])
            self.lift()
            self.focus_force()
            return

        tab = ttk.Frame(self._nb)
        panel = OmniPanel(
            tab,
            char_id=int(char_id),
            mode=mode,
            fetch_state=self._fetch_state,
            tap=self._tap,
            beep_get=lambda: bool(self._beep_var.get()),
            prefer_original=self._prefer_original,
            get_text_geo=self._get_text_geo,
            set_text_geo=self._set_text_geo,
            on_log=self._on_log,
            on_done_changed=self._on_done_changed,
            on_title=lambda t, fr=tab: self._nb.tab(fr, text=t),
            on_open_refine=lambda cid: self.open_card(int(cid), mode="refine"),
            wip_get=lambda: bool(self._wip_var.get()),
            on_wip_next=self._wip_advance,
            dm_preview=self._dm_preview,
            on_media_changed=self._on_media_changed,
        )
        panel.pack(fill=tk.BOTH, expand=True)
        label = f"R #{char_id}" if mode == "refine" else f"#{char_id}"
        self._nb.add(tab, text=label)
        self._tabs[key] = (tab, panel)
        self._nb.select(tab)
        self._squeeze_tabs()
        self.lift()
        self.focus_force()

    def _wip_advance(self, old_char_id: int) -> None:
        if self._wip_busy or self._fetch_undone is None:
            return
        if not bool(self._wip_var.get()):
            return
        self._wip_busy = True
        old = int(old_char_id)

        def on_ok(body: dict) -> None:
            self._wip_busy = False
            items = body.get("items") or []
            cands = [
                int(it.get("id") or 0)
                for it in items
                if int(it.get("id") or 0) > 0 and int(it.get("id") or 0) != old
            ]
            if not cands:
                self._on_log("WIP: no undone cards left")
                return
            import random

            nxt = random.choice(cands)
            self._replace_omni_tab(old, nxt)

        def on_err(exc: BaseException) -> None:
            self._wip_busy = False
            self._on_log(f"WIP next failed: {exc}")

        self._fetch_undone(on_ok, on_err, exclude_id=old)

    def _replace_omni_tab(self, old_cid: int, new_cid: int) -> None:
        key = (int(old_cid), "omni")
        existing = self._tabs.get(key)
        if existing is not None:
            fr, _panel = existing
            try:
                self._nb.forget(fr)
            except Exception:
                pass
            try:
                fr.destroy()
            except Exception:
                pass
            del self._tabs[key]
        self.open_card(int(new_cid), mode="omni")
        self._on_log(f"WIP → #{new_cid}")

    def _squeeze_tabs(self) -> None:
        n = len(self._tabs)
        # Keep the host readable; shrink a bit when many tabs pile up.
        w = max(860, 980 - max(0, n - 2) * 40)
        h = max(580, 700 - max(0, n - 2) * 20)
        _center_window(self, w, h)

    def _close_current(self) -> None:
        try:
            cur = self._nb.select()
        except Exception:
            return
        if not cur:
            return
        for key, (fr, _panel) in list(self._tabs.items()):
            if str(fr) == str(cur):
                self._nb.forget(fr)
                fr.destroy()
                del self._tabs[key]
                break
        if not self._tabs:
            self.destroy()
        else:
            self._squeeze_tabs()


# Back-compat alias used by older call sites.
OmniWindow = OmniHost
