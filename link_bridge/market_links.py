"""Booru browser links for market lots (mirrors Omnicraft / @buy inspect)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

_RATINGS = ("g", "s", "q", "e")
_LABELS = {"g": "G", "s": "S", "q": "Q", "e": "E"}


def normalize_artist_tag(raw: object) -> str:
    tag = (str(raw or "")).strip().replace(" ", "_")
    if not tag or tag.lower() in ("unknown", "?", ""):
        return ""
    return tag


def format_artist_name(tag: str) -> str:
    raw = (tag or "").strip().replace("_", " ")
    return raw[:48] if raw else ""


def character_tag(item: dict[str, Any]) -> str:
    for key in ("canonical_tag", "character_tag"):
        tag = (str(item.get(key) or "")).strip().replace(" ", "_")
        if tag and tag.lower() not in ("unknown", "?", "", "animated"):
            return tag
    return ""


def is_hell_item(item: dict[str, Any]) -> bool:
    return (str(item.get("source") or "")).strip().lower() == "hell"


def post_button_label(item: dict[str, Any]) -> str:
    w = int(item.get("image_width") or 0)
    h = int(item.get("image_height") or 0)
    if w > 0 and h > 0:
        return f"{w}??{h}"
    if is_hell_item(item):
        return "??r34??"
    return "Post"


def _danbooru_posts_url(character_tag: str, rating: str, *, multi: bool = False) -> str:
    tag = (character_tag or "").strip().replace(" ", "_")
    base = f"{tag} -solo" if multi else f"solo {tag}"
    tags = f"{base} rating:{rating.lower()}"
    return f"https://danbooru.donmai.us/posts?tags={quote(tags)}"


def _client_browse_link_rows(item: dict[str, Any]) -> list[list[tuple[str, str]]]:
    """Fallback when server did not send browse_link_rows (older Koara)."""
    rows: list[list[tuple[str, str]]] = []
    post = (item.get("post_url") or "").strip()
    if post.startswith("http"):
        rows.append([("This post", post)])
    tag = character_tag(item)
    if not tag or is_hell_item(item):
        return rows
    solo = [(_LABELS[r], _danbooru_posts_url(tag, r)) for r in _RATINGS]
    if solo:
        rows.append(solo)
    multi = [(f"{_LABELS[r]}m", _danbooru_posts_url(tag, r, multi=True)) for r in _RATINGS]
    if multi:
        rows.append(multi)
    return rows


def browse_link_rows(item: dict[str, Any]) -> list[list[tuple[str, str]]]:
    """Omnicraft-style browse rows: This post, GSQE, Gm/Sm/???, r34 solo/m/slop."""
    raw = item.get("browse_link_rows")
    if isinstance(raw, list) and raw:
        out: list[list[tuple[str, str]]] = []
        for row in raw:
            if not isinstance(row, list):
                continue
            parsed: list[tuple[str, str]] = []
            for cell in row:
                if not isinstance(cell, dict):
                    continue
                label = str(cell.get("label") or "").strip()
                url = str(cell.get("url") or "").strip()
                if label and url.startswith("http"):
                    parsed.append((label, url))
            if parsed:
                out.append(parsed)
        if out:
            return out
    return _client_browse_link_rows(item)


def artist_solo_url(item: dict[str, Any]) -> str:
    tag = normalize_artist_tag(item.get("artist_tag"))
    if not tag:
        return ""
    if is_hell_item(item):
        return (
            "https://rule34.xxx/index.php?page=post&s=list&tags="
            + quote(f"{tag.lower()} solo")
        )
    return f"https://danbooru.donmai.us/posts?tags={quote(f'{tag} solo')}"


def artist_multi_url(item: dict[str, Any]) -> str:
    tag = normalize_artist_tag(item.get("artist_tag"))
    if not tag:
        return ""
    if is_hell_item(item):
        return (
            "https://rule34.xxx/index.php?page=post&s=list&tags="
            + quote(f"{tag.lower()} -solo")
        )
    return f"https://danbooru.donmai.us/posts?tags={quote(f'{tag} -solo')}"


def artist_open_urls(item: dict[str, Any]) -> list[str]:
    solo = (artist_solo_url(item) or "").strip()
    multi = (artist_multi_url(item) or "").strip()
    out: list[str] = []
    if solo:
        out.append(solo)
    if multi and multi != solo:
        out.append(multi)
    return out


def browser_link_specs(item: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, url) pairs for Post + author solo + author ???solo."""
    out: list[tuple[str, str]] = []
    post = (item.get("post_url") or "").strip()
    if post.startswith("http"):
        out.append((post_button_label(item), post))
    tag = normalize_artist_tag(item.get("artist_tag"))
    if tag:
        name = format_artist_name(tag) or "Author"
        solo = artist_solo_url(item)
        multi = artist_multi_url(item)
        if solo:
            out.append((name, solo))
        if multi and multi != solo:
            out.append((f"{name} ???solo", multi))
    return out


