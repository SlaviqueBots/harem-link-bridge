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
VIEW_FULL = 760
_CAP_LINES = 2
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

_BG = "#1e1f22"
_BG2 = "#2b2d31"
_FG = "#f2f3f5"
_MUTED = "#b5bac1"
_CRAFT_BG = "#3a2f4a"
_CRAFT_BG2 = "#2f3d4a"
_STATUS_BG = "#3a3a3a"
_LINK_BG = "#1e4a5c"
_LIT_BG = "#1b8f4a"
_LIT_FG = "#ffffff"
_DIS_BG = "#2a2a2a"
_DIS_FG = "#777777"
_MODE_BG = "#5a3d2a"
_ACCENT = "#5865f2"

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


def _url_path(url: str) -> str:
    return (url or "").strip().lower().split("?", 1)[0]


def is_video_url(url: str) -> bool:
    path = _url_path(url)
    return any(path.endswith(ext) for ext in (".mp4", ".webm", ".mkv", ".mov", ".m4v"))


def is_gif_url(url: str) -> bool:
    return _url_path(url).endswith(".gif")


def _looks_like_video_bytes(data: bytes) -> bool:
    if not data or len(data) < 12:
        return False
    # ISO BMFF / MP4
    if data[4:8] == b"ftyp":
        return True
    # EBML / WebM / MKV
    if data[:4] == b"\x1aE\xdf\xa3":
        return True
    return False


def omni_display_url(
    state: dict[str, Any],
    *,
    full: bool,
    prefer_original: bool,
) -> str:
    """URL for the omni image pane (preview vs original/sample/gif/video)."""
    st = state or {}
    preview = (st.get("preview_url") or "").strip()
    sample = (st.get("image_url") or "").strip()
    original = (st.get("file_url") or "").strip()
    if not full:
        return preview or sample or ""
    if prefer_original:
        cands = [original, sample, preview]
    else:
        cands = [sample, original, preview]
    for u in cands:
        if u:
            return u
    return ""


def _pil_rgb_still(data: bytes):
    """Decode first frame of jpeg/png/webp/gif into RGB (no video)."""
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    try:
        im.seek(0)
    except Exception:
        pass
    if im.mode == "P":
        im = im.convert("RGBA")
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", im.size, (17, 18, 20))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def _pad_color_for_panel(panel: Any) -> tuple[int, int, int]:
    pad = (17, 18, 20)
    try:
        host = panel.winfo_toplevel()
        pal = getattr(host, "_omni_palette", None) or {}
        raw = str(pal.get("canvas") or "")
        if raw.startswith("#") and len(raw) == 7:
            return (int(raw[1:3], 16), int(raw[3:5], 16), int(raw[5:7], 16))
    except Exception:
        pass
    return pad


