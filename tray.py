"""Minimal tray helpers (pystray). Optional — GUI still works without it."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def make_icon_image():
    """Small solid icon for the tray (Pillow)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(46, 125, 50, 255))
    draw.rectangle((28, 14, 36, 50), fill=(255, 255, 255, 255))
    draw.polygon([(18, 28), (46, 28), (32, 48)], fill=(255, 255, 255, 255))
    return img


def start_tray(
    *,
    on_show: Callable[[], None],
    on_toggle_pause: Callable[[], None],
    on_quit: Callable[[], None],
    on_check_updates: Callable[[], None] | None = None,
    title: str = "Harem Link Bridge",
) -> Any | None:
    """Start a background tray icon. Returns the icon, or None if unavailable.

    Left double-click opens the window (``Show`` is the default menu action).
    """
    try:
        import pystray
        from pystray import MenuItem as Item
    except ImportError:
        logger.info("pystray not installed — tray disabled")
        return None

    items = [
        # default=True → Windows left-double-click activates Show.
        Item("Show", lambda _icon, _item: on_show(), default=True),
        Item("Pause / Resume", lambda _icon, _item: on_toggle_pause()),
    ]
    if on_check_updates is not None:
        items.append(
            Item("Check for updates", lambda _icon, _item: on_check_updates())
        )
    items.append(Item("Quit", lambda _icon, _item: on_quit()))
    menu = pystray.Menu(*items)
    icon = pystray.Icon("harem_link_bridge", make_icon_image(), title, menu)
    try:
        icon.run_detached()
    except AttributeError:
        import threading

        threading.Thread(target=icon.run, daemon=True).start()
    return icon
