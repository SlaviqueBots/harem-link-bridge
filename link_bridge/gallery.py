"""Justified photo gallery — tight rows that fill width (Telegram-like)."""

from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable
from typing import Any
from tkinter import ttk

from link_bridge.thumb_grid import (
    decode_thumb_sized,
    peek_aspect,
    release_photos,
    schedule_aspect,
    schedule_thumb_fetch,
)

logger = logging.getLogger(__name__)

GAP = 3
MIN_ROW_H = 110
MAX_ROW_H = 280
# Pack/fill around this height so rows hold enough tiles to span the window.
TARGET_ROW_H = 168
# Below this, justify_layout packs a single left column (startup flash).
MIN_READY_WIDTH = 240


def ready_layout_width(canvas_w: int, parent_w: int = 0) -> int | None:
    """Width to lay out, or None if the widget is not mapped yet."""
    try:
        cw = int(canvas_w or 0)
    except Exception:
        cw = 0
    try:
        pw = int(parent_w or 0)
    except Exception:
        pw = 0
    width = max(cw, pw - 28 if pw > 28 else 0)
    if width < MIN_READY_WIDTH:
        return None
    return width


def _clamp_aspect(raw: float) -> float:
    a = float(raw) if raw and raw > 0.05 else 1.0
    return max(0.2, min(a, 4.0))


def dense_order(
    aspects: list[float],
    container_width: int,
    *,
    target_h: int = TARGET_ROW_H,
    gap: int = GAP,
    max_h: int = MAX_ROW_H,
    min_h: int = MIN_ROW_H,
) -> list[int]:
    """Reorder indices so sequential justified rows waste less width on the right.

    Greedy shelf packing at the layout target height: each row repeatedly picks
    the unused image that leaves the least leftover space without overflowing.
    """
    width = max(80, int(container_width))
    target = max(min_h, min(max_h, int(target_h)))
    if not aspects:
        return []
    clamped = [_clamp_aspect(a) for a in aspects]
    remaining = set(range(len(clamped)))
    order: list[int] = []

    while remaining:
        row: list[int] = []
        used = 0.0
        while remaining:
            best_i: int | None = None
            best_key: tuple[float, float, int] | None = None
            for idx in remaining:
                aw = clamped[idx] * target
                need = (gap if row else 0) + aw
                if used + need > width + 0.5:
                    continue
                new_used = used + need
                waste = width - new_used
                # Prefer fuller rows; then prefer a tile that eats more leftover.
                key = (waste, -aw, idx)
                if best_key is None or key < best_key:
                    best_key = key
                    best_i = idx
            if best_i is None:
                break
            used += (gap if row else 0) + clamped[best_i] * target
            row.append(best_i)
            remaining.remove(best_i)
        if not row:
            narrowest = min(remaining, key=lambda i: (clamped[i], i))
            row = [narrowest]
            remaining.remove(narrowest)
        order.extend(row)
    return order


def justify_layout(
    aspects: list[float],
    container_width: int,
    *,
    target_h: int = TARGET_ROW_H,
    gap: int = GAP,
    max_h: int = MAX_ROW_H,
    min_h: int = MIN_ROW_H,
    stretch_last: bool = False,
    reorder: bool = True,
) -> tuple[list[tuple[int, int, int, int]], int, list[int]]:
    """Pack images into full-width rows.

    Non-last rows always fill the container width by scaling shared row height
    (aspect preserved — this is not the old per-tile stretch bug). Only a short
    last row may leave blank space on the right.
    """
    width = max(80, int(container_width))
    target = max(min_h, min(max_h, int(target_h)))
    if not aspects:
        return [], 0, []

    order = (
        dense_order(aspects, width, target_h=target, gap=gap, max_h=max_h, min_h=min_h)
        if reorder
        else list(range(len(aspects)))
    )
    packed = [_clamp_aspect(aspects[i]) for i in order]

    rows: list[list[float]] = []
    cur: list[float] = []
    for a in packed:
        trial = cur + [a]
        trial_w = sum(x * target for x in trial) + gap * (len(trial) - 1)
        if cur and trial_w > width:
            rows.append(cur)
            cur = [a]
        else:
            cur = trial
    if cur:
        rows.append(cur)

    boxes: list[tuple[int, int, int, int]] = []
    y = 0
    for ri, row in enumerate(rows):
        n = len(row)
        gaps = gap * max(0, n - 1)
        usable = max(1, width - gaps)
        is_last = ri == len(rows) - 1
        sparse_last = is_last and not stretch_last and n < 3
        h_ideal = usable / max(sum(row), 0.01)
        if sparse_last:
            # Short trailing row: keep natural height, allow right blank.
            h = float(target)
            fill = False
        else:
            # Always fill width by shared row height (aspects stay correct).
            # Soft max only for a lone ultra-wide gap that packing couldn't close.
            soft_max = float(max_h) * 1.25
            if h_ideal > soft_max and n <= 2:
                h = soft_max
                fill = False
            else:
                h = float(max(min_h, h_ideal))
                fill = True
        hh = max(1, int(round(h)))
        widths = [max(1, int(round(a * h))) for a in row]
        if fill:
            drift = usable - sum(widths)
            i = 0
            while drift != 0 and widths:
                step = 1 if drift > 0 else -1
                idx = i % len(widths)
                if widths[idx] + step >= 1:
                    widths[idx] += step
                    drift -= step
                i += 1
                if i > len(widths) * (abs(drift) + 2):
                    break
        x = 0
        for w in widths:
            boxes.append((x, y, w, hh))
            x += w + gap
        y += hh + gap
    total_h = max(0, y - gap) if boxes else 0
    return boxes, total_h, order


