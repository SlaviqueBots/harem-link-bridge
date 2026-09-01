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
DmCraftFn = Callable[..., None]  # (char_id, craft, on_ok, on_err)
FlavourGetFn = Callable[[int], str]
SilentCraftFn = Callable[[int, str], None]

VIEW = 480
VIEW_FULL = 760
_CAP_LINES = 1
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

# Realistic initial wall-clock ETAs (seconds) for Omni operations.
# These adapt dynamically at runtime based on measured server response times.
_DEFAULT_ETA_SEC: dict[str, float] = {
    "rs": 3.2,
    "rm": 4.5,
    "sl": 3.2,
    "sm": 4.5,
    "po": 3.0,
    "pa": 4.5,
    "au": 4.0,
    "am": 5.0,
    "ti": 3.5,
    "uo": 2.0,
    "ld": 1.8,
    "rj": 5.5,
    "mi": 1.2,
    "cp": 1.2,
    "dn": 0.8,
    "ud": 0.8,
    "hi": 0.8,
    "sh": 0.8,
    "fl": 0.8,
    "flset": 0.8,
    "rfl": 0.8,
}

_NO_PROGRESS_OPS = frozenset(
    {"mi", "dn", "ud", "hi", "sh", "fl", "flset", "rfl", "cp", "uo", "dmp", "vr"}
)

_ADAPTIVE_ETA: dict[str, float] = dict(_DEFAULT_ETA_SEC)


def calculate_omni_progress(elapsed: float, eta: float) -> float:
    """Calculate smooth progress percentage [0..100] based on elapsed time and expected ETA."""
    el = max(0.0, float(elapsed))
    target_eta = max(0.6, float(eta))
    ratio = el / target_eta
    if ratio <= 1.0:
        # Smooth ease-out curve from 3% to 88% over eta
        frac = 1.0 - (1.0 - ratio) ** 1.6
        return float(min(88.0, 3.0 + 85.0 * frac))
    # Beyond eta: decelerate toward 95% while waiting for server response
    over = el - target_eta
    frac = 1.0 - math.exp(-over / (target_eta * 0.9))
    return float(min(95.0, 88.0 + 7.0 * frac))


def update_adaptive_eta(op: str, measured_elapsed: float) -> float:
    """Update exponentially weighted moving average ETA for operation."""
    op_key = str(op or "").strip()
    if not op_key:
        return 3.0
    measured = max(0.2, float(measured_elapsed))
    prev = _ADAPTIVE_ETA.get(op_key, _DEFAULT_ETA_SEC.get(op_key, 3.0))
    # Exponential moving average with 35% weight on new measurement, clamped between 0.6s and 45s
    new_val = max(0.6, min(45.0, 0.65 * prev + 0.35 * measured))
    _ADAPTIVE_ETA[op_key] = new_val
    return new_val


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


