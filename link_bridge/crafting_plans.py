"""Local per-card crafting plans (booru link lists). Client-only; not a server note."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from link_bridge.config import app_dir

PLANS_NAME = "crafting_plans.json"
PRESET_SECTIONS: tuple[tuple[str, str], ...] = (
    ("author", "Author"),
    ("author_m", "Author M"),
    ("g", "G"),
    ("s", "S"),
    ("q", "Q"),
    ("e", "E"),
    ("g_m", "G M"),
    ("s_m", "S M"),
    ("q_m", "Q M"),
    ("e_m", "E M"),
    ("reshape", "Reshape"),
    ("reshape_m", "Reshape M"),
    ("slopify", "Slopify"),
    ("slopify_m", "Slopify M"),
    ("portal", "Portal"),
    ("title", "Title"),
)
RATING_SECTION_IDS = frozenset({"g", "s", "q", "e", "g_m", "s_m", "q_m", "e_m"})
_R34_HIDE_EMPTY = RATING_SECTION_IDS
_DANBOORU_HIDE_EMPTY = frozenset({"reshape"})
_SEARCH_SKIP = frozenset({"solo", "-solo", "1girl", "1boy", "1other"})
_RATING_SOLO = {"g": "g", "s": "s", "q": "q", "e": "e"}
_RATING_MULTI = {"g": "g_m", "s": "s_m", "q": "q_m", "e": "e_m"}
_PRESET_IDS = {pid for pid, _title in PRESET_SECTIONS}
_PRESET_TITLES = {pid: title for pid, title in PRESET_SECTIONS}

_DANBOORU_POST = re.compile(r"/posts/(\d+)", re.I)
_HTTP_RE = re.compile(r"^https?://", re.I)


@dataclass
class PlanSection:
    id: str
    title: str
    preset: bool
    urls: list[str] = field(default_factory=list)

    def remaining(self) -> list[str]:
        return list(self.urls)


@dataclass
class ConsumeResult:
    removed: list[str]
    one_left: list[tuple[str, str]]  # (section title, remaining display URL)
    completed: list[str]  # section titles that just went to zero
    changed: bool


def plans_path() -> Path:
    return app_dir() / PLANS_NAME


def canonical_post_url(raw: str) -> str:
    """Stable identity for a booru post URL (Danbooru ??? Rule34)."""
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except Exception:
        return text.split("#", 1)[0].rstrip("/").lower()
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host.endswith("donmai.us"):
        m = _DANBOORU_POST.search(path)
        if m:
            return f"https://danbooru.donmai.us/posts/{m.group(1)}"
    if host.endswith("rule34.xxx"):
        qs = parse_qs(parsed.query or "")
        ids = qs.get("id") or []
        post_id = str(ids[0] or "").strip() if ids else ""
        if post_id.isdigit():
            return f"https://rule34.xxx/index.php?page=post&s=view&id={post_id}"
    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    clean_path = path.rstrip("/") or "/"
    return urlunparse((scheme, netloc, clean_path, "", "", ""))


def preview_label(url: str) -> str:
    key = canonical_post_url(url)
    if "/posts/" in key:
        return "#" + key.rsplit("/", 1)[-1]
    if "id=" in key:
        return "#" + key.rsplit("id=", 1)[-1]
    text = (url or "").strip()
    if len(text) > 28:
        return text[:12] + "???" + text[-12:]
    return text


def parse_url_lines(text: str) -> list[str]:
    """Keep first occurrence of each canonical HTTP(S) URL; skip junk lines."""
    seen: set[str] = set()
    out: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not _HTTP_RE.match(line):
            continue
        line = line.split()[0]
        key = canonical_post_url(line)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def urls_to_text(urls: list[str]) -> str:
    return "\n".join(u.strip() for u in urls if str(u).strip())


def norm_tag(raw: str) -> str:
    return (raw or "").strip().lower().replace(" ", "_")


def tags_match(a: str, b: str) -> bool:
    na, nb = norm_tag(a), norm_tag(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na.startswith(nb + "_(") or nb.startswith(na + "_("):
        return True
    return na.split("_(")[0] == nb.split("_(")[0]


def any_tag_overlap(left: list[str] | set[str], right: list[str] | set[str]) -> bool:
    for a in left:
        for b in right:
            if tags_match(a, b):
                return True
    return False


def normalize_rating(raw: str) -> str:
    text = (raw or "").strip().lower()
    if text in ("g", "general"):
        return "g"
    if text in ("s", "sensitive", "safe"):
        return "s"
    if text in ("q", "questionable"):
        return "q"
    if text in ("e", "explicit"):
        return "e"
    if len(text) == 1 and text in "gsqe":
        return text
    return ""


def _search_tags(url: str) -> list[str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return []
    qs = parse_qs(parsed.query or "")
    raw = " ".join(str(x) for x in (qs.get("tags") or []))
    parts = [norm_tag(p) for p in raw.replace("+", " ").split() if p.strip()]
    return [p for p in parts if p]


def card_is_r34(state: dict[str, Any] | None) -> bool:
    st = state or {}
    post = str(st.get("post_url") or "").lower()
    if "rule34" in post:
        return True
    for row in st.get("buttons") or []:
        specs = row if isinstance(row, list) else [row]
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            op = str(spec.get("op") or "")
            arg = str(spec.get("arg") or "").strip()
            if op in ("sl", "sm"):
                return True
            if op == "rs" and not arg:
                return True
            blob = " ".join(
                [str(spec.get("url") or "")]
                + [str(u) for u in (spec.get("urls") or [])]
            ).lower()
            if "rule34" in blob:
                return True
    return False


def card_identity_from_omni_state(state: dict[str, Any] | None) -> tuple[set[str], set[str], bool]:
    """Artist tags, character tags, and whether the card is R34/hell."""
    st = state or {}
    artists: set[str] = set()
    characters: set[str] = set()
    name = norm_tag(str(st.get("name") or ""))
    if name and name not in {"unknown", "?", "animated"}:
        characters.add(name)
    is_r34 = card_is_r34(st)
    for row in st.get("buttons") or []:
        specs = row if isinstance(row, list) else [row]
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            urls = [str(spec.get("url") or "")]
            urls.extend(str(u) for u in (spec.get("urls") or []))
            for url in urls:
                parts = _search_tags(url)
                if not parts:
                    continue
                content = [
                    p
                    for p in parts
                    if p not in _SEARCH_SKIP and not p.startswith("rating:")
                ]
                if not content:
                    continue
                if any(p.startswith("rating:") for p in parts):
                    characters.update(content)
                elif "solo" in parts or "-solo" in parts:
                    artists.add(content[0])
    return artists, characters, is_r34


def classify_plan_section(
    *,
    card_artists: list[str] | set[str],
    card_characters: list[str] | set[str],
    card_is_r34: bool,
    post_artists: list[str] | set[str],
    post_characters: list[str] | set[str],
    post_solo: bool,
    post_rating: str = "",
) -> str | None:
    """Pick a preset section id for a browser post, or None if it does not fit."""
    if any_tag_overlap(card_artists, post_artists):
        return "author" if post_solo else "author_m"
    if not any_tag_overlap(card_characters, post_characters):
        return None
    if card_is_r34:
        return "reshape" if post_solo else "reshape_m"
    rating = normalize_rating(post_rating)
    table = _RATING_SOLO if post_solo else _RATING_MULTI
    if rating in table:
        return table[rating]
    return "s" if post_solo else "reshape_m"


def editor_shows_section(sec: PlanSection, *, is_r34: bool) -> bool:
    if sec.urls or not sec.preset:
        return True
    if is_r34:
        return sec.id not in _R34_HIDE_EMPTY
    return sec.id not in _DANBOORU_HIDE_EMPTY


def _preset_section(pid: str, urls: list[str] | None = None) -> PlanSection:
    return PlanSection(
        id=pid,
        title=_PRESET_TITLES[pid],
        preset=True,
        urls=list(urls or []),
    )


def new_custom_id(title: str, used: set[str]) -> str:
    """Stable custom section id that does not collide with presets or *used*."""
    return _custom_id(title, used)


def _custom_id(title: str, used: set[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:40]
    if not slug:
        slug = "custom"
    base = f"custom:{slug}"
    cid = base
    n = 2
    while cid in used or cid in _PRESET_IDS:
        cid = f"{base}-{n}"
        n += 1
    return cid


def empty_card_sections() -> list[PlanSection]:
    return [_preset_section(pid) for pid, _title in PRESET_SECTIONS]


def _sections_from_raw(raw: Any) -> list[PlanSection]:
    by_id: dict[str, list[str]] = {}
    custom: list[PlanSection] = []
    if isinstance(raw, dict):
        items = raw.get("sections")
    elif isinstance(raw, list):
        items = raw
    else:
        items = None
    if not isinstance(items, list):
        items = []
    used_ids = set(_PRESET_IDS)
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        urls = parse_url_lines("\n".join(str(u) for u in (item.get("urls") or []) if str(u).strip()))
        if sid in _PRESET_IDS:
            by_id[sid] = urls
            continue
        if not title:
            continue
        if not sid.startswith("custom:"):
            sid = _custom_id(title, used_ids)
        while sid in used_ids:
            sid = _custom_id(f"{title}-{len(used_ids)}", used_ids)
        used_ids.add(sid)
        custom.append(PlanSection(id=sid, title=title[:48], preset=False, urls=urls))
    out = [_preset_section(pid, by_id.get(pid) or []) for pid, _title in PRESET_SECTIONS]
    out.extend(custom)
    return out


def card_has_links(sections: list[PlanSection]) -> bool:
    return any(s.urls for s in sections)


class CraftingPlanStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else plans_path()
        self._cards: dict[str, list[PlanSection]] = {}
        self.load()

    def load(self) -> None:
        self._cards = {}
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(raw, dict):
            return
        cards = raw.get("cards")
        if not isinstance(cards, dict):
            return
        for key, body in cards.items():
            try:
                cid = str(int(key))
            except (TypeError, ValueError):
                continue
            sections = _sections_from_raw(body)
            if card_has_links(sections):
                self._cards[cid] = sections

    def _dump(self) -> dict[str, Any]:
        cards: dict[str, Any] = {}
        for cid, sections in self._cards.items():
            payload = []
            for sec in sections:
                if not sec.urls:
                    continue
                payload.append(
                    {
                        "id": sec.id,
                        "title": sec.title,
                        "preset": bool(sec.preset),
                        "urls": list(sec.urls),
                    }
                )
            if payload:
                cards[cid] = {"sections": payload}
        return {"version": 1, "cards": cards}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self._dump(), indent=2, ensure_ascii=False) + "\n"
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(self.path)

    def get_sections(self, char_id: int) -> list[PlanSection]:
        key = str(int(char_id))
        existing = self._cards.get(key)
        if existing is None:
            return empty_card_sections()
        copies = [
            PlanSection(
                id=sec.id,
                title=sec.title,
                preset=sec.preset,
                urls=list(sec.urls),
            )
            for sec in existing
        ]
        by_id = {s.id: s for s in copies if s.preset}
        ordered = [by_id.get(pid) or _preset_section(pid) for pid, _t in PRESET_SECTIONS]
        custom = [s for s in copies if not s.preset]
        return ordered + custom

    def visible_sections(self, char_id: int) -> list[PlanSection]:
        return [s for s in self.get_sections(int(char_id)) if s.urls]

    def put_sections(self, char_id: int, sections: list[PlanSection]) -> None:
        key = str(int(char_id))
        cleaned: list[PlanSection] = []
        used = set(_PRESET_IDS)
        for sec in sections:
            urls = parse_url_lines("\n".join(sec.urls))
            if sec.preset:
                if sec.id not in _PRESET_IDS:
                    continue
                cleaned.append(_preset_section(sec.id, urls))
                continue
            title = (sec.title or "").strip()[:48]
            if not title or not urls:
                continue
            sid = sec.id if str(sec.id).startswith("custom:") else _custom_id(title, used)
            while sid in used:
                sid = _custom_id(f"{title}-{len(used)}", used)
            used.add(sid)
            cleaned.append(PlanSection(id=sid, title=title, preset=False, urls=urls))
        # keep preset order
        by_id = {s.id: s for s in cleaned if s.preset}
        ordered = [_preset_section(pid, (by_id.get(pid).urls if pid in by_id else [])) for pid, _t in PRESET_SECTIONS]
        custom = [s for s in cleaned if not s.preset]
        final = ordered + custom
        if card_has_links(final):
            self._cards[key] = final
        else:
            self._cards.pop(key, None)
        self.save()

    def consume_reached_post(self, char_id: int, post_url: str) -> ConsumeResult:
        key = canonical_post_url(post_url)
        if not key:
            return ConsumeResult(removed=[], one_left=[], completed=[], changed=False)
        sections = self.get_sections(int(char_id))
        if not card_has_links(sections):
            return ConsumeResult(removed=[], one_left=[], completed=[], changed=False)
        removed: list[str] = []
        one_left: list[tuple[str, str]] = []
        completed: list[str] = []
        changed = False
        for sec in sections:
            before = list(sec.urls)
            if not before:
                continue
            kept = [u for u in before if canonical_post_url(u) != key]
            if len(kept) == len(before):
                continue
            changed = True
            dropped = [u for u in before if canonical_post_url(u) == key]
            removed.extend(dropped)
            if len(before) >= 2 and len(kept) == 1:
                one_left.append((sec.title, kept[0]))
            if len(before) >= 1 and len(kept) == 0:
                completed.append(sec.title)
            sec.urls = kept
        if changed:
            self.put_sections(int(char_id), sections)
        return ConsumeResult(
            removed=removed, one_left=one_left, completed=completed, changed=changed
        )

    def append_url(self, char_id: int, section_id: str, url: str) -> str:
        """Add *url* to *section_id*. Returns the section title. Raises ValueError."""
        target_id = str(section_id or "").strip()
        if target_id not in _PRESET_IDS and not target_id.startswith("custom:"):
            raise ValueError("unknown plan field")
        lines = parse_url_lines(url)
        if not lines:
            raise ValueError("not a post URL")
        add = lines[0]
        key = canonical_post_url(add)
        sections = self.get_sections(int(char_id))
        hit = next((s for s in sections if s.id == target_id), None)
        if hit is None:
            if target_id in _PRESET_IDS:
                hit = _preset_section(target_id, [])
                sections.append(hit)
            else:
                raise ValueError("unknown plan field")
        if any(canonical_post_url(u) == key for u in hit.urls):
            return hit.title
        hit.urls.append(add)
        self.put_sections(int(char_id), sections)
        return hit.title


_STORE: CraftingPlanStore | None = None


def get_store() -> CraftingPlanStore:
    global _STORE
    if _STORE is None:
        _STORE = CraftingPlanStore()
    return _STORE


def reset_store_for_tests(path: Path | None = None) -> CraftingPlanStore:
    global _STORE
    _STORE = CraftingPlanStore(path)
    return _STORE