def target_row_height(
    view_h: int, view_w: int, *, scale: float = 1.0
) -> int:
    """Stable mid-size rows so packing can fill wide monitors."""
    del view_w
    scale = max(0.5, min(2.0, float(scale or 1.0)))
    base = int(round(TARGET_ROW_H * scale))
    base = max(MIN_ROW_H, min(int(MAX_ROW_H * 1.15), base))
    if view_h < 120:
        return base
    guess = base
    if view_h >= 1100:
        guess = min(int(MAX_ROW_H * 1.15), base + int(24 * scale))
    elif view_h < 500:
        guess = max(MIN_ROW_H, base - 16)
    return int(guess)


BindThumbFn = Callable[[tk.Label, int, str], None]

# Shared wheel handler — dual Done/Undone panes must not steal bind_all from each other.
_wheel_target: "JustifiedGallery | PairGallery | None" = None

# Smooth wheel: pixels per notch at scroll_speed=1.0, then eased toward rest.
_WHEEL_PX_PER_NOTCH = 56
_SMOOTH_FRAME_MS = 12
_SMOOTH_EASE = 0.32  # fraction of remaining distance per frame


def _gallery_wheel(event) -> None:
    g = _wheel_target
    if g is None or getattr(g, "_canvas", None) is None:
        return
    try:
        if not g._canvas.winfo_exists():
            return
    except Exception:
        return
    delta = getattr(event, "delta", 0) or 0
    if not delta:
        return
    # Windows: ±120 per notch. Keep sign: positive delta = scroll up = negative y.
    notches = float(delta) / 120.0
    speed = max(0.25, min(6.0, float(getattr(g, "_scroll_speed", 3.0) or 3.0)))
    px = -notches * _WHEEL_PX_PER_NOTCH * speed
    g._queue_smooth_scroll(px)


# Debounce while thumbs trickle in — each layout can re-decode many tiles.
_LAYOUT_DEBOUNCE_MS = 160
_DECODE_BUDGET = 48  # max PhotoImage rebuilds per layout tick (keeps UI alive)


