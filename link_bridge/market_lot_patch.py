"""Source-side fixes for the frozen MarketLotWindow (1.4.19 exe bytecode).

Two gaps the frozen code cannot cover:
1. The lot picture is a fixed-size thumbnail (``decode_thumb`` to a small
   view) - small and off-center in a big/fullscreen window. This renders the
   full image instead, Omni-style: fit into the label, centered.
2. Window geometry restores the size string but never the zoomed/fullscreen
   state. This adds state persist via installable get/set hooks.

Same patch pattern as ``ws_client.py``: override methods on the frozen class.
Only ``_photo`` / ``_shown_url`` attrs are shared with frozen logic, so the
buy/hide/original flows keep working untouched.
"""

from __future__ import annotations

import io
import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

_installed = False
_get_state: Callable[[], str] | None = None
_set_state: Callable[[str], None] | None = None


def _best_full_url(item: dict) -> str:
    for key in ("image_url", "file_url", "preview_url"):
        try:
            url = str(item.get(key) or "").strip()
        except Exception:
            continue
        if url.startswith("http"):
            return url
    return ""


def _fit_size(w: int, h: int, max_w: int, max_h: int) -> tuple[int, int]:
    """Shrink-only fit (never upscale a small source into blur)."""
    max_w = max(1, int(max_w))
    max_h = max(1, int(max_h))
    scale = min(max_w / max(int(w), 1), max_h / max(int(h), 1), 1.0)
    if scale >= 0.999:
        return int(w), int(h)
    return max(1, int(int(w) * scale)), max(1, int(int(h) * scale))


def _fit_photo(pil_image: Any, max_w: int, max_h: Any) -> Any:
    from PIL import Image, ImageTk

    w, h = pil_image.size
    nw, nh = _fit_size(w, h, max_w, max_h)
    if (nw, nh) != (int(w), int(h)):
        pil_image = pil_image.resize((nw, nh), Image.Resampling.BILINEAR)
    return ImageTk.PhotoImage(pil_image)


def _patched_load_image(self: Any) -> None:
    """Full-res replacement for the frozen thumbnail loader."""
    try:
        item = dict(getattr(self, "_item", None) or {})
    except Exception:
        item = {}
    url = _best_full_url(item)
    if not url:
        try:
            url = str(self._display_url() or "").strip()
        except Exception:
            url = ""
    if not url:
        try:
            self._img_lbl.configure(text="No preview", image="")
            self._photo = None
        except Exception:
            pass
        return
    if getattr(self, "_photo", None) is not None and url == getattr(
        self, "_shown_url", None
    ):
        return
    try:
        self._img_lbl.configure(image="", text="Loading…")
    except Exception:
        pass
    gen = (int(getattr(self, "_img_gen", 0) or 0) + 1) % 1000000
    self._img_gen = gen

    def _refit_cached() -> None:
        raw = getattr(self, "_full_pil", None)
        cur_url = getattr(self, "_shown_url", None)
        if raw is None or cur_url != url:
            return
        try:
            if not self.winfo_exists():
                return
            lw = self._img_lbl.winfo_width()
            lh = self._img_lbl.winfo_height()
            if lw < 10 or lh < 10:
                return
            photo = _fit_photo(raw, lw, lh)
            self._img_lbl.configure(image=photo, text="")
            self._photo = photo
        except Exception:
            logger.debug("lot image refit failed", exc_info=True)

    try:
        if not getattr(self, "_fit_bound", False):
            self._fit_bound = True

            def _on_resize(_event: Any = None) -> None:
                try:
                    self.after_idle(_refit_cached)
                except Exception:
                    pass

            try:
                self._img_lbl.bind("<Configure>", _on_resize, add="+")
            except Exception:
                pass
    except Exception:
        pass

    def _worker() -> None:
        try:
            from link_bridge import image_cache
            from link_bridge.thumb_grid import fetch_url_bytes

            data = image_cache.get(url) or b""
            if not data:
                data = fetch_url_bytes(url, timeout=30, retries=2)
                if data:
                    image_cache.put(url, data)
        except Exception as exc:
            logger.debug("lot full image fetch failed: %s", exc, exc_info=True)
            data = b""

        def _apply() -> None:
            try:
                if not self.winfo_exists():
                    return
                if int(getattr(self, "_img_gen", -1) or -1) != gen:
                    return
                if not data:
                    self._img_lbl.configure(text="No preview", image="")
                    return
                from PIL import Image

                raw = Image.open(io.BytesIO(bytes(data))).convert("RGB")
                self._full_pil = raw
                self._shown_url = url
                try:
                    lw = self._img_lbl.winfo_width()
                    lh = self._img_lbl.winfo_height()
                    if lw < 10 or lh < 10:
                        lw, lh = self.winfo_width(), self.winfo_height()
                    photo = _fit_photo(raw, max(64, lw), max(64, lh))
                except Exception:
                    photo = _fit_photo(raw, 1400, 1000)
                self._img_lbl.configure(image=photo, text="", anchor="center")
                try:
                    self._img_lbl.configure(compound="center")
                except Exception:
                    pass
                self._photo = photo
            except Exception:
                logger.debug("lot full image render failed", exc_info=True)
                try:
                    self._img_lbl.configure(text="No preview", image="")
                except Exception:
                    pass

        try:
            self.after(0, _apply)
        except Exception:
            pass

    threading.Thread(target=_worker, name="hlb-lot-img", daemon=True).start()