def omni_media_needs_repaint(
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    *,
    full: bool,
    prefer_original: bool,
    has_bytes: bool,
) -> bool:
    """Repaint only when media changed or no decoded image exists yet."""
    if not has_bytes:
        return True
    return omni_display_url(
        old_state, full=full, prefer_original=prefer_original
    ) != omni_display_url(
        new_state, full=full, prefer_original=prefer_original
    )


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
        font="TkSmallCaptionFont",
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
        "Omni.TabActive.TButton",
        background=bg3,
        foreground=fg,
        borderwidth=0,
        focusthickness=0,
        focuscolor=bg3,
        padding=(8, 2),
        relief="flat",
        lightcolor=bg3,
        darkcolor=bg3,
        bordercolor=bg3,
    )
    style.map(
        "Omni.TabActive.TButton",
        background=[("active", hover)],
        foreground=[("active", fg)],
    )
    style.configure(
        "Omni.TNotebook",
        background=bg,
        borderwidth=0,
        relief="flat",
        tabmargins=(0, 0, 0, 0),
        lightcolor=bg,
        darkcolor=bg,
        bordercolor=bg,
    )
    # Card ids live in the host toolbar — hide the notebook tab strip.
    style.layout("Omni.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
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
        troughcolor=bg,
        bordercolor=bg,
        lightcolor=accent,
        darkcolor=accent,
        thickness=3,
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
    try:
        from link_bridge.dpi import apply_ui_scale

        extra = float(getattr(root, "_bridge_ui_scale", 1.0) or 1.0)
        apply_ui_scale(root, extra)
    except Exception:
        pass
    return c


def _section(parent: tk.Misc, title: str) -> tuple[ttk.Frame, ttk.Frame]:
    """Flat section: muted title + body frame (no groove border)."""
    wrap = ttk.Frame(parent, style="Omni.TFrame")
    ttk.Label(wrap, text=title.upper(), style="Omni.Muted.TLabel").pack(
        anchor=tk.W, pady=(0, 1)
    )
    body = ttk.Frame(wrap, style="Omni.TFrame")
    body.pack(fill=tk.X)
    return wrap, body


def _chunk(specs: list[dict[str, Any]], cols: int) -> list[list[dict[str, Any]]]:
    cols = max(1, int(cols))
    return [specs[i : i + cols] for i in range(0, len(specs), cols)]


def _craft_slot_key(spec: dict[str, Any]) -> tuple[str, str]:
    op = str(spec.get("op") or "").strip()
    arg = str(spec.get("arg") or "").strip()
    return (op, arg)


_DANBOORU_CRAFT_SLOTS: tuple[tuple[tuple[str, str] | None, ...], ...] = (
    (
        ("rs", "G"), ("rs", "S"), ("rs", "Q"), ("rs", "E"),
        ("au", ""), ("am", ""), ("uo", ""), ("cp", ""),
    ),
    (
        ("rm", "G"), ("rm", "S"), ("rm", "Q"), ("rm", "E"),
        ("po", ""), ("pa", ""), ("ti", ""), ("rf", ""),
    ),
)

_R34_CRAFT_SLOTS: tuple[tuple[tuple[str, str] | None, ...], ...] = (
    (
        ("rs", ""), ("rm", "M"), ("sl", ""), ("sm", ""),
        ("au", ""), ("am", ""), ("uo", ""), ("cp", ""),
    ),
    (
        ("po", ""), ("pa", ""), ("ti", ""), ("rf", ""),
        None, None, None, None,
    ),
)


def _uses_r34_craft_slots(crafts: list[dict[str, Any]]) -> bool:
    keys = {_craft_slot_key(s) for s in crafts}
    if any(op in ("sl", "sm") for op, _arg in keys):
        return True
    if ("rs", "") in keys:
        return True
    return False


def stable_omni_craft_rows(
    crafts: list[dict[str, Any]],
) -> list[list[dict[str, Any] | None]]:
    """Fixed OmniCraft slots so missing GSQE/title/etc. leave holes instead of reflow."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for spec in crafts:
        key = _craft_slot_key(spec)
        if key[0] and key not in by_key:
            by_key[key] = spec
    template = _R34_CRAFT_SLOTS if _uses_r34_craft_slots(crafts) else _DANBOORU_CRAFT_SLOTS
    used: set[tuple[str, str]] = set()
    rows: list[list[dict[str, Any] | None]] = []
    for slot_row in template:
        out: list[dict[str, Any] | None] = []
        for slot in slot_row:
            if slot is None:
                out.append(None)
                continue
            spec = by_key.get(slot)
            if spec is not None:
                used.add(slot)
                out.append(spec)
            else:
                out.append(None)
        rows.append(out)
    leftovers = [s for s in crafts if _craft_slot_key(s) not in used]
    if leftovers:
        for chunk in _chunk(leftovers, 8):
            pad: list[dict[str, Any] | None] = list(chunk)
            while len(pad) < 8:
                pad.append(None)
            rows.append(pad)
    while rows and all(cell is None for cell in rows[-1]):
        rows.pop()
    return rows


def _client_status(detail: str = "", *, busy: bool = False) -> str:
    """English-only status for Bridge (never show Russian server detail in the UI)."""
    if busy:
        return "Working…"
    d = (detail or "").strip()
    if not d:
        return "Ready"
    low = d.lower()
    if low.startswith("mirror:"):
        return "Mirrored"
    if low in ("ok", "ready"):
        return "Ready"
    if any("\u0400" <= c <= "\u04ff" for c in d):
        if "…" in d or "меня" in low:
            return "Working…"
        return "Ready"
    return d


def repeat_key_label(keysym: str) -> str:
    key = str(keysym or "space").strip().lower() or "space"
    if key == "space":
        return "Space"
    if len(key) == 1:
        return key.upper()
    return key.replace("_", " ").title()


def event_matches_repeat_key(event: Any, keysym: str) -> bool:
    want = str(keysym or "space").strip().lower() or "space"
    got = str(getattr(event, "keysym", "") or "").strip().lower()
    if want == "space":
        return got == "space"
    return got == want


def find_omni_repeat_spec(
    buttons: list[Any],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Prefer the green (lit) control; else the last local click if still present."""
    lit: list[dict[str, Any]] = []
    present: list[dict[str, Any]] = []
    for row in buttons or []:
        if not isinstance(row, list):
            continue
        for btn in row:
            if not isinstance(btn, dict):
                continue
            if not str(btn.get("op") or "").strip():
                continue
            present.append(btn)
            if btn.get("lit"):
                lit.append(btn)
    if lit:
        return lit[0]
    if fallback:
        fk = _craft_slot_key(fallback)
        for btn in present:
            if _craft_slot_key(btn) == fk:
                return btn
    return None


def _dispatch_omni_keypress(event: Any) -> str | None:
    widget = getattr(event, "widget", None)
    if widget is None or isinstance(widget, str):
        return None
    try:
        top = widget.winfo_toplevel()
    except Exception:
        return None
    handler = getattr(top, "_omni_keypress", None)
    if handler is None:
        return None
    return handler(event)


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
        on_plan_changed: Callable[[int], None] | None = None,
        dm_craft: DmCraftFn | None = None,
        get_flavour: FlavourGetFn | None = None,
        on_silent_craft: SilentCraftFn | None = None,
        seed_panel: OmniPanel | None = None,
        on_host_status: Callable[[str], None] | None = None,
        on_balance: Callable[[dict[str, Any]], None] | None = None,
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
        self._on_plan_changed = on_plan_changed
        self._dm_craft = dm_craft
        self._get_flavour = get_flavour
        self._on_silent_craft = on_silent_craft
        self._seed_panel = seed_panel
        self._set_host_status = on_host_status or (lambda _s: None)
        self._on_balance = on_balance
        self._last_status = "Loading…"
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
        self._button_render_sig: tuple[Any, ...] | None = None
        self._anim_after: str | None = None
        self._anim_frames: list[Any] = []
        self._anim_delays: list[int] = []
        self._anim_idx = 0
        self._anim_data: bytes | None = None
        self._img_video_still = False
        self._last_repeat: dict[str, Any] | None = None
        self._panel_bg = _BG

        body = ttk.Frame(self, style="Omni.TFrame")
        body.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Compact dark dock under the image.
        controls = ttk.Frame(body, style="Omni.TFrame")
        controls.pack(side=tk.BOTTOM, fill=tk.X)

        self._craft_fr = tk.Frame(controls, bg=self._panel_bg, highlightthickness=0, bd=0)
        self._craft_fr.pack(fill=tk.X)

        self._prog_after: str | None = None
        self._prog_t0 = 0.0
        self._prog_eta = 12.0
        self._prog_visible = False

        mid = ttk.Frame(controls, style="Omni.TFrame")
        mid.pack(fill=tk.X)
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=2)
        status_wrap, status_body = _section(mid, "Card")
        status_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._status_tools = ttk.Frame(status_body, style="Omni.TFrame")
        self._status_tools.pack(fill=tk.X, pady=(0, 2))
        tk.Button(
            self._status_tools,
            text="Plan",
            command=self._edit_plan,
            bg=_STATUS_BG,
            fg=_FG,
            activebackground=_STATUS_BG,
            relief=tk.FLAT,
            bd=0,
            padx=6,
            pady=2,
            font=("Segoe UI", 9),
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(
            self._status_tools,
            text="Refresh",
            command=self.reload,
            bg=_STATUS_BG,
            fg=_FG,
            activebackground=_STATUS_BG,
            relief=tk.FLAT,
            bd=0,
            padx=6,
            pady=2,
            font=("Segoe UI", 9),
            cursor="hand2",
        ).pack(side=tk.LEFT)
        self._status_fr = ttk.Frame(status_body, style="Omni.TFrame")
        self._status_fr.pack(fill=tk.X)
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
            pady=1,
            font=("Segoe UI", 8),
        )
        self._cap.pack(fill=tk.X, pady=(1, 0))

        # Image fills everything above the dock; plan chips sit on the right.
        self._mid = ttk.Frame(body, style="Omni.TFrame")
        self._mid.pack(fill=tk.BOTH, expand=True)
        self._plan_rail = tk.Frame(self._mid, bg="#111214", highlightthickness=0, bd=0)
        self._plan_rail.pack_propagate(False)
        self._left = tk.Frame(self._mid, bg="#111214", highlightthickness=0, bd=0)
        self._left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
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
        self._prog_accent = "#5865f2"
        self._prog_fill = tk.Frame(
            self._left,
            bg=self._prog_accent,
            height=3,
            highlightthickness=0,
            bd=0,
        )
        self._left.bind("<Configure>", self._on_img_configure)
        self.bind("<Configure>", self._on_panel_configure)
        if self._mode == "refine" and self._seed_panel is not None:
            # The host adds this panel's frame to its notebook immediately after
            # construction.  Bootstrap on idle so _on_title can safely address
            # that tab while still reusing the sibling's already-decoded media.
            self.after_idle(self._bootstrap_from_sibling)
        else:
            self.after(30, self.reload)
        self.after(40, self.refresh_plan_rail)
        self._set_status("Loading…")

    def _set_status(self, detail: str = "", *, busy: bool | None = None) -> None:
        flag = self._busy if busy is None else bool(busy)
        text = _client_status(detail, busy=flag)
        self._last_status = text
        self._set_host_status(text)

    def publish_status_to_host(self) -> None:
        self._set_host_status(self._last_status)

    def apply_ui_theme(self, pal: dict[str, str]) -> None:
        bg = pal.get("bg") or _BG
        canvas = pal.get("canvas") or "#111214"
        fg = pal.get("fg") or _FG
        muted = pal.get("muted") or _MUTED
        self._panel_bg = bg
        accent = pal.get("accent") or "#5865f2"
        self._prog_accent = accent
        try:
            self._left.configure(bg=canvas)
            self._plan_rail.configure(bg=canvas)
            self._img_lbl.configure(bg=canvas, fg=muted)
            self._cap.configure(bg=pal.get("log_bg") or canvas, fg=fg, insertbackground=fg)
            self._craft_fr.configure(bg=bg)
            self._prog_fill.configure(bg=accent)
        except Exception:
            pass
        if self._img_bytes:
            self._fit_box = (0, 0)
            self._fit_photo()
        try:
            self.refresh_plan_rail()
        except Exception:
            pass

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
        use_full = bool(self._full_image_get())
        return omni_display_url(
            body if body is not None else self._state,
            full=use_full,
            prefer_original=bool(self._prefer_original()),
        )

    def _bootstrap_from_sibling(self) -> None:
        """Refine tab: reuse Omni's quick preview, fetch only refine controls."""
        self._busy = False
        self._busy_gen += 1
        sib = self._seed_panel
        if sib is None or not sib._state:
            self.reload()
            return
        self._state = dict(sib._state)
        if self._on_title is not None:
            self._on_title(f"R #{self._char_id}")
        self._set_caption(str(self._state.get("caption") or ""))

        if sib._img_bytes:
            self._img_bytes = sib._img_bytes
            self._img_video_still = sib._img_video_still
            self._fit_box = sib._fit_box
            self._anim_data = sib._anim_data
            if sib._anim_frames and len(sib._anim_frames) > 0:
                self._anim_frames = list(sib._anim_frames)
                self._anim_delays = list(sib._anim_delays)
                self._anim_idx = sib._anim_idx
                self._photo = sib._photo
                self._img_lbl.configure(image=self._photo, text="")
                if len(self._anim_frames) > 1 and self._anim_after is None:
                    self._anim_after = self.after(
                        self._anim_delays[self._anim_idx % len(self._anim_delays)],
                        self._tick_anim,
                    )
            elif sib._photo is not None:
                self._photo = sib._photo
                self._img_lbl.configure(image=self._photo, text="")
        else:
            show = self._media_url()
            if show:
                self._paint_preview(show)

        self._set_status("Loading refine…")

        def on_ok(body: dict) -> None:
            if body.get("op") == "omni_state_ok":
                self._apply_state(body, acquired=False, repaint_image=False)
            else:
                self._set_status(str(body.get("error") or "failed"))

        def on_err(exc: BaseException) -> None:
            self._set_status(f"Load failed: {exc}")

        self._fetch_state(self._char_id, on_ok, on_err, self._mode)

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
            and abs(box[0] - prev[0]) < 16
            and abs(box[1] - prev[1]) < 16
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

            already_playing = bool(
                self._anim_frames
                and len(self._anim_frames) > 0
                and self._anim_data is not None
                and len(self._anim_data) == len(data)
                and self._anim_data[:1024] == data[:1024]
            )
            if (
                already_playing
                and abs(box[0] - self._fit_box[0]) < 16
                and abs(box[1] - self._fit_box[1]) < 16
            ):
                return True

            pad = _pad_color_for_panel(self)

            if not already_playing:
                self._stop_anim()
                im = Image.open(io.BytesIO(data))
                # Step 1: Display first frame immediately so window opening has zero lag
                first_frame = next(ImageSequence.Iterator(im), None)
                if first_frame is None:
                    return False
                rgb0 = first_frame.convert("RGBA") if first_frame.mode in ("P", "RGBA", "LA") else first_frame
                if rgb0.mode != "RGB":
                    if rgb0.mode in ("RGBA", "LA"):
                        bg = Image.new("RGB", rgb0.size, pad)
                        bg.paste(rgb0, mask=rgb0.split()[-1])
                        rgb0 = bg
                    else:
                        rgb0 = rgb0.convert("RGB")
                canvas0 = _fit_rgb_to_box(rgb0, box, pad)
                p0 = ImageTk.PhotoImage(canvas0)
                self._photo = p0
                self._fit_box = box
                self._anim_data = data
                self._img_lbl.configure(image=p0, text="")

            # Step 2: Decode and resize animation frames in background worker pool
            gen = self._paint_gen
            from link_bridge.thumb_grid import _fetch_pool

            def decode_worker() -> None:
                try:
                    w_im = Image.open(io.BytesIO(data))
                    pil_frames: list[Any] = []
                    delays: list[int] = []
                    for frame in ImageSequence.Iterator(w_im):
                        rgb = frame.convert("RGBA") if frame.mode in ("P", "RGBA", "LA") else frame
                        if rgb.mode != "RGB":
                            if rgb.mode in ("RGBA", "LA"):
                                bg = Image.new("RGB", rgb.size, pad)
                                bg.paste(rgb, mask=rgb.split()[-1])
                                rgb = bg
                            else:
                                rgb = rgb.convert("RGB")
                        c = _fit_rgb_to_box(rgb, box, pad)
                        pil_frames.append(c)
                        delays.append(max(20, int(frame.info.get("duration") or 100)))
                        if len(pil_frames) >= 120:
                            break

                    def on_frames_ready() -> None:
                        if gen != self._paint_gen or self._anim_data is not data:
                            return
                        try:
                            from PIL import ImageTk

                            tk_frames = [ImageTk.PhotoImage(c) for c in pil_frames]
                            self._anim_frames = tk_frames
                            self._anim_delays = delays
                            self._anim_idx = 0
                            self._anim_data = data
                            self._fit_box = box
                            if len(tk_frames) > 1 and self._anim_after is None:
                                self._anim_after = self.after(delays[0], self._tick_anim)
                        except Exception as exc:
                            logger.debug("on_frames_ready failed: %s", exc)

                    self.after(0, on_frames_ready)
                except Exception as exc:
                    logger.debug("omni gif background decode failed: %s", exc)

            _fetch_pool.submit(decode_worker)
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
            self._raise_overlay()
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
                self._set_status("Loading original…")
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
            self._set_status("No original URL")
            return
        if is_video_url(url) or self._img_video_still:
            self._set_status("Opening video in your player…")
        open_full_image(url, on_err=lambda e: self._set_status(f"Open failed: {e}"))

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
                            self._set_status("Video — click image to play")
                        elif is_gif_url(url) or kind == "gif":
                            detail = str(self._state.get("detail") or "Ready")
                            self._set_status(f"{detail} · gif")
                        else:
                            self._set_status(str(self._state.get("detail") or ""))
                except Exception as exc:
                    self._img_lbl.configure(image="", text="×")
                    logger.debug("omni preview decode failed: %s", exc)
                    if want_full:
                        still = self._fallback_still_url(url)
                        if still:
                            self._set_status("Original failed — showing preview")
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
                        self._set_status("Original failed — showing preview")
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
                    self._set_status("Video — click image to play")
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
            from link_bridge.thumb_grid import _fetch_pool, cache_get, cache_put, fetch_url_bytes

            cached = cache_get(url)
            if cached is not None:
                on_data(cached, kind="gif" if is_gif_url(url) else "image")
                return

            def worker() -> None:
                try:
                    data = fetch_url_bytes(url, timeout=45.0, retries=2)
                    cache_put(url, data)
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

    def _apply_state(
        self, body: dict[str, Any], *, acquired: bool = False, repaint_image: bool = True
    ) -> None:
        prev_done = bool(self._state.get("done"))
        needs_media_repaint = omni_media_needs_repaint(
            self._state,
            body,
            full=bool(self._full_image_get()),
            prefer_original=bool(self._prefer_original()),
            has_bytes=bool(self._img_bytes),
        )
        try:
            new_cid = int(body.get("char_id") or 0)
            if new_cid > 0:
                self._char_id = new_cid
        except (TypeError, ValueError):
            pass
        self._state = body
        if "flavour" in body:
            self._state["flavour"] = str(body.get("flavour") or "")
        if self._on_balance is not None and "balance" in body:
            try:
                self._on_balance(body)
            except Exception:
                logger.debug("omni balance notify failed", exc_info=True)
        if self._on_title is not None:
            short = f"#{self._char_id}"
            if self._mode == "refine":
                short = f"R {short}"
            self._on_title(short)
        self._set_caption(str(body.get("caption") or ""))
        prev = (body.get("preview_url") or "").strip()
        show = self._media_url(body)
        if repaint_image and needs_media_repaint:
            if self._mode != "refine" and self._full_image_get() and show and show != prev:
                self._set_status("Loading original…")
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
        self._consume_plan_hit(str(body.get("post_url") or ""), acquired=acquired)
        self._stop_progress(done=True)
        self._render_buttons(body.get("buttons") or [])
        loading_full = (
            repaint_image
            and self._mode != "refine"
            and bool(self._full_image_get())
            and bool(show)
            and show != prev
        )
        if bool(body.get("busy")):
            self._set_status("", busy=True)
        elif loading_full:
            self._set_status("Loading original…")
        else:
            self._set_status(str(body.get("detail") or ""))
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

    def _flash_center_notice(self, title: str, sub: str = "") -> None:
        try:
            old = getattr(self, "_notice", None)
            if old is not None:
                old.destroy()
        except Exception:
            pass
        wrap = tk.Frame(
            self._left,
            bg="#0d6b38",
            highlightthickness=5,
            highlightbackground="#9dffc4",
        )
        title_pady = (22, 6) if sub else (28, 28)
        tk.Label(
            wrap,
            text=title,
            font=("Segoe UI", 36, "bold"),
            bg="#0d6b38",
            fg="#ffffff",
        ).pack(padx=48, pady=title_pady)
        if sub:
            tk.Label(
                wrap,
                text=sub,
                font=("Segoe UI", 13),
                bg="#0d6b38",
                fg="#d7ffe8",
            ).pack(pady=(0, 18))
        wrap.place(relx=0.5, rely=0.48, anchor="center")
        self._notice = wrap
        self._raise_overlay()

        def _drop() -> None:
            try:
                if wrap.winfo_exists():
                    wrap.destroy()
            except Exception:
                pass
            if getattr(self, "_notice", None) is wrap:
                self._notice = None

        self.after(3200, _drop)

    def _raise_overlay(self) -> None:
        notice = getattr(self, "_notice", None)
        if notice is not None:
            try:
                if notice.winfo_exists():
                    notice.tkraise()
            except Exception:
                pass
        if getattr(self, "_prog_visible", False):
            try:
                self._prog_fill.tkraise()
            except Exception:
                pass

    def refresh_plan_rail(self) -> None:
        from link_bridge.browser_open import open_url
        from link_bridge.crafting_plans import get_store, preview_label

        for child in list(self._plan_rail.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        sections = get_store().visible_sections(self._char_id)
        if not sections:
            try:
                self._plan_rail.pack_forget()
            except Exception:
                pass
            return
        if not self._plan_rail.winfo_ismapped():
            self._plan_rail.configure(width=176)
            self._plan_rail.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))
        pal_bg = "#111214"
        try:
            pal_bg = str(self._plan_rail.cget("bg") or pal_bg)
        except Exception:
            pass
        pad = tk.Frame(self._plan_rail, bg=pal_bg)
        pad.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        tk.Label(
            pad,
            text="Plan",
            bg=pal_bg,
            fg=_MUTED,
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(fill=tk.X)
        for sec in sections:
            tk.Label(
                pad,
                text=f"{sec.title}  {len(sec.urls)}",
                bg=pal_bg,
                fg=_FG,
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            ).pack(fill=tk.X, pady=(6, 0))
            for url in sec.urls[:8]:
                lab = tk.Label(
                    pad,
                    text=preview_label(url),
                    bg="#2b2d31",
                    fg="#d7e0ff",
                    font=("Segoe UI", 8),
                    anchor="w",
                    cursor="hand2",
                    padx=4,
                    pady=1,
                )
                lab.pack(fill=tk.X, pady=(2, 0))
                lab.bind("<Button-1>", lambda _e, u=url: open_url(u))
            extra = len(sec.urls) - 8
            if extra > 0:
                tk.Label(
                    pad,
                    text=f"+{extra} more",
                    bg=pal_bg,
                    fg=_MUTED,
                    font=("Segoe UI", 7),
                    anchor="w",
                ).pack(fill=tk.X)

    def _edit_plan(self) -> None:
        from link_bridge.crafting_plan_dialog import edit_crafting_plan
        from link_bridge.crafting_plans import card_is_r34, get_store

        store = get_store()
        updated = edit_crafting_plan(
            self,
            char_id=self._char_id,
            sections=store.get_sections(self._char_id),
            is_r34=card_is_r34(self._state),
            geometry=self._get_text_geo(),
            on_geometry=self._set_text_geo,
        )
        if updated is None:
            return
        store.put_sections(self._char_id, updated)
        self.refresh_plan_rail()
        if self._on_plan_changed is not None:
            try:
                self._on_plan_changed(self._char_id)
            except Exception:
                logger.debug("omni plan notify failed", exc_info=True)

    def _consume_plan_hit(self, post_url: str, *, acquired: bool) -> None:
        from link_bridge.crafting_plans import get_store, preview_label

        result = get_store().consume_reached_post(self._char_id, post_url)
        if result.changed:
            self.refresh_plan_rail()
            if self._on_plan_changed is not None:
                try:
                    self._on_plan_changed(self._char_id)
                except Exception:
                    logger.debug("omni plan notify failed", exc_info=True)
        if acquired and result.completed:
            names = " · ".join(result.completed)
            _soft_beep()
            self._flash_center_notice(
                f"{names} DONE" if len(result.completed) == 1 else "DONE",
                "category complete — move on",
            )
        elif acquired and result.one_left:
            names = " · ".join(title for title, _url in result.one_left)
            first_url = result.one_left[0][1]
            _soft_beep()
            self._flash_center_notice(
                f"LAST {names}" if len(result.one_left) == 1 else "LAST LINK",
                f"{preview_label(first_url)}  —  move on",
            )

    def ingest_browser_craft(self, url: str, tags: dict[str, Any] | None) -> dict[str, Any]:
        from link_bridge.crafting_plans import (
            card_identity_from_omni_state,
            classify_plan_section,
            get_store,
        )

        tags = tags or {}
        artists, characters, is_r34 = card_identity_from_omni_state(self._state)
        post_artists = [str(x) for x in (tags.get("artists") or []) if str(x).strip()]
        post_characters = [str(x) for x in (tags.get("characters") or []) if str(x).strip()]
        general = [str(x).lower().replace(" ", "_") for x in (tags.get("general") or [])]
        solo = tags.get("solo")
        if solo is None:
            solo = "solo" in set(general)
        section_id = classify_plan_section(
            card_artists=artists,
            card_characters=characters,
            card_is_r34=is_r34,
            post_artists=post_artists,
            post_characters=post_characters,
            post_solo=bool(solo),
            post_rating=str(tags.get("rating") or ""),
        )
        if not section_id:
            return {
                "ok": False,
                "error": "Post does not match this card’s author or character",
            }
        try:
            title = get_store().append_url(self._char_id, section_id, url)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        self.refresh_plan_rail()
        if self._on_plan_changed is not None:
            try:
                self._on_plan_changed(self._char_id)
            except Exception:
                logger.debug("omni plan notify failed", exc_info=True)
        self._set_status(f"Plan ← {title}")
        return {"ok": True, "detail": f"Added to {title}"}

    def _place_progress_fill(self, value: float) -> None:
        pct = max(0.0, min(100.0, float(value)))
        if pct < 0.5:
            if self._prog_visible:
                try:
                    self._prog_fill.place_forget()
                except Exception:
                    pass
                self._prog_visible = False
            return
        try:
            self._prog_fill.place(
                relx=0,
                rely=1.0,
                relwidth=pct / 100.0,
                height=3,
                anchor="sw",
            )
            self._prog_fill.lift()
            self._raise_overlay()
            self._prog_visible = True
        except Exception:
            pass

    def _show_progress_bar(self) -> None:
        """No-op until _tick_progress paints the first visible slice."""
        return

    def _hide_progress_bar(self) -> None:
        if not self._prog_visible:
            return
        try:
            self._prog_fill.place_forget()
        except Exception:
            pass
        self._prog_visible = False

    def _start_progress(self, op: str) -> None:
        import time

        self._stop_progress(done=False)
        self._prog_op = str(op or "")
        self._prog_eta = float(
            _ADAPTIVE_ETA.get(self._prog_op, _DEFAULT_ETA_SEC.get(self._prog_op, 3.0))
        )
        self._prog_t0 = time.monotonic()
        self._tick_progress()

    def _tick_progress(self) -> None:
        import time

        self._prog_after = None
        if not self._busy:
            return
        elapsed = max(0.0, time.monotonic() - self._prog_t0)
        value = calculate_omni_progress(elapsed, self._prog_eta)
        self._place_progress_fill(value)
        self._prog_after = self.after(40, self._tick_progress)

    def _stop_progress(self, *, done: bool) -> None:
        import time

        if self._prog_after is not None:
            try:
                self.after_cancel(self._prog_after)
            except Exception:
                pass
            self._prog_after = None
        try:
            if done:
                op = getattr(self, "_prog_op", "")
                t0 = getattr(self, "_prog_t0", 0.0)
                if op and t0 > 0.0:
                    update_adaptive_eta(op, time.monotonic() - t0)
        except Exception:
            pass
        self._hide_progress_bar()

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

    def _refresh_button(self, btn: tk.Button, spec: dict[str, Any]) -> None:
        hidden = bool(self._state.get("hidden"))
        done = bool(self._state.get("done"))
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
        if urls:
            cmd = lambda us=urls: self._open_urls(us)
        elif url and not op:
            cmd = lambda u=url: self._open_url(u)
        elif op:
            cmd = lambda o=op, a=arg, t=text: self._click(o, a, t)
        else:
            cmd = lambda: None
            enabled = False
        btn.configure(
            text=text,
            command=cmd,
            cursor="hand2" if enabled else "arrow",
            state=tk.NORMAL if enabled else tk.DISABLED,
            highlightthickness=1 if spec.get("lit") else 0,
            highlightbackground=_LIT_BG,
            highlightcolor=_LIT_BG,
            **self._style_for(spec, enabled=enabled),
        )

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

    def _place_slot_cell(
        self, parent: tk.Misc, spec: dict[str, Any] | None, row: int, col: int
    ) -> None:
        if spec is None:
            return
        hidden = bool(self._state.get("hidden"))
        done = bool(self._state.get("done"))
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
        btn.grid(row=row, column=col, sticky="nsew", padx=1, pady=1)
        self._btns.append(btn)

    def _place_stable_grid(
        self, parent: tk.Misc, grid: list[list[dict[str, Any] | None]]
    ) -> None:
        for child in list(parent.winfo_children()):
            child.destroy()
        cols = 8
        trimmed: list[list[dict[str, Any] | None]] = []
        for row in grid:
            cells = list(row[:cols])
            while cells and cells[-1] is None:
                cells.pop()
            if not cells:
                continue
            trimmed.append(cells)
        if not trimmed:
            return
        panel_bg = getattr(self, "_panel_bg", _BG)
        parent.columnconfigure(0, weight=1)
        for r, cells in enumerate(trimmed):
            specs = [s for s in cells if s is not None]
            if not specs:
                continue
            row_fr = tk.Frame(parent, bg=panel_bg, highlightthickness=0, bd=0)
            row_fr.grid(row=r, column=0, sticky="ew", pady=(0, 1))
            self._place_row(row_fr, specs, 0)

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
                if op == "vr":
                    kind = "status"
                if not kind:
                    if (url or urls) and not op:
                        kind = "link"
                    elif op == "rf":
                        kind = "mode"
                    elif op in ("dn", "ud", "hi", "sh", "fl", "flset", "rfl", "mi", "vr"):
                        kind = "status"
                    else:
                        kind = "craft"
                if kind == "link":
                    links.append(btn)
                elif kind == "status":
                    status.append(btn)
                else:
                    crafts.append(btn)
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
        craft_grid: list[list[dict[str, Any] | None]] | None = None
        if self._mode == "omni":
            craft_grid = stable_omni_craft_rows(crafts)
        else:
            paired = _coalesce_reshape_rows([[c] for c in crafts])
            crafts = [b for row in paired for b in row]

        visible_crafts = (
            [cell for row in craft_grid for cell in row if cell is not None]
            if craft_grid is not None
            else crafts
        )
        flat_specs = visible_crafts + status + links
        craft_mask = (
            tuple(tuple(cell is not None for cell in row) for row in craft_grid)
            if craft_grid is not None
            else (len(crafts), craft_cols)
        )
        render_sig = (
            self._mode,
            craft_mask,
            len(status),
            len(links),
            status_cols,
            link_cols,
        )
        if (
            render_sig == self._button_render_sig
            and len(self._btns) == len(flat_specs)
        ):
            for btn, spec in zip(self._btns, flat_specs):
                self._refresh_button(btn, spec)
            return

        self._clear_btns()
        if craft_grid is not None:
            self._place_stable_grid(self._craft_fr, craft_grid)
        else:
            self._place_flow(self._craft_fr, crafts, craft_cols)
        self._place_flow(self._status_fr, status, status_cols)
        self._place_flow(self._link_fr, links, link_cols)
        self._button_render_sig = render_sig

    def _click_dm_preview(self) -> None:
        if self._busy or self._dm_preview is None:
            return
        self._busy = True
        self._busy_gen += 1
        gen = self._busy_gen
        self._set_status("DM preview…")

        def on_ok(body: dict) -> None:
            if gen != self._busy_gen:
                return
            self._busy = False
            if body.get("op") == "dm_craft_ok":
                self._set_status("DM preview sent")
                self._on_log(f"omni DM preview #{self._char_id}")
            else:
                self._set_status(str(body.get("error") or "DM failed"))

        def on_err(exc: BaseException) -> None:
            if gen != self._busy_gen:
                return
            self._busy = False
            self._set_status(f"DM failed: {exc}")

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
                self._set_status("Refine unavailable")
            return
        if op == "dmp":
            self._click_dm_preview()
            return
        if op == "fl":
            self._edit_flavour()
            return
        self._last_repeat = {"op": op, "arg": arg, "text": label, "lit": True}
        arg_s = None if arg is None or str(arg).strip() == "" else str(arg).strip()
        self._busy = True
        self._busy_gen += 1
        gen = self._busy_gen
        self._set_status(f"{label}…", busy=True)
        if op not in _NO_PROGRESS_OPS:
            self._start_progress(op)
        prev_before = self._media_url()

        def _unstick() -> None:
            if gen != self._busy_gen or not self._busy:
                return
            self._busy = False
            self._stop_progress(done=False)
            self._set_status("Still working — tap Refresh if stuck.")

        self.after(150000, _unstick)

        def on_ok(body: dict) -> None:
            if gen != self._busy_gen:
                return
            self._busy = False
            op_name = str(body.get("op") or "")
            if op_name == "omni_tap_ok":
                if op == "mi":
                    self._stop_progress(done=False)
                    detail = str(body.get("detail") or "")
                    sub = ""
                    if detail.startswith("mirror:"):
                        try:
                            sub = f"→ #{int(detail.split(':', 1)[1])}"
                        except (ValueError, IndexError):
                            pass
                    self._flash_center_notice("MIRRORED", sub)
                    self.after(80, self._raise_overlay)
                    self.after(300, self._raise_overlay)
                acquired = self._media_url(body) != prev_before and (
                    op
                    in (
                        "rs",
                        "rm",
                        "po",
                        "pa",
                        "sl",
                        "sm",
                        "au",
                        "am",
                        "ti",
                        "uo",
                        "ld",
                        "rj",
                    )
                    or (op == "vr" and str(arg_s or "") == "k")
                )
                self._apply_state(body, acquired=acquired)
                self._on_log(f"{self._mode} {op} #{self._char_id}: {body.get('detail') or 'ok'}")
            else:
                self._stop_progress(done=False)
                err = body.get("error") or body.get("detail") or "failed"
                self._set_status(str(err))
                if body.get("buttons"):
                    self._apply_state(body, acquired=False)

        def on_err(exc: BaseException) -> None:
            if gen != self._busy_gen:
                return
            self._busy = False
            self._stop_progress(done=False)
            self._set_status(f"Failed: {exc}")

        self._tap(self._char_id, op, arg_s, on_ok, on_err, self._mode)

    def repeat_last_craft(self) -> None:
        spec = find_omni_repeat_spec(
            self._state.get("buttons") or [],
            fallback=self._last_repeat,
        )
        if spec is None:
            self._set_status("No last craft to repeat")
            return
        text = _pretty_label(
            str(spec.get("text") or "Repeat"),
            str(spec.get("op") or ""),
            hidden=bool(self._state.get("hidden")),
            done=bool(self._state.get("done")),
        )
        self._click(str(spec.get("op") or ""), spec.get("arg"), text)

    def _current_flavour(self) -> str:
        cached = str(self._state.get("flavour") or "").strip()
        if cached:
            return cached
        if self._get_flavour is not None:
            try:
                return str(self._get_flavour(self._char_id) or "").strip()
            except Exception:
                logger.debug("omni flavour lookup failed", exc_info=True)
        return ""

    def _reload_state(self, *, status: str = "") -> None:
        def on_ok(body: dict) -> None:
            if body.get("op") == "omni_state_ok":
                self._apply_state(body, acquired=False)
            if status:
                self._set_status(status)

        def on_err(exc: BaseException) -> None:
            if status:
                self._set_status(f"{status} (refresh: {exc})")
            else:
                self._set_status(f"Refresh failed: {exc}")

        self._fetch_state(self._char_id, on_ok, on_err, self._mode)

    def _edit_flavour(self) -> None:
        from link_bridge.text_edit_dialog import ask_text

        text = ask_text(
            self,
            title=f"Flavour #{self._char_id}",
            initial=self._current_flavour(),
            prompt="Public flavour text (saved quietly).",
            geometry=self._get_text_geo(),
            on_geometry=self._set_text_geo,
            allow_empty=True,
        )
        if text is None:
            return
        stripped = str(text).strip()
        craft = "rfl" if not stripped else f"flset:{stripped}"
        label = "Remove flavour" if not stripped else "Set flavour"
        if self._dm_craft is not None:
            self._save_flavour_quiet(craft, label, stripped)
            return
        if not stripped:
            self._click("rfl", None, label)
        else:
            self._click("flset", stripped, label)

    def _save_flavour_quiet(self, craft: str, label: str, flavour_text: str) -> None:
        if self._busy or self._dm_craft is None:
            return
        self._busy = True
        self._busy_gen += 1
        gen = self._busy_gen
        self._set_status(f"{label}…", busy=True)

        def on_ok(body: dict) -> None:
            if gen != self._busy_gen:
                return
            self._busy = False
            if body.get("op") != "dm_craft_ok":
                err = body.get("error") or body.get("detail") or "failed"
                self._set_status(str(err))
                return
            detail = str(body.get("detail") or "flavour saved").strip()
            self._state["flavour"] = flavour_text
            if self._on_silent_craft is not None:
                try:
                    self._on_silent_craft(self._char_id, craft)
                except Exception:
                    logger.debug("omni silent craft notify failed", exc_info=True)
            self._on_log(f"omni {craft} #{self._char_id}: {detail}")
            self._reload_state(status=detail)

        def on_err(exc: BaseException) -> None:
            if gen != self._busy_gen:
                return
            self._busy = False
            self._set_status(f"Failed: {exc}")

        self._dm_craft(self._char_id, craft, on_ok, on_err)

    def reload(self) -> None:
        self._busy = False
        self._busy_gen += 1
        self._set_status("Loading…")

        def on_ok(body: dict) -> None:
            if body.get("op") == "omni_state_ok":
                self._apply_state(body, acquired=False)
            else:
                self._set_status(str(body.get("error") or "failed"))

        def on_err(exc: BaseException) -> None:
            self._set_status(f"Load failed: {exc}")

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
        get_window_state: Callable[[], str] | None = None,
        set_window_state: Callable[[str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_done_changed: Callable[[int, bool], None] | None = None,
        fetch_undone: Callable[..., None] | None = None,
        dm_preview: Callable[[int, OkCb, ErrCb], None] | None = None,
        on_media_changed: Callable[[int, dict[str, Any]], None] | None = None,
        repeat_key_get: Callable[[], str] | None = None,
        repeat_key_set: Callable[[str], None] | None = None,
        dm_craft: DmCraftFn | None = None,
        get_flavour: FlavourGetFn | None = None,
        on_silent_craft: SilentCraftFn | None = None,
        on_balance: Callable[[dict[str, Any]], None] | None = None,
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
        self._get_window_state = get_window_state or (lambda: "zoomed")
        self._set_window_state = set_window_state
        self._want_zoomed = (
            str(self._get_window_state() or "zoomed").strip().lower() == "zoomed"
        )
        self._on_log = on_log or (lambda _m: None)
        self._on_done_changed = on_done_changed
        self._fetch_undone = fetch_undone
        self._dm_preview = dm_preview
        self._on_media_changed = on_media_changed
        self._repeat_key_get = repeat_key_get or (lambda: "space")
        self._repeat_key_set = repeat_key_set or (lambda _k: None)
        self._dm_craft = dm_craft
        self._get_flavour = get_flavour
        self._on_silent_craft = on_silent_craft
        self._on_balance_external = on_balance
        self._capturing_repeat = False
        self._omni_keypress = self._on_omni_key
        self._tabs: dict[tuple[int, str], tuple[ttk.Frame, OmniPanel]] = {}
        self._tab_order: list[tuple[int, str]] = []
        self._active_key: tuple[int, str] | None = None
        self._wip_busy = False
        self._geo_save_after: str | None = None
        self._geo_ready = False

        top = ttk.Frame(self, padding=(4, 0), style="Omni.TFrame")
        top.pack(fill=tk.BOTH, expand=True)
        head = ttk.Frame(top, style="Omni.TFrame")
        head.pack(fill=tk.X)
        self._beep_var = tk.BooleanVar(value=bool(self._beep_get()))
        self._beep_cb = ttk.Checkbutton(
            head,
            text="Beep",
            style="Omni.TCheckbutton",
            variable=self._beep_var,
            command=lambda: self._beep_set(bool(self._beep_var.get())),
            takefocus=0,
        )
        self._beep_cb.pack(side=tk.LEFT, padx=(0, 8))
        self._beep_cb.bind("<KeyPress-space>", lambda _e: "break")
        self._full_var = tk.BooleanVar(value=bool(self._full_image_get()))
        ttk.Checkbutton(
            head,
            text="Full original",
            style="Omni.TCheckbutton",
            variable=self._full_var,
            command=self._on_full_image_toggle,
        ).pack(side=tk.LEFT, padx=(0, 8))
        self._repeat_btn = ttk.Button(
            head,
            text=f"Repeat: {repeat_key_label(self._repeat_key_get())}",
            command=self._start_capture_repeat_key,
            width=16,
            style="Omni.TButton",
        )
        self._repeat_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._wip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            head,
            text="WIP",
            style="Omni.TCheckbutton",
            variable=self._wip_var,
        ).pack(side=tk.LEFT, padx=(0, 8))
        self._tab_chips = ttk.Frame(head, style="Omni.TFrame")
        self._tab_chips.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8))
        self._host_status_var = tk.StringVar(value="")
        ttk.Label(
            head,
            textvariable=self._host_status_var,
            style="Omni.Muted.TLabel",
        ).pack(side=tk.RIGHT, padx=(8, 8))
        from link_bridge.balance_chip import BalanceChip

        self._balance_chip = BalanceChip(head, fg=pal["fg"], bg=pal["bg"])
        self._balance_chip.pack(side=tk.RIGHT, padx=(0, 4))

        self._body = ttk.Frame(top, style="Omni.TFrame")
        self._body.pack(fill=tk.BOTH, expand=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._want_zoomed:
            try:
                self.state("zoomed")
            except Exception:
                self._restore_or_center()
        else:
            self._restore_or_center()
        self.after_idle(self._restore_window_state)
        self.after(100, self._restore_window_state)
        self.bind("<Configure>", self._on_configure)
        self.bind("<F11>", lambda _e: self.toggle_fullscreen())
        self.after(250, self._arm_geo_save)
        self._bind_omni_keys()

    def _bind_omni_keys(self) -> None:
        try:
            root = self.master.winfo_toplevel()
        except Exception:
            root = self
        if getattr(root, "_omni_keys_bound", False):
            return
        root.bind_all("<KeyPress>", _dispatch_omni_keypress, add="+")
        try:
            root._omni_keys_bound = True  # type: ignore[attr-defined]
        except Exception:
            pass

    def _sync_repeat_label(self) -> None:
        try:
            if self._capturing_repeat:
                self._repeat_btn.configure(text="Press a key…")
            else:
                self._repeat_btn.configure(
                    text=f"Repeat: {repeat_key_label(self._repeat_key_get())}"
                )
        except Exception:
            pass

    def _start_capture_repeat_key(self) -> None:
        self._capturing_repeat = True
        self._sync_repeat_label()

    def _current_panel(self) -> OmniPanel | None:
        if self._active_key is None:
            return None
        pair = self._tabs.get(self._active_key)
        return pair[1] if pair else None

    def toggle_fullscreen(self) -> None:
        try:
            is_fs = bool(self.attributes("-fullscreen"))
        except Exception:
            is_fs = False
        try:
            if is_fs:
                self.attributes("-fullscreen", False)
                self.state("zoomed")
                self._want_zoomed = True
                if self._set_window_state is not None:
                    self._set_window_state("zoomed")
            else:
                cur = str(self.state() or "normal")
                if cur == "zoomed":
                    self.attributes("-fullscreen", True)
                else:
                    self.state("zoomed")
                    self._want_zoomed = True
                    if self._set_window_state is not None:
                        self._set_window_state("zoomed")
        except Exception:
            pass

    def _on_omni_key(self, event: Any) -> str | None:
        widget = getattr(event, "widget", None)
        if widget is not None and not isinstance(widget, str):
            try:
                cls = widget.winfo_class()
            except Exception:
                cls = ""
            if cls in ("Entry", "TEntry", "Text", "TCombobox"):
                try:
                    if str(widget.cget("state") or "").lower() != "disabled":
                        return None
                except Exception:
                    return None
        from link_bridge.window_keys import (
            _control_down,
            _widget_accepts_typing,
            is_physical_q_key,
            is_physical_w_key,
        )

        keysym = str(getattr(event, "keysym", "") or "")
        if _control_down(event) and is_physical_w_key(event):
            if not _widget_accepts_typing(widget):
                self._close_current()
                return "break"
        if keysym.lower() == "f11":
            self.toggle_fullscreen()
            return "break"
        if is_physical_q_key(event) and not self._capturing_repeat:
            self._on_close()
            return "break"
        if keysym.lower() == "escape":
            try:
                if bool(self.attributes("-fullscreen")):
                    self.attributes("-fullscreen", False)
                    self.state("zoomed")
                    return "break"
            except Exception:
                pass
        if self._capturing_repeat:
            if keysym.lower() in (
                "shift_l",
                "shift_r",
                "control_l",
                "control_r",
                "alt_l",
                "alt_r",
                "caps_lock",
                "num_lock",
                "scroll_lock",
            ):
                return "break"
            if keysym.lower() == "escape":
                self._capturing_repeat = False
                self._sync_repeat_label()
                return "break"
            from link_bridge.config import _normalize_omni_repeat_key

            key = _normalize_omni_repeat_key(keysym)
            self._repeat_key_set(key)
            self._capturing_repeat = False
            self._sync_repeat_label()
            return "break"
        if not event_matches_repeat_key(event, self._repeat_key_get()):
            return None
        panel = self._current_panel()
        if panel is not None:
            panel.repeat_last_craft()
        return "break"

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

    def _restore_window_state(self) -> None:
        if not self._want_zoomed:
            return
        try:
            if str(self.state()) != "zoomed":
                self.state("zoomed")
        except Exception:
            pass

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
        try:
            state = str(self.state() or "normal")
        except Exception:
            state = "normal"
        norm_state = "zoomed" if state == "zoomed" else "normal"
        self._want_zoomed = norm_state == "zoomed"
        if self._set_window_state is not None:
            self._set_window_state(norm_state)
        if state != "zoomed" and self._set_window_geo is not None:
            try:
                geo = self.geometry()
            except Exception:
                geo = ""
            if geo:
                self._set_window_geo(geo)

    def _on_close(self) -> None:
        self._persist_geometry()
        self.destroy()

    def set_balance_from_body(self, body: dict[str, Any]) -> None:
        if hasattr(self, "_balance_chip"):
            self._balance_chip.set_from_body(body)

    def _notify_balance(self, body: dict[str, Any]) -> None:
        self.set_balance_from_body(body)
        if self._on_balance_external is not None:
            try:
                self._on_balance_external(body)
            except Exception:
                logger.debug("omni balance forward failed", exc_info=True)

    def _on_full_image_toggle(self) -> None:
        enabled = bool(self._full_var.get())
        self._full_image_set(enabled)
        for _fr, panel in list(self._tabs.values()):
            try:
                panel.refresh_display()
            except Exception:
                logger.debug("omni full-image refresh failed", exc_info=True)

    def _tab_label(self, key: tuple[int, str]) -> str:
        cid, mode = key
        return f"R #{cid}" if mode == "refine" else f"#{cid}"

    def _current_tab_frame(self) -> ttk.Frame | None:
        if self._active_key is None:
            return None
        pair = self._tabs.get(self._active_key)
        return pair[0] if pair else None

    def _show_tab(self, key: tuple[int, str]) -> None:
        pair = self._tabs.get(key)
        if pair is None:
            return
        fr, _panel = pair
        for k, (f, _p) in self._tabs.items():
            if k == key:
                f.pack(fill=tk.BOTH, expand=True)
            else:
                try:
                    f.pack_forget()
                except Exception:
                    pass
        self._active_key = key
        self._sync_tab_chips()
        pair = self._tabs.get(key)
        if pair is not None:
            try:
                pair[1].publish_status_to_host()
            except Exception:
                pass

    def _sync_tab_chips(self) -> None:
        if not hasattr(self, "_tab_chips"):
            return
        for child in list(self._tab_chips.winfo_children()):
            child.destroy()
        cur = self._current_tab_frame()
        for key, (fr, _panel) in self._tabs.items():
            label = self._tab_label(key)
            selected = cur is not None and str(fr) == str(cur)
            chip = ttk.Frame(self._tab_chips, style="Omni.TFrame")
            chip.pack(side=tk.LEFT, padx=(0, 4))
            tab_btn = ttk.Button(
                chip,
                text=label,
                width=max(5, len(label) + 1),
                style="Omni.TabActive.TButton" if selected else "Omni.TButton",
                command=lambda f=fr: self._select_tab(f),
            )
            tab_btn.pack(side=tk.LEFT)
            close_btn = ttk.Button(
                chip,
                text="×",
                width=2,
                style="Omni.TButton",
                command=lambda k=key: self._close_tab(k),
            )

            def _show_close(_event=None, btn=close_btn) -> None:
                if not btn.winfo_ismapped():
                    btn.pack(side=tk.LEFT)

            def _hide_close(_event=None, wrap=chip, btn=close_btn) -> None:
                def _maybe() -> None:
                    try:
                        px, py = wrap.winfo_pointerxy()
                        target = wrap.winfo_containing(px, py)
                    except Exception:
                        target = None
                    w = target
                    while w is not None:
                        if w is wrap:
                            return
                        try:
                            w = w.master
                        except Exception:
                            break
                    try:
                        btn.pack_forget()
                    except Exception:
                        pass

                wrap.after_idle(_maybe)

            chip.bind("<Enter>", _show_close, add="+")
            chip.bind("<Leave>", _hide_close, add="+")
            tab_btn.bind("<Enter>", _show_close, add="+")
            close_btn.bind("<Enter>", _show_close, add="+")
            close_btn.bind("<Leave>", _hide_close, add="+")

    def _select_tab(self, fr: ttk.Frame) -> None:
        for key, (f, _panel) in self._tabs.items():
            if f is fr:
                self._show_tab(key)
                return

    def open_card(self, char_id: int, *, mode: str = "omni") -> None:
        mode = "refine" if mode == "refine" else "omni"
        key = (int(char_id), mode)
        existing = self._tabs.get(key)
        if existing is not None:
            tab, panel = existing
            self._show_tab(key)
            if mode == "refine":
                omni_pair = self._tabs.get((int(char_id), "omni"))
                if omni_pair is not None:
                    panel._seed_panel = omni_pair[1]
                    panel._bootstrap_from_sibling()
                else:
                    panel.reload()
            self.lift()
            self.focus_force()
            return

        tab = ttk.Frame(self._body, style="Omni.TFrame")
        seed_panel = None
        if mode == "refine":
            omni_pair = self._tabs.get((int(char_id), "omni"))
            if omni_pair is not None:
                seed_panel = omni_pair[1]
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
            on_title=lambda _t, fr=tab: self._sync_tab_chips(),
            on_open_refine=lambda cid: self.open_card(int(cid), mode="refine"),
            wip_get=lambda: bool(self._wip_var.get()),
            on_wip_next=self._wip_advance,
            dm_preview=self._dm_preview,
            on_media_changed=self._on_media_changed,
            on_plan_changed=self._refresh_plan_rails,
            dm_craft=self._dm_craft,
            get_flavour=self._get_flavour,
            on_silent_craft=self._on_silent_craft,
            on_balance=self._notify_balance,
            seed_panel=seed_panel,
            on_host_status=lambda s: self._host_status_var.set(s),
        )
        panel.pack(fill=tk.BOTH, expand=True)
        self._tabs[key] = (tab, panel)
        if key not in self._tab_order:
            self._tab_order.append(key)
        self._show_tab(key)
        self.lift()
        self.focus_force()

    def _refresh_plan_rails(self, char_id: int) -> None:
        cid = int(char_id)
        for (tab_cid, _mode), (_fr, panel) in list(self._tabs.items()):
            if int(tab_cid) != cid:
                continue
            try:
                panel.refresh_plan_rail()
            except Exception:
                logger.debug("omni plan rail refresh failed", exc_info=True)

    def first_omni_panel(self) -> OmniPanel | None:
        """First-opened omni tab — the card the craft button targets."""
        for key in self._tab_order:
            if key[1] != "omni" or key not in self._tabs:
                continue
            return self._tabs[key][1]
        return None

    def ingest_browser_craft(self, url: str, tags: dict[str, Any] | None) -> dict[str, Any]:
        panel = self.first_omni_panel()
        if panel is None:
            return {"ok": False, "error": "Open OmniCraft first"}
        return panel.ingest_browser_craft(url, tags)

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
                fr.pack_forget()
            except Exception:
                pass
            try:
                fr.destroy()
            except Exception:
                pass
            del self._tabs[key]
            if key in self._tab_order:
                self._tab_order.remove(key)
            if self._active_key == key:
                self._active_key = None
        self.open_card(int(new_cid), mode="omni")
        self._on_log(f"WIP → #{new_cid}")

    def _close_tab(self, key: tuple[int, str]) -> None:
        pair = self._tabs.get(key)
        if pair is None:
            return
        fr, _panel = pair
        try:
            fr.pack_forget()
        except Exception:
            pass
        try:
            fr.destroy()
        except Exception:
            pass
        del self._tabs[key]
        if key in self._tab_order:
            self._tab_order.remove(key)
        if self._active_key == key:
            self._active_key = None
        if self._tabs:
            nxt = next((k for k in self._tab_order if k in self._tabs), None)
            if nxt is None:
                nxt = next(iter(self._tabs))
            self._show_tab(nxt)
        else:
            self._sync_tab_chips()
            self._on_close()

    def _close_current(self) -> None:
        if self._active_key is None:
            return
        self._close_tab(self._active_key)


# Back-compat alias used by older call sites.
OmniWindow = OmniHost