_GSQE_SOLO = frozenset({"G", "S", "Q", "E"})
_GSQE_MULTI = frozenset({"Gm", "Sm", "Qm", "Em"})
_R34_ROW = frozenset({"solo", "m", "slop", "mslop"})
_POST_ROW_CHUNK = 6


def _gsqe_bucket(label: str) -> str:
    """Classify a browse chip into post / gsqe / gsqe_m / r34."""
    text = (label or "").strip()
    low = text.lower()
    if low in _R34_ROW:
        return "r34"
    if text in _GSQE_SOLO:
        return "gsqe"
    if text in _GSQE_MULTI:
        return "gsqe_m"
    if len(text) >= 2 and text[0] in "GSQE" and text[1] == "m":
        return "gsqe_m"
    if len(text) >= 2 and text[0] in "GSQE" and text[1] == ":":
        return "gsqe"
    return "post"


def _dedupe_post_links(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """One button per URL; keep dimensions label over generic 'This post'."""
    by_url: dict[str, str] = {}
    order: list[str] = []
    for label, url in entries:
        u = (url or "").strip()
        if not u.startswith("http"):
            continue
        prev = by_url.get(u)
        if prev is None:
            by_url[u] = label
            order.append(u)
            continue
        if prev == "This post" and label != "This post":
            by_url[u] = label
    return [(by_url[u], u) for u in order]


def lot_link_grid_rows(item: dict[str, Any]) -> list[list[tuple[str, str, list[str]]]]:
    """Compact omni-style rows: (label, url, extra_urls) for equal-width grid."""
    post_entries: list[tuple[str, str]] = []
    gsqe: list[tuple[str, str]] = []
    gsqe_m: list[tuple[str, str]] = []
    r34: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _take(bucket: list[tuple[str, str]], label: str, url: str) -> None:
        u = (url or "").strip()
        if not u.startswith("http") or u in seen:
            return
        if bucket is post_entries:
            post_entries.append((label, u))
            return
        seen.add(u)
        bucket.append((label, u))

    for row in browse_link_rows(item):
        for label, url in row:
            kind = _gsqe_bucket(label)
            if kind == "post":
                _take(post_entries, label, url)
            elif kind == "gsqe":
                _take(gsqe, label, url)
            elif kind == "gsqe_m":
                _take(gsqe_m, label, url)
            else:
                _take(r34, label, url)

    for label, url in browser_link_specs(item):
        _take(post_entries, label, url)

    post_links = _dedupe_post_links(post_entries)
    for _lbl, u in post_links:
        seen.add(u)

    rows: list[list[tuple[str, str, list[str]]]] = []
    if post_links:
        flat: list[tuple[str, str, list[str]]] = [(lbl, u, []) for lbl, u in post_links]
        artist_urls = artist_open_urls(item)
        if len(artist_urls) > 1:
            flat.append(("author all", "", artist_urls))
        for i in range(0, len(flat), _POST_ROW_CHUNK):
            rows.append(flat[i : i + _POST_ROW_CHUNK])
    if gsqe:
        rows.append([(lbl, u, []) for lbl, u in gsqe])
    if gsqe_m:
        rows.append([(lbl, u, []) for lbl, u in gsqe_m])
    if r34:
        rows.append([(lbl, u, []) for lbl, u in r34])
    return rows
