"""Hardcore fullscreen: re-zoom content windows that drop to windowed.

Off by default. When enabled, a windowed-but-visible content window
(state ``normal``) is pushed back to ``zoomed``. Minimized (``iconic``)
and tray-hidden (``withdrawn``) states are never fought — only the
"mostly screen-wide but a WINDOW" case the toggle exists for.
"""

from __future__ import annotations

from typing import Any, Callable


def needs_rezoom(state: str, *, enabled: bool, viewable: bool) -> bool:
    """True only for a visible windowed window while the toggle is on."""
    return bool(enabled) and bool(viewable) and (state or "") == "normal"


def _rezoom(win: Any, enabled_fn: Callable[[], bool]) -> None:
    try:
        if not bool(enabled_fn()):
            return
        try:
            state = str(win.state() or "")
        except Exception:
            return
        try:
            viewable = bool(win.winfo_viewable())
        except Exception:
            return
        if not needs_rezoom(state, enabled=True, viewable=viewable):
            return
        win.state("zoomed")
    except Exception:
        pass


def bind_hardcore(win: Any, enabled_fn: Callable[[], bool]) -> None:
    """Re-zoom ``win`` whenever it lands in windowed state (debounced idle)."""

    def _on_cfg(_event: Any = None) -> None:
        try:
            if not bool(enabled_fn()):
                return
            win.after_idle(lambda: _rezoom(win, enabled_fn))
        except Exception:
            pass

    try:
        win.bind("<Configure>", _on_cfg, add="+")
    except Exception:
        pass
