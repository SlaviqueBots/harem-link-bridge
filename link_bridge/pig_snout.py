"""Colored pig-head glyph for money UI (Tk greys Unicode ????)."""

from __future__ import annotations

from typing import Any

import tkinter as tk

_CACHE: dict[int, Any] = {}

# Soft pinks matching Telegram-style pig emoji.
_FACE = (255, 170, 196, 255)
_FACE_EDGE = (235, 120, 160, 255)
_EAR = (255, 150, 180, 255)
_EAR_IN = (255, 120, 160, 255)
_SNOUT = (255, 140, 175, 255)
_NOSTRIL = (140, 55, 90, 255)
_EYE = (60, 35, 50, 255)


def pig_char() -> str:
    """Fallback text when a PhotoImage cannot be built."""
    return "????"


def pig_photo(master: tk.Misc, *, size: int = 18) -> Any:
    """Return a cached full-color pig-head PhotoImage."""
    key = int(size)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    try:
        from PIL import Image, ImageDraw, ImageTk
    except Exception:
        return None
    s = max(12, min(48, key))
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    # Ears (behind face)
    ear_w = max(3, s // 4)
    ear_h = max(4, int(s * 0.42))
    d.ellipse((1, 1, 1 + ear_w, 1 + ear_h), fill=_EAR, outline=_FACE_EDGE)
    d.ellipse((s - 2 - ear_w, 1, s - 2, 1 + ear_h), fill=_EAR, outline=_FACE_EDGE)
    # Inner ear
    iw = max(1, ear_w // 2)
    ih = max(2, ear_h // 2)
    d.ellipse((2, 3, 2 + iw, 3 + ih), fill=_EAR_IN)
    d.ellipse((s - 3 - iw, 3, s - 3, 3 + ih), fill=_EAR_IN)

    # Head
    pad = max(1, s // 10)
    d.ellipse((pad, pad + 1, s - 1 - pad, s - 1), fill=_FACE, outline=_FACE_EDGE)

    # Eyes
    er = max(1, s // 14)
    ey = int(s * 0.42)
    ex_gap = max(3, s // 5)
    cx = s // 2
    d.ellipse((cx - ex_gap - er, ey - er, cx - ex_gap + er, ey + er), fill=_EYE)
    d.ellipse((cx + ex_gap - er, ey - er, cx + ex_gap + er, ey + er), fill=_EYE)

    # Snout oval
    sw = max(5, int(s * 0.42))
    sh = max(4, int(s * 0.28))
    sx0 = cx - sw // 2
    sy0 = int(s * 0.55)
    d.ellipse((sx0, sy0, sx0 + sw, sy0 + sh), fill=_SNOUT, outline=_FACE_EDGE)

    # Nostrils
    nr = max(1, s // 12)
    ny = sy0 + sh // 2
    ng = max(2, sw // 4)
    d.ellipse((cx - ng - nr, ny - nr, cx - ng + nr, ny + nr), fill=_NOSTRIL)
    d.ellipse((cx + ng - nr, ny - nr, cx + ng + nr, ny + nr), fill=_NOSTRIL)

    photo = ImageTk.PhotoImage(im, master=master)
    _CACHE[key] = photo
    return photo


# Back-compat alias used by older call sites.
pig_snout_photo = pig_photo


def pack_pig_label(
    parent: tk.Misc,
    text: str,
    *,
    size: int = 16,
    **pack_kw: Any,
) -> tk.Frame:
    """Label with colored pig icon + optional text."""
    fr = tk.Frame(parent)
    photo = pig_photo(parent, size=size)
    if photo is not None:
        lbl = tk.Label(fr, image=photo, bd=0)
        lbl.image = photo  # type: ignore[attr-defined]
        lbl.pack(side=tk.LEFT)
        if text:
            tk.Label(fr, text=f" {text}", bd=0).pack(side=tk.LEFT)
    else:
        label = f"{pig_char()} {text}".rstrip() if text else pig_char()
        tk.Label(fr, text=label, font=("Segoe UI Emoji", 11), bd=0).pack(side=tk.LEFT)
    fr.pack(**pack_kw)
    return fr