def _controls_first_order(keys_sides: list[tuple[Any, str]]) -> list[Any]:
    """Order keys with bottom-docked control bars first.

    Tk pack serves widgets in packing order: first-packed keeps its parcel
    when the window shrinks. The frozen lot window packs the image area
    first, so a large photo squeezes the buttons out. Omni packs controls
    first - mirror that.
    """
    docks = [k for k, s in keys_sides if s == "bottom"]
    rest = [k for k, s in keys_sides if s != "bottom"]
    return docks + rest


def _wrap_build_ui(orig: Any) -> Any:
    def _build_ui(self: Any) -> None:
        orig(self)
        try:
            kids = list(self.winfo_children())
            packed: list[tuple[Any, dict]] = []
            for child in kids:
                try:
                    info = dict(child.pack_info())
                except Exception:
                    continue
                packed.append((child, info))
            if not packed:
                return
            ordered = _controls_first_order(
                [(idx, str(info.get("side") or "top")) for idx, (_, info) in enumerate(packed)]
            )
            # Re-pack in the new order with each widget's original options.
            by_pos = {pos: (child, info) for pos, (child, info) in enumerate(packed)}
            for pos in ordered:
                child, info = by_pos[int(pos)]
                opts = {k: v for k, v in info.items() if k in ("side", "fill", "expand", "padx", "pady", "ipadx", "ipady", "anchor")}
                child.pack_forget()
                child.pack(**opts)
        except Exception:
            logger.debug("lot layout reorder failed", exc_info=True)

    return _build_ui


def _wrap_restore(orig: Any) -> Any:
    def _restore_or_center(self: Any) -> None:
        try:
            orig(self)
        except Exception:
            pass
        # Frozen code restores size only - re-apply the zoomed state too.
        try:
            if _get_state is not None and str(_get_state() or "").lower() == "zoomed":
                self.state("zoomed")
        except Exception:
            pass

    return _restore_or_center


def _persist_with_state(self: Any) -> None:
    """Omni-style persist: state always, geometry string only when windowed."""
    try:
        state = str(self.state() or "normal")
    except Exception:
        state = "normal"
    norm_state = "zoomed" if state == "zoomed" else "normal"
    if _set_state is not None:
        try:
            _set_state(norm_state)
        except Exception:
            pass
    if state == "zoomed":
        return
    try:
        geo = self.geometry()
    except Exception:
        return
    setter = getattr(self, "_set_window_geo", None)
    if setter is None:
        return
    try:
        setter(geo)
    except Exception:
        pass


def install(
    get_state: Callable[[], str] | None = None,
    set_state: Callable[[str], None] | None = None,
) -> bool:
    """Patch the frozen MarketLotWindow once. Safe to call repeatedly."""
    global _installed, _get_state, _set_state
    if get_state is not None:
        _get_state = get_state
    if set_state is not None:
        _set_state = set_state
    if _installed:
        return True
    try:
        from link_bridge import market_lot
    except Exception:
        logger.debug("lot patch: market_lot import failed", exc_info=True)
        return False
    cls = getattr(market_lot, "MarketLotWindow", None)
    if cls is None:
        return False
    try:
        orig_restore = cls._restore_or_center
        orig_build = cls._build_ui
        cls._load_image = _patched_load_image
        cls._restore_or_center = _wrap_restore(orig_restore)
        cls._build_ui = _wrap_build_ui(orig_build)
        cls._persist_geometry = _persist_with_state
    except Exception:
        logger.debug("lot patch: method override failed", exc_info=True)
        return False
    _installed = True
    return True