class JustifiedGallery:
    """Scrollable justified thumb strip hosted inside ``parent``."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        photos: list[Any],
        bind_thumb: BindThumbFn,
        gen_fn: Callable[[], int],
        preview_scale: float = 1.0,
        scroll_speed: float = 3.0,
        show_scrollbar: bool = False,
    ) -> None:
        self._parent = parent
        self._photos = photos
        self._bind_thumb = bind_thumb
        self._gen_fn = gen_fn
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.0)))
        self._scroll_speed = max(0.25, min(6.0, float(scroll_speed or 3.0)))
        self._show_scrollbar = bool(show_scrollbar)
        self._entries: list[dict[str, Any]] = []
        self._canvas: tk.Canvas | None = None
        self._inner: tk.Frame | None = None
        self._sb: ttk.Scrollbar | None = None
        self._win = None
        self._layout_after: str | None = None
        self._wheel_bound = False
        self._in_layout = False
        self._last_sig: tuple[Any, ...] | None = None
        self._last_view_w = 0
        self._decode_queue: list[int] = []
        self._dragging_scroll = False
        self._layout_after_drag = False
        self._scroll_gen = 0
        self._smooth_remaining = 0.0
        self._smooth_after: str | None = None
        self._stamp = 0
        self._layout_waits = 0

    def set_preview_scale(self, scale: float) -> None:
        self._preview_scale = max(0.5, min(2.0, float(scale or 1.0)))
        self._last_sig = None
        self._schedule_layout(immediate=True)

    def set_scroll_speed(self, speed: float) -> None:
        self._scroll_speed = max(0.25, min(6.0, float(speed or 3.0)))
        # Pixel units so smooth easing can move by 1px steps.
        if self._canvas is not None:
            self._canvas.configure(yscrollincrement=1)

    def _surface(self) -> dict[str, str]:
        from link_bridge.theme import surface_for

        return surface_for(self._parent)

    def _gap(self) -> int:
        from link_bridge.theme import gallery_gap

        return gallery_gap(self._parent)

    def _apply_surface(self) -> None:
        c = self._surface()
        bg = c.get("canvas") or c.get("bg") or "#1e1f22"
        try:
            if self._canvas is not None:
                self._canvas.configure(
                    bg=bg, highlightthickness=0, highlightbackground=bg, bd=0
                )
            if self._inner is not None:
                self._inner.configure(bg=bg, highlightthickness=0, bd=0)
        except Exception:
            pass

    def _queue_smooth_scroll(self, px: float) -> None:
        if self._canvas is None or abs(px) < 0.01:
            return
        self._smooth_remaining += float(px)
        if self._smooth_after is None:
            self._tick_smooth_scroll()

    def _tick_smooth_scroll(self) -> None:
        self._smooth_after = None
        if self._canvas is None:
            self._smooth_remaining = 0.0
            return
        rem = self._smooth_remaining
        if abs(rem) < 0.8:
            if abs(rem) >= 0.2:
                self._canvas.yview_scroll(1 if rem > 0 else -1, "units")
            self._smooth_remaining = 0.0
            return
        step = rem * _SMOOTH_EASE
        if abs(step) < 1.0:
            step = 1.0 if rem > 0 else -1.0
        moved = int(round(step))
        if moved == 0:
            self._smooth_remaining = 0.0
            return
        self._smooth_remaining -= moved
        self._canvas.yview_scroll(moved, "units")
        self._smooth_after = self._canvas.after(_SMOOTH_FRAME_MS, self._tick_smooth_scroll)

    def destroy(self) -> None:
        global _wheel_target
        self._cancel_layout()
        if self._smooth_after is not None and self._canvas is not None:
            try:
                self._canvas.after_cancel(self._smooth_after)
            except Exception:
                pass
        self._smooth_after = None
        self._smooth_remaining = 0.0
        if _wheel_target is self:
            _wheel_target = None
        self._wheel_bound = False
        self._entries.clear()
        self._last_sig = None
        self._decode_queue.clear()
        if self._canvas is not None:
            try:
                self._canvas.destroy()
            except Exception:
                pass
        if self._sb is not None:
            try:
                self._sb.destroy()
            except Exception:
                pass
        self._canvas = None
        self._inner = None
        self._sb = None
        self._win = None

    def _cancel_layout(self) -> None:
        if self._layout_after is not None and self._canvas is not None:
            try:
                self._canvas.after_cancel(self._layout_after)
            except Exception:
                pass
        self._layout_after = None

    def _on_scrollbar(self, *args) -> None:
        """Drag-scroll must not retrigger justified layout (that jumbles tiles)."""
        self._scroll_gen += 1
        gen = self._scroll_gen
        self._dragging_scroll = True
        self._smooth_remaining = 0.0
        if self._canvas is not None:
            self._canvas.yview(*args)

            def _clear() -> None:
                if gen != self._scroll_gen:
                    return
                self._dragging_scroll = False
                if self._layout_after_drag:
                    self._layout_after_drag = False
                    self._schedule_layout()

            self._canvas.after(180, _clear)

    def _ensure_chrome(self) -> tk.Frame:
        if self._canvas is not None and self._inner is not None:
            return self._inner
        parent = self._parent
        for child in list(parent.winfo_children()):
            child.destroy()
        self._canvas = tk.Canvas(parent, highlightthickness=0, bd=0)
        self._canvas.configure(yscrollincrement=1)
        self._apply_surface()
        # Wheel-only: ttk.Scrollbar on Windows draws the classic light trough.
        if self._show_scrollbar:
            self._sb = ttk.Scrollbar(parent, orient=tk.VERTICAL)
            self._sb.configure(command=self._on_scrollbar)
            self._canvas.configure(yscrollcommand=self._sb.set)
            self._sb.pack(side=tk.RIGHT, fill=tk.Y)
            self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        else:
            self._sb = None
            self._canvas.pack(fill=tk.BOTH, expand=True)
        self._inner = tk.Frame(self._canvas, bd=0, highlightthickness=0)
        self._apply_surface()
        self._win = self._canvas.create_window((0, 0), window=self._inner, anchor=tk.NW)

        def _on_inner(_event=None) -> None:
            if self._in_layout or self._dragging_scroll or self._canvas is None:
                return
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))

        def _on_canvas(event) -> None:
            if self._in_layout or self._canvas is None or self._win is None:
                return
            width = int(getattr(event, "width", 0) or 0)
            if width < 80:
                return
            self._canvas.itemconfigure(self._win, width=width)
            if self._dragging_scroll:
                return
            # Ignore tiny/noise width changes (scrollbar flicker).
            if abs(width - self._last_view_w) < 8 and self._last_sig:
                return
            self._schedule_layout()

        self._inner.bind("<Configure>", _on_inner)
        self._canvas.bind("<Configure>", _on_canvas)
        self._canvas.bind("<Enter>", self._claim_wheel)
        self._inner.bind("<Enter>", self._claim_wheel)

        if not self._wheel_bound:
            self._canvas.bind_all("<MouseWheel>", _gallery_wheel)
            self._wheel_bound = True
        return self._inner

    def _claim_wheel(self, _event=None) -> None:
        global _wheel_target
        _wheel_target = self

    def render(self, items: list[dict[str, Any]]) -> None:
        self.destroy()
        inner = self._ensure_chrome()
        # Prefer the visible gallery under the pointer; do not steal wheel from
        # another pane just because this one finished rendering.
        if _wheel_target is None:
            self._claim_wheel()
        self._stamp += 1
        gen = self._stamp
        self._entries = []
        self._last_sig = None
        for item in items:
            cid = int(item.get("id") or 0)
            post_url = (item.get("post_url") or "").strip()
            url = (item.get("preview_url") or "").strip()
            lbl = tk.Label(
                inner,
                text="…",
                relief=tk.FLAT,
                cursor="hand2",
                bd=0,
                highlightthickness=0,
                bg=self._surface().get("canvas", "#1e1f22"),
                fg=self._surface().get("muted", "#b5bac1"),
            )
            self._bind_thumb(lbl, cid, post_url)
            entry: dict[str, Any] = {
                "item": item,
                "label": lbl,
                "url": url,
                "char_id": cid,
                "post_url": post_url,
                "data": None,
                "aspect": 1.0,
                "photo": None,
                "box": None,
                "photo_size": None,
            }
            self._entries.append(entry)
            if url:
                self._fetch(entry, gen)
            else:
                lbl.configure(text="?")
        # First paint after labels exist; aspects arrive async.
        self._schedule_layout(immediate=True)

    def retry_missing(self) -> None:
        """Re-fetch tiles that never got a photo (cancelled while we were away)."""
        gen = self._stamp
        pending = False
        for entry in self._entries:
            if entry.get("photo") is not None:
                continue
            if entry.get("data"):
                pending = True
                continue
            url = (entry.get("url") or "").strip()
            if not url:
                continue
            pending = True
            self._fetch(entry, gen)
        if pending:
            self._schedule_layout(immediate=True)

    def remove_char(self, char_id: int) -> bool:
        """Drop one tile and reflow in place (no full page reload)."""
        cid = int(char_id)
        hit = None
        for entry in self._entries:
            if int(entry.get("char_id") or 0) == cid:
                hit = entry
                break
        if hit is None:
            return False
        self._entries.remove(hit)
        try:
            hit["label"].destroy()
        except Exception:
            pass
        old = hit.get("photo")
        if old is not None:
            try:
                if old in self._photos:
                    self._photos.remove(old)
                release_photos([old])
            except Exception:
                pass
        self._last_sig = None
        if not self._entries:
            self.destroy()
            return True
        self._schedule_layout(immediate=True)
        return True

    def update_char_preview(
        self,
        char_id: int,
        *,
        preview_url: str,
        post_url: str | None = None,
        item: dict[str, Any] | None = None,
    ) -> bool:
        """Swap one tile's preview URL and refetch (no reorder)."""
        cid = int(char_id)
        url = (preview_url or "").strip()
        if not url:
            return False
        hit = None
        for entry in self._entries:
            if int(entry.get("char_id") or 0) == cid:
                hit = entry
                break
        if hit is None:
            return False
        if (hit.get("url") or "") == url and hit.get("photo") is not None:
            if item is not None:
                hit["item"] = item
            return True
        old_photo = hit.get("photo")
        if old_photo is not None:
            try:
                if old_photo in self._photos:
                    self._photos.remove(old_photo)
                release_photos([old_photo])
            except Exception:
                pass
        hit["photo"] = None
        hit["photo_size"] = None
        hit["data"] = None
        hit["url"] = url
        if post_url is not None:
            hit["post_url"] = str(post_url or "")
        if item is not None:
            hit["item"] = item
        try:
            hit["label"].configure(image="", text="…")
            self._bind_thumb(
                hit["label"],
                cid,
                str(hit.get("post_url") or ""),
            )
        except Exception:
            pass
        gen = self._stamp
        self._fetch(hit, gen)
        return True

    def _apply_bytes(self, entry: dict[str, Any], data: bytes, gen: int) -> None:
        if gen != self._stamp:
            return
        if self._inner is None or not entry["label"].winfo_exists():
            return
        entry["data"] = data
        url = entry.get("url") or ""
        # Restore normal click after a × → retry cycle.
        try:
            self._bind_thumb(
                entry["label"],
                int(entry.get("char_id") or 0),
                str(entry.get("post_url") or ""),
            )
        except Exception:
            pass

        def on_aspect(a: float) -> None:
            def ui() -> None:
                if gen != self._stamp:
                    return
                if self._inner is None or not entry["label"].winfo_exists():
                    return
                entry["aspect"] = float(a) if a and a > 0.05 else 1.0
                self._schedule_layout()

            try:
                entry["label"].after(0, ui)
            except Exception:
                pass

        hit = peek_aspect(url)
        if hit is not None:
            entry["aspect"] = hit
            self._schedule_layout()
            return
        schedule_aspect(data, cache_key=url, on_done=on_aspect)

    def _fetch(self, entry: dict[str, Any], gen: int) -> None:
        url = entry["url"]
        attempts = int(entry.get("fetch_attempts") or 0)

        def on_data(data: bytes) -> None:
            entry["fetch_attempts"] = 0
            try:
                entry["label"].after(0, lambda d=data: self._apply_bytes(entry, d, gen))
            except Exception:
                pass

        def on_err(_exc: BaseException) -> None:
            nxt = attempts + 1
            entry["fetch_attempts"] = nxt

            def fail_or_retry() -> None:
                if gen != self._stamp or not entry["label"].winfo_exists():
                    return
                if nxt < 3:
                    entry["label"].configure(text="…")
                    delay = 700 * nxt
                    try:
                        entry["label"].after(
                            delay, lambda: self._fetch(entry, gen)
                        )
                    except Exception:
                        pass
                    return
                entry["label"].configure(text="×")
                # Click × to try again.
                entry["label"].bind(
                    "<Button-1>",
                    lambda _e, e=entry, g=gen: self._retry_fetch(e, g),
                )

            try:
                entry["label"].after(0, fail_or_retry)
            except Exception:
                pass

        # Always go through the pool (even cache hits) so render() stays snappy.
        schedule_thumb_fetch(url, on_data=on_data, on_err=on_err)

    def _retry_fetch(self, entry: dict[str, Any], gen: int) -> None:
        if gen != self._stamp or not entry["label"].winfo_exists():
            return
        entry["fetch_attempts"] = 0
        entry["label"].configure(text="…", image="")
        self._fetch(entry, gen)

    def _schedule_layout(self, *, immediate: bool = False) -> None:
        if self._dragging_scroll:
            self._layout_after_drag = True
            return
        self._cancel_layout()
        if self._canvas is None:
            return
        delay = 1 if immediate else _LAYOUT_DEBOUNCE_MS
        self._layout_after = self._canvas.after(delay, self._layout)

    def _layout(self) -> None:
        self._layout_after = None
        if self._dragging_scroll:
            self._layout_after_drag = True
            return
        if self._canvas is None or self._inner is None or not self._entries:
            return
        parent_w = 0
        try:
            parent_w = int(self._parent.winfo_width() or 0)
        except Exception:
            parent_w = 0
        ready = ready_layout_width(self._canvas.winfo_width(), parent_w)
        if ready is None:
            self._layout_waits += 1
            if self._layout_waits < 40:
                self._layout_after = self._canvas.after(16, self._layout)
                return
            ready = max(240, int(self._canvas.winfo_width() or 0), parent_w - 28)
        self._layout_waits = 0
        view_w = ready
        view_h = max(80, self._canvas.winfo_height())
        target = target_row_height(view_h, view_w, scale=self._preview_scale)
        aspects = [float(e.get("aspect") or 1.0) for e in self._entries]
        # Round aspects so tiny EXIF noise doesn't force a full reshuffle.
        sig = (view_w, target, round(self._preview_scale, 2), tuple(round(a, 2) for a in aspects))
        if sig == self._last_sig and not self._decode_queue:
            return

        self._in_layout = True
        try:
            boxes, total_h, order = justify_layout(
                aspects,
                view_w,
                target_h=target,
                gap=self._gap(),
                stretch_last=False,
                reorder=False,
            )
            need_h = max(total_h, view_h)
            if self._inner.winfo_width() != view_w or self._inner.winfo_height() != need_h:
                self._inner.configure(width=view_w, height=need_h)
            self._last_view_w = view_w
            self._last_sig = sig

            decode_left = _DECODE_BUDGET
            pending: list[int] = []
            surf = self._surface().get("canvas", "#1e1f22")
            for box, src_i in zip(boxes, order):
                entry = self._entries[src_i]
                x, y, bw, bh = box
                lbl: tk.Label = entry["label"]
                try:
                    lbl.configure(bg=surf)
                except Exception:
                    pass
                data = entry.get("data")
                prev_size = entry.get("photo_size")
                size = (bw, bh)
                need_decode = data is not None and (
                    entry.get("photo") is None or prev_size != size
                )
                if need_decode:
                    if decode_left <= 0:
                        pending.append(src_i)
                    else:
                        decode_left -= 1
                        try:
                            photo = decode_thumb_sized(data, bw, bh)
                            old = entry.get("photo")
                            entry["photo"] = photo
                            entry["photo_size"] = size
                            self._photos.append(photo)
                            lbl.configure(image=photo, text="")
                            if old is not None:
                                try:
                                    if old in self._photos:
                                        self._photos.remove(old)
                                    release_photos([old])
                                except Exception:
                                    pass
                        except Exception as exc:
                            logger.debug("natural decode failed: %s", exc)
                            lbl.configure(text="×")
                entry["box"] = box
                lbl.place(x=x, y=y, width=bw, height=bh)
            self._canvas.configure(scrollregion=(0, 0, view_w, max(total_h, 1)))
            self._decode_queue = pending
        finally:
            self._in_layout = False

        if self._decode_queue and self._canvas is not None:
            # Keep UI responsive — finish remaining resizes on later ticks.
            self._layout_after = self._canvas.after(1, self._flush_decodes)

    def _flush_decodes(self) -> None:
        self._layout_after = None
        if self._canvas is None or not self._decode_queue:
            return
        budget = _DECODE_BUDGET
        left: list[int] = []
        for src_i in self._decode_queue:
            if budget <= 0:
                left.append(src_i)
                continue
            if src_i < 0 or src_i >= len(self._entries):
                continue
            entry = self._entries[src_i]
            box = entry.get("box")
            data = entry.get("data")
            if not box or data is None:
                continue
            _x, _y, bw, bh = box
            size = (bw, bh)
            if entry.get("photo") is not None and entry.get("photo_size") == size:
                continue
            budget -= 1
            try:
                photo = decode_thumb_sized(data, bw, bh)
                old = entry.get("photo")
                entry["photo"] = photo
                entry["photo_size"] = size
                self._photos.append(photo)
                entry["label"].configure(image=photo, text="")
                if old is not None:
                    try:
                        if old in self._photos:
                            self._photos.remove(old)
                        release_photos([old])
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("natural decode flush failed: %s", exc)
        self._decode_queue = left
        if left and self._canvas is not None:
            self._layout_after = self._canvas.after(1, self._flush_decodes)


