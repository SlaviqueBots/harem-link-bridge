"""Multi-set membership helpers (must match bot ``card_sets.SET_NAME_SEP``)."""

from __future__ import annotations

from collections.abc import Sequence

# Unit separator — same as bot.services.card_sets.SET_NAME_SEP
SET_NAME_SEP = "\x1f"
SET_NAME_MAX = 30


def display_set_name(text: str) -> str:
    raw = " ".join((text or "").replace(SET_NAME_SEP, " ").split())
    if not raw:
        return ""
    return raw[:SET_NAME_MAX]


def parse_set_names(raw: str | None) -> list[str]:
    """Split stored ``set`` / ``set_name`` into membership labels."""
    text = (raw or "").strip()
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in text.split(SET_NAME_SEP):
        label = display_set_name(part)
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out


def encode_set_names(names: Sequence[str]) -> str:
    """Join unique set labels for the roster ``set`` field."""
    return SET_NAME_SEP.join(parse_set_names(SET_NAME_SEP.join(names)))
