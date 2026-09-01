"""Compact balance label for the roster tab row."""

from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from typing import Any

_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF]+"
)


def _balance_amount(body: dict[str, Any]) -> int | None:
    if "balance" in body:
        try:
            return int(body["balance"])
        except (TypeError, ValueError):
            pass
    disp = str(body.get("balance_display") or "").strip()
    m = re.match(r"^(\d+)", disp)
    if m:
        return int(m.group(1))
    return None


class BalanceChip(tk.Frame):
    """Plain balance amount label (no coin emoji)."""

    def __init__(self, master: tk.Misc, *, fg: str = "#f2f3f5", bg: str = "") -> None:
        super().__init__(master, bd=0, highlightthickness=0, bg=bg or master.cget("bg"))
        self._fg = fg
        self._font = tkfont.Font(family="Segoe UI", size=10)
        self._lbl = tk.Label(
            self,
            text="",
            font=self._font,
            fg=fg,
            bg=self.cget("bg"),
            bd=0,
            highlightthickness=0,
        )
        self._lbl.pack()

    def apply_theme(self, *, fg: str, bg: str) -> None:
        self._fg = fg
        self.configure(bg=bg)
        self._lbl.configure(fg=fg, bg=bg)

    def set_from_body(self, body: dict[str, Any]) -> None:
        amount = _balance_amount(body)
        if amount is None:
            self.clear()
            return
        self.set_balance(amount)

    def set_balance(self, amount: int, _coin: str = "") -> None:
        self._lbl.configure(text=str(int(amount)))

    def clear(self) -> None:
        self._lbl.configure(text="")