PAIR_GAP = 3  # px gap between before/after inside a pair cell
BindPairFn = Callable[[tk.Label, tk.Label, int, str], None]


class PairGallery:
    """Justified gallery of before|after pair cells (tamed cards)."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        photos: list[Any],
        bind_pair: BindPairFn,
        gen_fn: Callable[[], int],
        preview_scale: float = 1.0,
        scroll_speed: float = 3.0,
    ) -> None:
        self._parent = parent
        self._photos = photos
        self._bind_pair = bind_pair
        self._gen_fn = gen_fn
        self._preview_scale = max(0.5, min(2.0, float(preview_scale or 1.0)))
        self._scroll_speed = max(0.25, min(6.0, float(scroll_speed or 3.0)))
        self._entries: list[dict[str, Any]] = []
        self._canvas: tk.Canvas | None = None
        self._inner: tk.Frame | None = None
        self._sb: ttk.Scrollbar | None = None
        self._win = None
        self._layout_after: str | None = None
        self._wheel_bound = False
        self._in_layout = False
        self._last_sig: tuple[Any, ...] | None = None
        self._last_view_w = 0
        self._decode_queue: list[int] = []
        self._dragging_scroll = False
        self._layout_after_drag = False
        self._scroll_gen = 0
        self._smooth_remaining = 0.0
        self._smooth_after: str | None = None

    def set_preview_scale(self, scale: float) -> None:
        self._preview_scale = max(0.5, min(2.0, float(scale or 1.0)))
        self._last_sig = None
        self._schedule_layout(immediate=True)

    def set_scroll_speed(self, speed: float) -> None:
        self._scroll_speed = max(0.25, min(6.0, float(speed or 3.0)))
        if self._canvas is not None:
            self._canvas.configure(yscrollincrement=1)

    def _surface(self) -> dict[str, str]:
        from link_bridge.theme import surface_for

        return surface_for(self._parent)

    def _gap(self) -> int:
        from link_bridge.theme import gallery_gap

        return gallery_gap(self._parent)

    def _apply_surface(self) -> None:
        c = self._surface()
        bg = c.get("canvas") or c.get("bg") or "#1e1f22"
        try:
            if self._canvas is not None:
                self._canvas.configure(
                    bg=bg, highlightthickness=0, highlightbackground=bg, bd=0
                )
            if self._inner is not None:
                self._inner.configure(bg=bg, highlightthickness=0, bd=0)
        except Exception:
            pass

    def _queue_smooth_scroll(self, px: float) -> None:
        if self._canvas is None or abs(px) < 0.01:
            return
        self._smooth_remaining += float(px)
        if self._smooth_after is None:
            self._tick_smooth_scroll()

    def _tick_smooth_scroll(self) -> None:
        self._smooth_after = None
        if self._canvas is None:
            self._smooth_remaining = 0.0
            return
        rem = self._smooth_remaining
        if abs(rem) < 0.8:
            if abs(rem) >= 0.2:
                self._canvas.yview_scroll(1 if rem > 0 else -1, "units")
            self._smooth_remaining = 0.0
            return
        step = rem * _SMOOTH_EASE
        if abs(step) < 1.0:
            step = 1.0 if rem > 0 else -1.0
        moved = int(round(step))
        if moved == 0:
            self._smooth_remaining = 0.0
            return
        self._smooth_remaining -= moved
        self._canvas.yview_scroll(moved, "units")
        self._smooth_after = self._canvas.after(_SMOOTH_FRAME_MS, self._tick_smooth_scroll)

    def destroy(self) -> None:
        global _wheel_target
        self._cancel_layout()
        if self._smooth_after is not None and self._canvas is not None:
            try:
                self._canvas.after_cancel(self._smooth_after)
            except Exception:
                pass
        self._smooth_after = None
        self._smooth_remaining = 0.0
        if _wheel_target is self:
            _wheel_target = None
        self._wheel_bound = False
        self._entries.clear()
        self._last_sig = None
        self._decode_queue.clear()
        if self._canvas is not None:
            try:
                self._canvas.destroy()
            except Exception:
                pass
        if self._sb is not None:
            try:
                self._sb.destroy()
            except Exception:
                pass
        self._canvas = None
        self._inner = None
        self._sb = None
        self._win = None

    def _cancel_layout(self) -> None:
        if self._layout_after is not None and self._canvas is not None:
            try:
                self._canvas.after_cancel(self._layout_after)
            except Exception:
                pass
        self._layout_after = None

    def _on_scrollbar(self, *args) -> None:
        self._scroll_gen += 1
        gen = self._scroll_gen
        self._dragging_scroll = True
        self._smooth_remaining = 0.0
        if self._canvas is not None:
            self._canvas.yview(*args)

            def _clear() -> None:
                if gen != self._scroll_gen:
                    return
                self._dragging_scroll = False
                if self._layout_after_drag:
                    self._layout_after_drag = False
                    self._schedule_layout()

            self._canvas.after(180, _clear)

    def _ensure_chrome(self) -> tk.Frame:
        if self._canvas is not None and self._inner is not None:
            return self._inner
        parent = self._parent
        for child in list(parent.winfo_children()):
            child.destroy()
        self._sb = None
        self._canvas = tk.Canvas(parent, highlightthickness=0, bd=0)
        self._canvas.configure(yscrollincrement=1)
        self._apply_surface()
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._inner = tk.Frame(self._canvas, bd=0, highlightthickness=0)
        self._apply_surface()
        self._win = self._canvas.create_window((0, 0), window=self._inner, anchor=tk.NW)

        def _on_inner(_event=None) -> None:
            if self._in_layout or self._dragging_scroll or self._canvas is None:
                return
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))

        def _on_canvas(event) -> None:
            if self._in_layout or self._canvas is None or self._win is None:
                return
            width = int(getattr(event, "width", 0) or 0)
            if width < 80:
                return
            self._canvas.itemconfigure(self._win, width=width)
            if self._dragging_scroll:
                return
            if abs(width - self._last_view_w) < 8 and self._last_sig:
                return
            self._schedule_layout()

        self._inner.bind("<Configure>", _on_inner)
        self._canvas.bind("<Configure>", _on_canvas)
        self._canvas.bind("<Enter>", self._claim_wheel)
        self._inner.bind("<Enter>", self._claim_wheel)

        if not self._wheel_bound:
            self._canvas.bind_all("<MouseWheel>", _gallery_wheel)
            self._wheel_bound = True
        return self._inner

    def _claim_wheel(self, _event=None) -> None:
        global _wheel_target
        _wheel_target = self  # type: ignore[assignment]

    def render(self, items: list[dict[str, Any]]) -> None:
        self.destroy()
        inner = self._ensure_chrome()
        if _wheel_target is None:
            self._claim_wheel()
        gen = self._gen_fn()
        self._entries = []
        self._last_sig = None
        surf = self._surface().get("canvas", "#1e1f22")
        muted = self._surface().get("muted", "#b5bac1")
        for item in items:
            cid = int(item.get("id") or 0)
            post_url = (item.get("post_url") or "").strip()
            before_url = (
                (item.get("before_preview_url") or "").strip()
                or (item.get("preview_url") or "").strip()
            )
            after_url = (
                (item.get("after_preview_url") or "").strip()
                or (item.get("preview_url") or "").strip()
            )
            cell = tk.Frame(inner, bd=0, highlightthickness=0, bg=surf)
            before_lbl = tk.Label(
                cell,
                text="…",
                relief=tk.FLAT,
                cursor="hand2",
                bd=0,
                highlightthickness=0,
                bg=surf,
                fg=muted,
            )
            after_lbl = tk.Label(
                cell,
                text="…",
                relief=tk.FLAT,
                cursor="hand2",
                bd=0,
                highlightthickness=0,
                bg=surf,
                fg=muted,
            )
            self._bind_pair(before_lbl, after_lbl, cid, post_url)
            entry: dict[str, Any] = {
                "item": item,
                "cell": cell,
                "before_lbl": before_lbl,
                "after_lbl": after_lbl,
                "before_url": before_url,
                "after_url": after_url,
                "char_id": cid,
                "post_url": post_url,
                "before_data": None,
                "after_data": None,
                "before_aspect": 1.0,
                "after_aspect": 1.0,
                "aspect": 2.05,
                "before_photo": None,
                "after_photo": None,
                "before_photo_size": None,
                "after_photo_size": None,
                "box": None,
            }
            self._entries.append(entry)
            if before_url:
                self._fetch_side(entry, "before", gen)
            else:
                before_lbl.configure(text="?")
            if after_url:
                self._fetch_side(entry, "after", gen)
            else:
                after_lbl.configure(text="?")
        self._schedule_layout(immediate=True)

    def _pair_aspect(self, entry: dict[str, Any]) -> float:
        a0 = float(entry.get("before_aspect") or 1.0)
        a1 = float(entry.get("after_aspect") or 1.0)
        # Approximate inner gap as a thin extra strip at typical row height.
        gap_frac = PAIR_GAP / max(120.0, 1.0)
        return max(0.4, a0 + a1 + gap_frac)

    def _apply_side(
        self, entry: dict[str, Any], side: str, data: bytes, gen: int
    ) -> None:
        if gen != self._gen_fn():
            return
        lbl = entry[f"{side}_lbl"]
        if self._inner is None or not lbl.winfo_exists():
            return
        entry[f"{side}_data"] = data
        url = entry.get(f"{side}_url") or ""

        def on_aspect(a: float) -> None:
            def ui() -> None:
                if gen != self._gen_fn():
                    return
                if self._inner is None or not lbl.winfo_exists():
                    return
                entry[f"{side}_aspect"] = float(a) if a and a > 0.05 else 1.0
                entry["aspect"] = self._pair_aspect(entry)
                self._schedule_layout()

            try:
                lbl.after(0, ui)
            except Exception:
                pass

        hit = peek_aspect(url)
        if hit is not None:
            entry[f"{side}_aspect"] = hit
            entry["aspect"] = self._pair_aspect(entry)
            self._schedule_layout()
            return
        schedule_aspect(data, cache_key=url, on_done=on_aspect)

    def _fetch_side(self, entry: dict[str, Any], side: str, gen: int) -> None:
        url = entry[f"{side}_url"]
        lbl = entry[f"{side}_lbl"]
        key = f"{side}_fetch_attempts"
        attempts = int(entry.get(key) or 0)

        def on_data(data: bytes) -> None:
            entry[key] = 0
            try:
                lbl.after(0, lambda d=data: self._apply_side(entry, side, d, gen))
            except Exception:
                pass

        def on_err(_exc: BaseException) -> None:
            nxt = attempts + 1
            entry[key] = nxt

            def fail_or_retry() -> None:
                if gen != self._gen_fn() or not lbl.winfo_exists():
                    return
                if nxt < 3:
                    lbl.configure(text="…")
                    try:
                        lbl.after(
                            700 * nxt,
                            lambda: self._fetch_side(entry, side, gen),
                        )
                    except Exception:
                        pass
                    return
                lbl.configure(text="×")

            try:
                lbl.after(0, fail_or_retry)
            except Exception:
                pass

        schedule_thumb_fetch(url, on_data=on_data, on_err=on_err)

    def _schedule_layout(self, *, immediate: bool = False) -> None:
        if self._dragging_scroll:
            self._layout_after_drag = True
            return
        self._cancel_layout()
        if self._canvas is None:
            return
        delay = 1 if immediate else _LAYOUT_DEBOUNCE_MS
        self._layout_after = self._canvas.after(delay, self._layout)

    def _layout(self) -> None:
        self._layout_after = None
        if self._dragging_scroll:
            self._layout_after_drag = True
            return
        if self._canvas is None or self._inner is None or not self._entries:
            return
        view_w = max(80, self._canvas.winfo_width())
        view_h = max(80, self._canvas.winfo_height())
        if view_w < 40:
            return
        target = target_row_height(view_h, view_w, scale=self._preview_scale)
        aspects = [float(e.get("aspect") or 2.0) for e in self._entries]
        sig = (
            view_w,
            target,
            round(self._preview_scale, 2),
            tuple(round(a, 2) for a in aspects),
        )
        if sig == self._last_sig and not self._decode_queue:
            return

        self._in_layout = True
        try:
            boxes, total_h, order = justify_layout(
                aspects,
                view_w,
                target_h=target,
                gap=self._gap(),
                stretch_last=False,
                reorder=False,
            )
            need_h = max(total_h, view_h)
            if self._inner.winfo_width() != view_w or self._inner.winfo_height() != need_h:
                self._inner.configure(width=view_w, height=need_h)
            self._last_view_w = view_w
            self._last_sig = sig

            decode_left = _DECODE_BUDGET
            pending: list[int] = []
            for box, src_i in zip(boxes, order):
                entry = self._entries[src_i]
                x, y, bw, bh = box
                cell: tk.Frame = entry["cell"]
                a0 = float(entry.get("before_aspect") or 1.0)
                a1 = float(entry.get("after_aspect") or 1.0)
                gap = PAIR_GAP
                usable = max(2, bw - gap)
                w0 = max(1, int(round(usable * a0 / max(a0 + a1, 0.01))))
                w1 = max(1, usable - w0)
                # Decode / place each side.
                for side, sw, ox in (
                    ("before", w0, 0),
                    ("after", w1, w0 + gap),
                ):
                    data = entry.get(f"{side}_data")
                    lbl: tk.Label = entry[f"{side}_lbl"]
                    prev_size = entry.get(f"{side}_photo_size")
                    size = (sw, bh)
                    need_decode = data is not None and (
                        entry.get(f"{side}_photo") is None or prev_size != size
                    )
                    if need_decode:
                        if decode_left <= 0:
                            if src_i not in pending:
                                pending.append(src_i)
                        else:
                            decode_left -= 1
                            try:
                                photo = decode_thumb_sized(data, sw, bh)
                                old = entry.get(f"{side}_photo")
                                entry[f"{side}_photo"] = photo
                                entry[f"{side}_photo_size"] = size
                                self._photos.append(photo)
                                lbl.configure(image=photo, text="")
                                if old is not None:
                                    try:
                                        if old in self._photos:
                                            self._photos.remove(old)
                                        release_photos([old])
                                    except Exception:
                                        pass
                            except Exception as exc:
                                logger.debug("pair decode failed: %s", exc)
                                lbl.configure(text="×")
                    lbl.place(x=ox, y=0, width=sw, height=bh)
                entry["box"] = box
                cell.place(x=x, y=y, width=bw, height=bh)
            self._canvas.configure(scrollregion=(0, 0, view_w, max(total_h, 1)))
            self._decode_queue = pending
        finally:
            self._in_layout = False

        if self._decode_queue and self._canvas is not None:
            self._layout_after = self._canvas.after(1, self._flush_decodes)

    def _flush_decodes(self) -> None:
        self._layout_after = None
        if self._canvas is None or not self._decode_queue:
            return
        # Re-run layout to finish pending side decodes with a fresh budget.
        self._last_sig = None
        self._layout()
