"""Search box for the frozen Themes admin pools (see themes_admin.exe.pyc).

The frozen ``ThemesAdminPanel`` shows two ``tk.Text`` pools (main/sec) in a
notebook but offers no way to find a line. This adds a search row on top:
typing jumps the viewport ("camera") to the first case-insensitive match —
switching to the pool tab that contains it, scrolling it into view and
highlighting it. No match → status note. Empty query clears the highlight.

Same patch pattern as ``market_lot_patch.py``: only ``main_pool`` /
``sec_pool`` / ``_nb`` / ``status_var`` attrs are shared with frozen logic.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Any

logger = logging.getLogger(__name__)

SEARCH_DEBOUNCE_MS = 300
HIT_TAG = "themes_search_hit"


def find_line_match(lines: list[str], query: str) -> tuple[int, int, int] | None:
    """(1-based line, start col, end col) of the first case-insensitive hit."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for lineno, line in enumerate(lines or [], start=1):
        col = (line or "").lower().find(q)
        if col >= 0:
            return lineno, col, col + len(q)
    return None


def _pool_lines(pool: Any) -> list[str]:
    try:
        text = pool.text
    except AttributeError:
        return []
    try:
        content = text.get("1.0", "end-1c")
    except Exception:
        return []
    return str(content or "").splitlines()


def _clear_hit(pool: Any) -> None:
    try:
        pool.text.tag_remove(HIT_TAG, "1.0", tk.END)
    except Exception:
        pass


def _show_hit(pool: Any, match: tuple[int, int, int]) -> None:
    lineno, start, end = match
    text = pool.text
    try:
        text.tag_configure(HIT_TAG, background="yellow", foreground="black")
    except Exception:
        pass
    idx0, idx1 = f"{lineno}.{start}", f"{lineno}.{end}"
    try:
        text.tag_remove(HIT_TAG, "1.0", tk.END)
        text.tag_add(HIT_TAG, idx0, idx1)
        text.tag_raise(HIT_TAG)
        text.mark_set("insert", idx0)
        text.see(idx0)
    except Exception:
        logger.debug("themes search scroll failed", exc_info=True)


def _select_pool(nb: Any, pool: Any) -> None:
    """Switch the notebook to the tab containing pool (no-op when unknown)."""
    try:
        tabs = list(nb.tabs())
    except Exception:
        return
    target = str(pool)
    for tab in tabs:
        try:
            frame = nb.nametowidget(tab)
        except Exception:
            continue
        if target == str(frame) or target.startswith(str(frame) + "."):
            try:
                nb.select(tab)
            except Exception:
                pass
            return


def search_pools(pools: list[Any], nb: Any, query: str, set_status: Any) -> bool:
    """Jump to the first match across pools. True when something matched."""
    q = (query or "").strip()
    if not q:
        for pool in pools:
            _clear_hit(pool)
        return False
    for pool in pools:
        match = find_line_match(_pool_lines(pool), q)
        if match is not None:
            _select_pool(nb, pool)
            _show_hit(pool, match)
            set_status(f"Ln {match[0]}")
            return True
    for pool in pools:
        _clear_hit(pool)
    set_status(f"No match for “{q}”")
    return False


def install(panel: Any) -> bool:
    """Add the search row to a ThemesAdminPanel. Safe to call repeatedly."""
    if getattr(panel, "_themes_search_installed", False):
        return True
    try:
        pools = [panel.main_pool, panel.sec_pool]
        nb, status_var = panel._nb, panel.status_var
    except AttributeError:
        logger.debug("themes search: frozen panel shape changed", exc_info=True)
        return False

    def _set_status(msg: str) -> None:
        try:
            status_var.set(msg)
        except Exception:
            pass

    def _run_search(query: str) -> None:
        search_pools(pools, nb, query, _set_status)

    try:
        row = ttk.Frame(panel)
        ttk.Label(row, text="Search").pack(side=tk.LEFT)
        var = tk.StringVar(value="")
        entry = ttk.Entry(row, textvariable=var, width=24)
        entry.pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)
        try:
            from link_bridge.theme import bind_entry_clipboard

            bind_entry_clipboard(entry)
        except Exception:
            pass
        after_id: list[str | None] = [None]

        def _on_typed(*_args: Any) -> None:
            if after_id[0] is not None:
                try:
                    entry.after_cancel(after_id[0])
                except Exception:
                    pass
            after_id[0] = entry.after(
                SEARCH_DEBOUNCE_MS, lambda: _run_search(var.get())
            )

        var.trace_add("write", _on_typed)
        row.pack(fill=tk.X, pady=(0, 4), before=nb)
    except Exception:
        logger.debug("themes search row build failed", exc_info=True)
        return False
    panel._themes_search_installed = True
    return True