def _fit_rgb_to_box(im: Any, box: tuple[int, int], pad: tuple[int, int, int]):
    from PIL import Image, ImageOps

    fitted = ImageOps.contain(im, box, method=Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", box, pad)
    ox = (box[0] - fitted.size[0]) // 2
    oy = (box[1] - fitted.size[1]) // 2
    canvas.paste(fitted, (ox, oy))
    return canvas


def _overlay_video_play_affordance(canvas: Any) -> Any:
    """Draw a play button + banner so a video poster is not mistaken for a still."""
    from PIL import Image, ImageDraw, ImageFont

    rgba = canvas.convert("RGBA")
    overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    w, h = rgba.size
    r = max(26, min(w, h) // 9)
    cx, cy = w // 2, h // 2
    d.ellipse(
        (cx - r, cy - r, cx + r, cy + r),
        fill=(0, 0, 0, 150),
        outline=(255, 255, 255, 210),
        width=max(2, r // 14),
    )
    tri = [
        (cx - r // 3, cy - (r * 5) // 9),
        (cx - r // 3, cy + (r * 5) // 9),
        (cx + (r * 5) // 9, cy),
    ]
    d.polygon(tri, fill=(255, 255, 255, 235))
    bh = max(24, min(36, h // 16))
    d.rectangle((0, h - bh, w, h), fill=(0, 0, 0, 170))
    text = "Video — click to play"
    font = ImageFont.load_default()
    for size in (14, 12, 11):
        try:
            font = ImageFont.truetype("segoeui.ttf", size)
            break
        except Exception:
            continue
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(
        ((w - tw) // 2, h - bh + max(0, (bh - th) // 2) - 1),
        text,
        fill=(255, 255, 255, 245),
        font=font,
    )
    return Image.alpha_composite(rgba, overlay).convert("RGB")

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


def _apply_omni_theme(root: tk.Misc, mode: str | None = None) -> dict[str, str]:
    """Borderless chrome matching the app dark/light palette."""
    from link_bridge.theme import normalize_theme, palette, surface_for

    if mode is None:
        c = surface_for(root)
        mode = str(c.get("mode") or "dark")
    else:
        mode = normalize_theme(mode)
        c = palette(mode)

    bg = c["bg"]
    bg2 = c["bg2"]
    bg3 = c["bg3"]
    fg = c["fg"]
    muted = c["muted"]
    hover = c.get("hover") or bg3
    accent = c["accent"]
    canvas = c["canvas"]

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    edge = {
        "background": bg,
        "foreground": fg,
        "borderwidth": 0,
        "relief": "flat",
        "lightcolor": bg,
        "darkcolor": bg,
        "bordercolor": bg,
        "focuscolor": bg,
    }
    style.configure("Omni.TFrame", **edge)
    style.configure("Omni.TLabel", background=bg, foreground=fg, borderwidth=0)
    style.configure(
        "Omni.Muted.TLabel",
        background=bg,
        foreground=muted,
        font=("Segoe UI", 8),
        borderwidth=0,
    )
    style.configure(
        "Omni.TCheckbutton",
        background=bg,
        foreground=fg,
        focuscolor=bg,
        borderwidth=0,
        lightcolor=bg,
        darkcolor=bg,
    )
    style.map(
        "Omni.TCheckbutton",
        background=[("active", bg)],
        foreground=[("disabled", muted), ("active", fg)],
    )
    style.configure(
        "Omni.TButton",
        background=bg2,
        foreground=fg,
        borderwidth=0,
        focusthickness=0,
        focuscolor=bg2,
        padding=(8, 2),
        relief="flat",
        lightcolor=bg2,
        darkcolor=bg2,
        bordercolor=bg2,
    )
    style.map(
        "Omni.TButton",
        background=[("active", hover), ("disabled", bg2)],
        foreground=[("disabled", muted)],
        relief=[("pressed", "flat"), ("!pressed", "flat")],
    )
    style.configure(
        "Omni.TNotebook",
        background=bg,
        borderwidth=0,
        relief="flat",
        tabmargins=(2, 2, 2, 0),
        lightcolor=bg,
        darkcolor=bg,
        bordercolor=bg,
    )
    style.configure(
        "Omni.TNotebook.Tab",
        background=bg2,
        foreground=muted,
        padding=(10, 3),
        borderwidth=0,
        relief="flat",
        lightcolor=bg2,
        darkcolor=bg2,
        bordercolor=bg2,
        focuscolor=bg2,
    )
    style.map(
        "Omni.TNotebook.Tab",
        background=[("selected", bg3), ("active", hover)],
        foreground=[("selected", fg), ("active", fg)],
        lightcolor=[("selected", bg3), ("active", hover)],
        darkcolor=[("selected", bg3), ("active", hover)],
        bordercolor=[("selected", bg3), ("active", hover)],
        expand=[("selected", [1, 1, 1, 0])],
    )
    style.configure(
        "Omni.Horizontal.TProgressbar",
        background=accent,
        troughcolor=bg2,
        bordercolor=bg,
        lightcolor=accent,
        darkcolor=accent,
    )
    try:
        root.configure(bg=bg)
    except Exception:
        pass
    # Remember for panels (caption / image pad).
    try:
        root._omni_palette = c  # type: ignore[attr-defined]
    except Exception:
        pass
    return c


def _section(parent: tk.Misc, title: str) -> tuple[ttk.Frame, ttk.Frame]:
    """Flat section: muted title + body frame (no groove border)."""
    wrap = ttk.Frame(parent, style="Omni.TFrame")
    ttk.Label(wrap, text=title.upper(), style="Omni.Muted.TLabel").pack(
        anchor=tk.W, pady=(0, 2)
    )
    body = ttk.Frame(wrap, style="Omni.TFrame")
    body.pack(fill=tk.X)
    return wrap, body


def _chunk(specs: list[dict[str, Any]], cols: int) -> list[list[dict[str, Any]]]:
    cols = max(1, int(cols))
    return [specs[i : i + cols] for i in range(0, len(specs), cols)]


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
        full_image_get: Callable[[], bool] | None = None,
        on_done_changed: Callable[[int, bool], None] | None = None,
        on_title: Callable[[str], None] | None = None,
        on_open_refine: Callable[[int], None] | None = None,
        wip_get: Callable[[], bool] | None = None,
        on_wip_next: Callable[[int], None] | None = None,
        dm_preview: Callable[[int, OkCb, ErrCb], None] | None = None,
        on_media_changed: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(master, style="Omni.TFrame")
        self._char_id = int(char_id)
        self._mode = "refine" if mode == "refine" else "omni"
        self._fetch_state = fetch_state
        self._tap = tap
        self._beep_get = beep_get
        self._prefer_original = prefer_original
        self._full_image_get = full_image_get or (lambda: False)
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
        self._img_bytes: bytes | None = None
        self._paint_gen = 0
        self._fit_after: str | None = None
        self._fit_box: tuple[int, int] = (0, 0)
        self._flow_after: str | None = None
        self._flow_cols: tuple[int, int, int] = (0, 0, 0)
        self._state: dict[str, Any] = {}
        self._btns: list[tk.Button] = []
        self._anim_after: str | None = None
        self._anim_frames: list[Any] = []
        self._anim_delays: list[int] = []
        self._anim_idx = 0
        self._anim_data: bytes | None = None
        self._img_video_still = False

        body = ttk.Frame(self, style="Omni.TFrame")
        body.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Compact dark dock under the image.
        controls = ttk.Frame(body, style="Omni.TFrame")
        controls.pack(side=tk.BOTTOM, fill=tk.X)

        head = ttk.Frame(controls, style="Omni.TFrame")
        head.pack(fill=tk.X)
        self._name_var = tk.StringVar(value=f"#{char_id}")
        ttk.Label(
            head,
            textvariable=self._name_var,
            style="Omni.TLabel",
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.LEFT, anchor=tk.W)
        ttk.Button(
            head, text="Refresh", command=self.reload, width=8, style="Omni.TButton"
        ).pack(side=tk.RIGHT)
        self._status_var = tk.StringVar(value="Loading…")
        self._status_lbl = ttk.Label(
            head, textvariable=self._status_var, style="Omni.TLabel", wraplength=280
        )
        self._status_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 6))

        self._prog = ttk.Progressbar(
            controls, mode="determinate", maximum=100, style="Omni.Horizontal.TProgressbar"
        )
        self._prog_after: str | None = None
        self._prog_t0 = 0.0
        self._prog_eta = 12.0
        self._prog.pack_forget()

        craft_title = "Exclude tag" if self._mode == "refine" else "Alter image"
        craft_wrap, self._craft_fr = _section(controls, craft_title)
        craft_wrap.pack(fill=tk.X, pady=(4, 2))

        mid = ttk.Frame(controls, style="Omni.TFrame")
        mid.pack(fill=tk.X, pady=(0, 2))
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=2)
        status_wrap, self._status_fr = _section(mid, "Card")
        status_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        link_wrap, self._link_fr = _section(mid, "Open in browser")
        link_wrap.grid(row=0, column=1, sticky="nsew")

        self._cap = tk.Text(
            controls,
            height=_CAP_LINES,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#111214",
            fg=_FG,
            insertbackground=_FG,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            padx=6,
            pady=2,
            font=("Segoe UI", 8),
        )
        self._cap.pack(fill=tk.X, pady=(2, 0))

        # Image fills everything above the dock.
        self._left = tk.Frame(body, bg="#111214", highlightthickness=0, bd=0)
        self._left.pack(fill=tk.BOTH, expand=True)
        self._left.pack_propagate(False)
        self._img_lbl = tk.Label(
            self._left,
            text="…",
            bg="#111214",
            fg=_MUTED,
            cursor="hand2",
            borderwidth=0,
            highlightthickness=0,
        )
        self._img_lbl.pack(fill=tk.BOTH, expand=True)
        self._img_lbl.bind("<Button-1>", self._open_original)
        self._left.bind("<Configure>", self._on_img_configure)
        self.bind("<Configure>", self._on_panel_configure)
        self.after(30, self.reload)

    def apply_ui_theme(self, pal: dict[str, str]) -> None:
        bg = pal.get("bg") or _BG
        canvas = pal.get("canvas") or "#111214"
        fg = pal.get("fg") or _FG
        muted = pal.get("muted") or _MUTED
        try:
            self._left.configure(bg=canvas)
            self._img_lbl.configure(bg=canvas, fg=muted)
            self._cap.configure(bg=pal.get("log_bg") or canvas, fg=fg, insertbackground=fg)
        except Exception:
            pass
        if self._img_bytes:
            self._fit_box = (0, 0)
            self._fit_photo()

    def _on_panel_configure(self, event=None) -> None:
        if event is not None and getattr(event, "widget", None) is not self:
            return
        try:
            w = max(160, int(self.winfo_width()) - 100)
            self._status_lbl.configure(wraplength=w)
        except Exception:
            pass
        if self._flow_after is not None:
            try:
                self.after_cancel(self._flow_after)
            except Exception:
                pass
        self._flow_after = self.after(200, self._maybe_reflow_buttons)

    def _flow_col_counts(self) -> tuple[int, int, int]:
        try:
            w = max(320, int(self.winfo_width()))
        except Exception:
            w = 700
        craft = max(4, min(8, w // 100))
        status = max(3, min(6, w // 130))
        links = max(4, min(8, w // 100))
        return (craft, status, links)

    def _maybe_reflow_buttons(self) -> None:
        self._flow_after = None
        if not self._state:
            return
        cols = self._flow_col_counts()
        if cols == self._flow_cols:
            return
        self._flow_cols = cols
        self._render_buttons(self._state.get("buttons") or [])

    def _media_url(self, body: dict[str, Any] | None = None) -> str:
        return omni_display_url(
            body if body is not None else self._state,
            full=bool(self._full_image_get()),
            prefer_original=bool(self._prefer_original()),
        )

    def _on_img_configure(self, event=None) -> None:
        if event is not None and getattr(event, "widget", None) is not self._left:
            return
        if self._fit_after is not None:
            try:
                self.after_cancel(self._fit_after)
            except Exception:
                pass
        self._fit_after = self.after(120, self._fit_photo)

    def _pane_box(self) -> tuple[int, int]:
        try:
            w = int(self._left.winfo_width())
            h = int(self._left.winfo_height())
        except Exception:
            return (VIEW, VIEW)
        return (max(64, w), max(64, h))

    def _fit_photo(self) -> None:
        self._fit_after = None
        data = self._img_bytes
        if not data:
            return
        box = self._pane_box()
        if box[0] < 80 or box[1] < 80:
            return
        prev = self._fit_box
        if (
            self._photo is not None
            and abs(box[0] - prev[0]) < 8
            and abs(box[1] - prev[1]) < 8
        ):
            return
        self._render_photo(data, box)

    def _stop_anim(self) -> None:
        if self._anim_after is not None:
            try:
                self.after_cancel(self._anim_after)
            except Exception:
                pass
        self._anim_after = None
        self._anim_frames = []
        self._anim_delays = []
        self._anim_idx = 0
        self._anim_data = None

    def _tick_anim(self) -> None:
        self._anim_after = None
        frames = self._anim_frames
        if len(frames) < 2:
            return
        self._anim_idx = (self._anim_idx + 1) % len(frames)
        photo = frames[self._anim_idx]
        self._photo = photo
        try:
            self._img_lbl.configure(image=photo, text="")
        except Exception:
            return
        delay = 100
        if self._anim_delays:
            delay = max(20, int(self._anim_delays[self._anim_idx % len(self._anim_delays)]))
        self._anim_after = self.after(delay, self._tick_anim)

    def _render_gif_anim(self, data: bytes, box: tuple[int, int]) -> bool:
        """Animate GIF frames in the pane (full-original path)."""
        try:
            from PIL import Image, ImageSequence, ImageTk

            self._stop_anim()
            im = Image.open(io.BytesIO(data))
            pad = _pad_color_for_panel(self)
            frames: list[Any] = []
            delays: list[int] = []
            for frame in ImageSequence.Iterator(im):
                rgb = frame.convert("RGBA") if frame.mode in ("P", "RGBA", "LA") else frame
                if rgb.mode != "RGB":
                    if rgb.mode in ("RGBA", "LA"):
                        bg = Image.new("RGB", rgb.size, pad)
                        bg.paste(rgb, mask=rgb.split()[-1])
                        rgb = bg
                    else:
                        rgb = rgb.convert("RGB")
                canvas = _fit_rgb_to_box(rgb, box, pad)
                frames.append(ImageTk.PhotoImage(canvas))
                delays.append(max(20, int(frame.info.get("duration") or 100)))
                if len(frames) >= 120:
                    break
            if not frames:
                return False
            self._anim_frames = frames
            self._anim_delays = delays
            self._anim_idx = 0
            self._anim_data = data
            self._photo = frames[0]
            self._fit_box = box
            self._img_lbl.configure(image=frames[0], text="")
            if len(frames) > 1:
                self._anim_after = self.after(delays[0], self._tick_anim)
            return True
        except Exception as exc:
            logger.debug("omni gif anim failed: %s", exc)
            self._stop_anim()
            return False

    def _render_photo(self, data: bytes, box: tuple[int, int] | None = None) -> bool:
        try:
            self.update_idletasks()
        except Exception:
            pass
        if box is None:
            box = self._pane_box()
        if box[0] < 80 or box[1] < 80:
            self.after(80, lambda d=data: self._render_photo(d))
            return True
        # Prefer animated GIF when we still have multi-frame source bytes.
        if self._anim_data is data or (
            data[:6] in (b"GIF87a", b"GIF89a") and self._full_image_get()
        ):
            if self._render_gif_anim(data, box):
                self._img_video_still = False
                return True
        self._stop_anim()
        try:
            from PIL import ImageTk

            im = _pil_rgb_still(data)
            pad = _pad_color_for_panel(self)
            canvas = _fit_rgb_to_box(im, box, pad)
            if self._img_video_still:
                canvas = _overlay_video_play_affordance(canvas)
            photo = ImageTk.PhotoImage(canvas)
            self._photo = photo
            self._fit_box = box
            self._img_lbl.configure(image=photo, text="")
            return True
        except Exception as exc:
            self._img_lbl.configure(image="", text="×")
            logger.debug("omni preview decode failed: %s", exc)
            return False

    def refresh_display(self) -> None:
        """Re-fetch after the full-original toggle flips (keeps window size)."""
        url = self._media_url()
        if url:
            if self._full_image_get():
                self._status_var.set("Loading original…")
            self._paint_preview(url)
        else:
            self._stop_anim()
            self._img_bytes = None
            self._img_video_still = False
            self._img_lbl.configure(image="", text="no preview")

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
        if is_video_url(url) or self._img_video_still:
            self._status_var.set("Opening video in your player…")
        open_full_image(url, on_err=lambda e: self._status_var.set(f"Open failed: {e}"))

    def _fallback_still_url(self, failed_url: str) -> str:
        st = self._state or {}
        for key in ("preview_url", "image_url", "file_url"):
            u = (st.get(key) or "").strip()
            if u and u != failed_url and not is_video_url(u):
                return u
        return ""

    def _media_is_video_card(self) -> bool:
        st = self._state or {}
        return is_video_url((st.get("file_url") or "").strip()) or is_video_url(
            (st.get("image_url") or "").strip()
        )

    def _paint_preview(self, url: str) -> None:
        if not url:
            self._stop_anim()
            self._img_bytes = None
            self._img_video_still = False
            self._img_lbl.configure(image="", text="no preview")
            return
        self._paint_gen += 1
        gen = self._paint_gen
        want_full = bool(self._full_image_get())
        video = is_video_url(url)

        def on_data(data: bytes, *, kind: str = "image") -> None:
            def ui() -> None:
                if gen != self._paint_gen:
                    return
                if _looks_like_video_bytes(data) and kind != "still":
                    still = self._fallback_still_url(url)
                    if still:
                        self._paint_preview(still)
                    else:
                        self._img_lbl.configure(image="", text="video")
                    return
                self._img_bytes = data
                self._img_video_still = bool(
                    kind == "still"
                    or video
                    or (want_full and self._media_is_video_card() and not is_gif_url(url))
                )
                try:
                    if kind == "gif" or (want_full and is_gif_url(url)):
                        self._img_video_still = False
                        ok = self._render_gif_anim(data, self._pane_box()) or self._render_photo(
                            data
                        )
                    else:
                        self._stop_anim()
                        ok = self._render_photo(data)
                    if not ok:
                        raise RuntimeError("decode failed")
                    if want_full and not self._busy:
                        if self._img_video_still:
                            self._status_var.set("Video — click image to play")
                        elif is_gif_url(url) or kind == "gif":
                            detail = str(self._state.get("detail") or "Ready")
                            self._status_var.set(f"{detail} · gif")
                        else:
                            self._status_var.set(str(self._state.get("detail") or "Ready"))
                except Exception as exc:
                    self._img_lbl.configure(image="", text="×")
                    logger.debug("omni preview decode failed: %s", exc)
                    if want_full:
                        still = self._fallback_still_url(url)
                        if still:
                            self._status_var.set("Original failed — showing preview")
                            self._paint_preview(still)

            try:
                self.after(0, ui)
            except Exception:
                pass

        def on_err(_exc: BaseException) -> None:
            def ui() -> None:
                if gen != self._paint_gen:
                    return
                if want_full:
                    still = self._fallback_still_url(url)
                    if still:
                        self._status_var.set("Original failed — showing preview")
                        self._paint_preview(still)
                        return
                self._img_lbl.configure(image="", text="×")

            try:
                self.after(0, ui)
            except Exception:
                pass

        if want_full and video:
            from link_bridge.thumb_grid import _fetch_pool
            from link_bridge.video_still import extract_video_still_bytes, ffmpeg_available

            if not ffmpeg_available():
                still = self._fallback_still_url(url)
                if still:
                    self._status_var.set("Video — click image to play")
                    self._paint_preview(still)
                else:
                    self._img_lbl.configure(image="", text="video")
                return

            def worker() -> None:
                try:
                    data = extract_video_still_bytes(url, timeout=45.0)
                    on_data(data, kind="still")
                except Exception as exc:
                    on_err(exc)

            _fetch_pool.submit(worker)
            return

        if want_full and not video:
            from link_bridge.thumb_grid import _fetch_pool, fetch_url_bytes

            def worker() -> None:
                try:
                    data = fetch_url_bytes(url, timeout=45.0, retries=2)
                    on_data(data, kind="gif" if is_gif_url(url) else "image")
                except Exception as exc:
                    on_err(exc)

            _fetch_pool.submit(worker)
            return

        # Preview mode: never pull raw video into the thumb path.
        if video:
            still = self._fallback_still_url(url)
            if still:
                self._paint_preview(still)
            else:
                self._img_lbl.configure(image="", text="video")
            return

        schedule_thumb_fetch(url, on_data=lambda d: on_data(d), on_err=on_err)

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
        show = self._media_url(body)
        if self._full_image_get() and show and show != prev:
            self._status_var.set("Loading original…")
        self._paint_preview(show)
        if acquired and show and self._beep_get():
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
        loading_full = (
            bool(self._full_image_get())
            and bool(show)
            and show != prev
        )
        if bool(body.get("busy")):
            self._status_var.set("Working…")
        elif loading_full:
            self._status_var.set("Loading original…")
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
            self._prog.pack(fill=tk.X, pady=(0, 2), before=self._craft_fr)
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
                try:
                    child.destroy()
                except Exception:
                    pass
            # Reset grid column weights so reflow can change density.
            try:
                cols = int(fr.grid_size()[0])
            except Exception:
                cols = 0
            for c in range(max(cols, 12)):
                try:
                    fr.columnconfigure(c, weight=0, uniform="")
                except Exception:
                    pass

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
            return {"bg": _DIS_BG, "fg": _DIS_FG, "activebackground": _DIS_BG, "relief": tk.FLAT, "bd": 0}
        if lit:
            return {
                "bg": _LIT_BG,
                "fg": _LIT_FG,
                "activebackground": "#24a656",
                "relief": tk.FLAT,
                "bd": 0,
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
            "relief": tk.FLAT,
            "bd": 0,
            "font": ("Segoe UI", 9),
        }

    def _place_row(self, parent: tk.Misc, specs: list[dict[str, Any]], row: int) -> None:
        """Equal-width buttons in one grid row."""
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
            btn = tk.Button(
                parent,
                text=text,
                command=cmd,
                padx=4,
                pady=3,
                cursor="hand2" if enabled else "arrow",
                state=tk.NORMAL if enabled else tk.DISABLED,
                highlightthickness=1 if spec.get("lit") else 0,
                highlightbackground=_LIT_BG,
                highlightcolor=_LIT_BG,
                **style,
            )
            btn.grid(row=row, column=c, sticky="nsew", padx=1, pady=1)
            self._btns.append(btn)

    def _place_flow(self, parent: tk.Misc, specs: list[dict[str, Any]], cols: int) -> None:
        """Pack buttons into a dense N-column grid (no half-empty Telegram rows)."""
        for child in list(parent.winfo_children()):
            child.destroy()
        if not specs:
            ttk.Label(parent, text="—", style="Omni.TLabel").grid(
                row=0, column=0, sticky="w", padx=4
            )
            return
        for i, row in enumerate(_chunk(specs, cols)):
            self._place_row(parent, row, i)

    def _render_buttons(self, rows: list[Any]) -> None:
        self._clear_btns()
        crafts: list[dict[str, Any]] = []
        status: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, list):
                continue
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
                    links.append(btn)
                elif kind == "status":
                    status.append(btn)
                else:
                    crafts.append(btn)
        # Keep reshape ± paired when reflowing.
        paired = _coalesce_reshape_rows([[c] for c in crafts])
        crafts = [b for row in paired for b in row]
        if self._mode == "omni" and self._dm_preview is not None:
            status.append(
                {
                    "text": "DM preview",
                    "op": "dmp",
                    "arg": "",
                    "url": "",
                    "kind": "status",
                    "lit": False,
                    "enabled": True,
                }
            )
        craft_cols, status_cols, link_cols = self._flow_col_counts()
        self._flow_cols = (craft_cols, status_cols, link_cols)
        self._place_flow(self._craft_fr, crafts, craft_cols)
        self._place_flow(self._status_fr, status, status_cols)
        self._place_flow(self._link_fr, links, link_cols)

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
        prev_before = self._media_url()

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
                acquired = self._media_url(body) != prev_before and op in (
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
        full_image_get: Callable[[], bool] | None = None,
        full_image_set: Callable[[bool], None] | None = None,
        get_window_geo: Callable[[], str] | None = None,
        set_window_geo: Callable[[str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_done_changed: Callable[[int, bool], None] | None = None,
        fetch_undone: Callable[..., None] | None = None,
        dm_preview: Callable[[int, OkCb, ErrCb], None] | None = None,
        on_media_changed: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Omnicraft")
        self.minsize(560, 480)
        from link_bridge.theme import normalize_theme, surface_for

        mode = normalize_theme(surface_for(master).get("mode") or "dark")
        self._ui_mode = mode
        pal = _apply_omni_theme(self, mode)
        self.configure(bg=pal["bg"])
        self._fetch_state = fetch_state
        self._tap = tap
        self._beep_get = beep_get
        self._beep_set = beep_set
        self._prefer_original = prefer_original
        self._full_image_get = full_image_get or (lambda: False)
        self._full_image_set = full_image_set or (lambda _v: None)
        self._get_text_geo = get_text_geo
        self._set_text_geo = set_text_geo
        self._get_window_geo = get_window_geo or (lambda: "")
        self._set_window_geo = set_window_geo
        self._on_log = on_log or (lambda _m: None)
        self._on_done_changed = on_done_changed
        self._fetch_undone = fetch_undone
        self._dm_preview = dm_preview
        self._on_media_changed = on_media_changed
        self._tabs: dict[tuple[int, str], tuple[ttk.Frame, OmniPanel]] = {}
        self._wip_busy = False
        self._geo_save_after: str | None = None
        self._geo_ready = False

        top = ttk.Frame(self, padding=(4, 2), style="Omni.TFrame")
        top.pack(fill=tk.BOTH, expand=True)
        head = ttk.Frame(top, style="Omni.TFrame")
        head.pack(fill=tk.X)
        self._beep_var = tk.BooleanVar(value=bool(self._beep_get()))
        ttk.Checkbutton(
            head,
            text="Beep",
            style="Omni.TCheckbutton",
            variable=self._beep_var,
            command=lambda: self._beep_set(bool(self._beep_var.get())),
        ).pack(side=tk.LEFT, padx=(0, 8))
        self._full_var = tk.BooleanVar(value=bool(self._full_image_get()))
        ttk.Checkbutton(
            head,
            text="Full original",
            style="Omni.TCheckbutton",
            variable=self._full_var,
            command=self._on_full_image_toggle,
        ).pack(side=tk.LEFT, padx=(0, 8))
        self._wip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            head,
            text="WIP",
            style="Omni.TCheckbutton",
            variable=self._wip_var,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            head,
            text="Close tab",
            command=self._close_current,
            width=10,
            style="Omni.TButton",
        ).pack(side=tk.RIGHT)

        self._nb = ttk.Notebook(top, style="Omni.TNotebook")
        self._nb.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._restore_or_center()
        self.bind("<Configure>", self._on_configure)
        self.after(250, self._arm_geo_save)

    def apply_ui_theme(self, mode: str) -> None:
        """Follow the main app Dark/Light toggle."""
        from link_bridge.theme import normalize_theme

        mode = normalize_theme(mode)
        self._ui_mode = mode
        pal = _apply_omni_theme(self, mode)
        try:
            self.configure(bg=pal["bg"])
        except Exception:
            pass
        for _fr, panel in list(self._tabs.values()):
            try:
                panel.apply_ui_theme(pal)
            except Exception:
                logger.debug("omni panel theme failed", exc_info=True)

    def _arm_geo_save(self) -> None:
        self._geo_ready = True

    def _restore_or_center(self) -> None:
        saved = (self._get_window_geo() or "").strip()
        if saved:
            try:
                self.geometry(saved)
                return
            except Exception:
                pass
        _center_window(self, 980, 700)

    def _on_configure(self, event=None) -> None:
        if event is not None and getattr(event, "widget", None) is not self:
            return
        if not self._geo_ready or self._set_window_geo is None:
            return
        if self._geo_save_after is not None:
            try:
                self.after_cancel(self._geo_save_after)
            except Exception:
                pass
        self._geo_save_after = self.after(350, self._persist_geometry)

    def _persist_geometry(self) -> None:
        self._geo_save_after = None
        if self._set_window_geo is None:
            return
        try:
            geo = self.geometry()
        except Exception:
            return
        if geo:
            self._set_window_geo(geo)

    def _on_close(self) -> None:
        self._persist_geometry()
        self.destroy()

    def _on_full_image_toggle(self) -> None:
        enabled = bool(self._full_var.get())
        self._full_image_set(enabled)
        for _fr, panel in list(self._tabs.values()):
            try:
                panel.refresh_display()
            except Exception:
                logger.debug("omni full-image refresh failed", exc_info=True)

    def open_card(self, char_id: int, *, mode: str = "omni") -> None:
        mode = "refine" if mode == "refine" else "omni"
        key = (int(char_id), mode)
        existing = self._tabs.get(key)
        if existing is not None:
            self._nb.select(existing[0])
            self.lift()
            self.focus_force()
            return

        tab = ttk.Frame(self._nb, style="Omni.TFrame")
        panel = OmniPanel(
            tab,
            char_id=int(char_id),
            mode=mode,
            fetch_state=self._fetch_state,
            tap=self._tap,
            beep_get=lambda: bool(self._beep_var.get()),
            prefer_original=self._prefer_original,
            full_image_get=lambda: bool(self._full_var.get()),
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
            self._on_close()


# Back-compat alias used by older call sites.
OmniWindow = OmniHost
