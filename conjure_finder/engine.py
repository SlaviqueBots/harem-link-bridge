"""Find the cheapest conjure/beckon query that can hit a specific post.

Mirrors bot conjure rules (max 2 tags, 25/50 pricing, 1 free reroll,
cross-session same-tag pity) and /beckon (single general tag, 10 peeks,
fixed cost) without changing any bot code. Imports pricing helpers and API
clients read-only.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from conjure_finder.urls import ParsedPostUrl, parse_post_url

ProgressCb = Callable[[str], None]
CancelCheck = Callable[[], bool]

Source = Literal["danbooru", "rule34"]
HellMode = Literal["hell", "slop", ""]

# Free reroll ⇒ pool size 1 or 2 is a single-session guarantee.
# Cross-session pity (same tags) skips already-seen posts until the pool
# reshuffles — expected cost uses without-replacement across sessions.
GUARANTEE_POOL_MAX = 2

# /beckon shows up to this many images; pool ≤ this is a single-beckon guarantee.
# Keep in sync with bot BECKON_PRICE / BECKON_MAX_POSTS (live bot on Koara).
BECKON_PRICE = 30
BECKON_PEEKS = 10

MAX_GENERAL_FOR_PAIRS = 30
MAX_PAIR_CHECKS_PER_TIER = 100
MAX_CROSS_CHECKS_PER_TIER = 120
# Always pair these rarest tags with every tag in the pair pool — catches
# small intersections that plain min(count) ranking would push past the cap.
FORCE_PAIR_ANCHORS = 5
# Cap how many beckonable singles we live-count (rarest first).
MAX_BECKON_CHECKS = 40

# Bot @refine / /refine — fixed exclude-tag crafts on Author/reshape.
# Mirror live bot REFINE_EXCLUDE_TAGS (reshape.py). ALL (mega) is skipped here:
# it needs ~6 counts to pick an API exclude, then client-side multi-tag reject,
# so pool math is unreliable and search time blows up for little gain over a
# single targeted −tag when the post lacks that trash tag.
REFINE_EXCLUDE_TAGS: tuple[str, ...] = (
    "greyscale",
    "simple_background",
    "white_background",
    "monochrome",
    "comic",
    "gradient_background",
)
# Live refine probes: top roster anchors × excludes the target lacks.
MAX_REFINE_ANCHORS = 2
MAX_REFINE_CHECKS = 12

# _probe pool sentinels (live counts are always >= 0).
_POOL_CANCELLED = -1
_POOL_SKIP = -2
_POOL_FAILED = -3


@dataclass(frozen=True)
class PricedTag:
    name: str
    category: int | None
    price: int
    kind: str
    post_count: int


@dataclass(frozen=True)
class ConjureOption:
    tags: tuple[str, ...]
    cost: int
    pool_size: int
    guaranteed: bool
    command: str
    hell_mode: HellMode
    expected_sessions: float
    expected_currency: float
    note: str = ""
    # conjure | beckon | author | reshape | reshape_m | refine
    path: str = "conjure"


@dataclass
class PostMeta:
    """Extra post fields for roster paths (author / reshape)."""

    rating: str = "e"
    has_solo: bool = False
    is_ai: bool = False
    artists: list[PricedTag] = field(default_factory=list)
    characters: list[PricedTag] = field(default_factory=list)


@dataclass
class FindResult:
    source: Source
    post_id: int
    file_ext: str
    warnings: list[str] = field(default_factory=list)
    best: ConjureOption | None = None
    guaranteed: bool = False
    checked: int = 0
    tags_on_post: int = 0
    elapsed_sec: float = 0.0
    post_ids: tuple[int, ...] = ()


def _progress(cb: ProgressCb | None, msg: str) -> None:
    if cb:
        cb(msg)


def _cancelled(check: CancelCheck | None) -> bool:
    return bool(check and check())


def _comb(n: int, k: int) -> int:
    if k < 0 or n < 0 or k > n:
        return 0
    if k == 0 or k == n:
        return 1
    k = min(k, n - k)
    out = 1
    for i in range(k):
        out = out * (n - i) // (i + 1)
    return out


def _hit_probability(pool: int, hits: int = 1) -> float:
    """P(see ≥1 acceptable post in one conjure + free reroll)."""
    if pool <= 0 or hits <= 0:
        return 0.0
    hits = min(hits, pool)
    if pool <= GUARANTEE_POOL_MAX:
        return 1.0
    peeks = GUARANTEE_POOL_MAX
    miss = _comb(pool - hits, peeks)
    total = _comb(pool, peeks)
    if total <= 0:
        return 1.0
    return 1.0 - (miss / float(total))


def _expected_sessions(pool: int, hits: int = 1) -> float:
    """Expected paid sessions until any of ``hits`` acceptable posts appears.

    Bot pity (same tags): unseen posts only, then reshuffle when exhausted.
    Each session shows up to ``GUARANTEE_POOL_MAX`` posts (conjure + free reroll).
    For ``hits == 1`` this matches the closed-form uniform-position formula.
    """
    if pool <= 0 or hits <= 0:
        return float("inf")
    hits = min(hits, pool)
    if pool <= GUARANTEE_POOL_MAX:
        return 1.0
    draws = GUARANTEE_POOL_MAX
    if hits == 1:
        full_groups = pool // draws
        rem = pool % draws
        if rem == 0:
            return (full_groups + 1) / 2.0
        m = full_groups
        return ((m + 1) / float(pool)) * (draws * m / 2.0 + rem)

    # E[ceil(M/draws)] where M = min rank among ``hits`` uniform positions.
    total = _comb(pool, hits)
    if total <= 0:
        return float("inf")
    exp = 0.0
    for m in range(1, pool - hits + 2):
        ways = _comb(pool - m, hits - 1)
        if ways <= 0:
            continue
        sessions = (m + draws - 1) // draws
        exp += (ways / float(total)) * sessions
    return exp


def _expected_beckon_sessions(
    pool: int, hits: int = 1, peeks: int = BECKON_PEEKS
) -> float:
    """Expected paid beckons until any of ``hits`` acceptable posts appears.

    Each beckon shows ``min(peeks, pool)`` distinct images. A miss keeps one
    non-target image (excluded from later beckons of the same tag); images
    shown but not kept can appear again. When ``pool <= peeks`` the whole
    pool is shown → single-session guarantee.
    """
    if pool <= 0 or hits <= 0:
        return float("inf")
    hits = min(hits, pool)
    if peeks <= 0:
        return float("inf")
    peeks_cap = min(peeks, pool)
    if pool <= peeks_cap:
        return 1.0

    # E(n) = 1 + P(miss|n) * E(n-1); E(n)=1 when n <= peeks or a hit is forced.
    e = 1.0
    for n in range(peeks_cap + 1, pool + 1):
        if peeks_cap > n - hits:
            p_miss = 0.0
        elif hits == 1:
            p_miss = (n - peeks_cap) / float(n)
        else:
            total = _comb(n, peeks_cap)
            miss = _comb(n - hits, peeks_cap)
            p_miss = (miss / float(total)) if total > 0 else 0.0
        e = 1.0 + p_miss * e
    return e


def _expected_action_clicks(
    action_pool: int,
    *,
    peeks: int = GUARANTEE_POOL_MAX,
    hits: int = 1,
    conjure_pool: int | None = None,
) -> tuple[float, bool]:
    """Extra Author/reshape clicks after one conjure (+ free reroll peeks).

    ``conjure_pool`` is what `/conjure tag` draws from. Author uses the same
    pool as the action; reshape uses the full character tag while the action
    filters to solo/−solo + rating — peeks must not be treated as coming from
    the filtered pool (that caused false “conjure alone” guarantees).

    Returns ``(expected_clicks, guaranteed_by_conjure_alone)``.
    """
    if action_pool <= 0 or hits <= 0:
        return float("inf"), False
    hits = min(hits, action_pool)
    if conjure_pool is None or conjure_pool <= 0:
        conjure_pool = action_pool
    hits_in_conjure = min(hits, conjure_pool)

    # Only the unfiltered conjure pool can be covered by conjure + free reroll.
    if conjure_pool <= peeks:
        return 0.0, True

    p_hit = _hit_probability(conjure_pool, hits_in_conjure)
    p_miss = 1.0 - p_hit
    if p_miss <= 0:
        return 0.0, True

    same_pool = conjure_pool == action_pool
    if same_pool:
        # Author-style: peeks already removed from the action pool.
        rem = max(action_pool - peeks, 0)
        e_if_miss = (rem + 1) / float(hits + 1) if rem > 0 else 0.0
    else:
        # Reshape-style: conjure peeks were from the full character tag, so the
        # filtered action pool is still intact. One post per paid click.
        e_if_miss = (action_pool + 1) / float(hits + 1)
    return p_miss * e_if_miss, False


def _option_rank_key(opt: ConjureOption) -> tuple:
    """Lower is better. Prefer cheaper, then guarantees, then author paths."""
    path_bias = {
        "author": 0,
        "reshape": 1,
        "reshape_m": 1,
        # Refine is the same craft family as reshape/Author, just with −exclude.
        "refine": 1,
        "beckon": 2,
        "conjure": 3,
    }.get(opt.path, 4)
    return (
        opt.expected_currency,
        0 if opt.guaranteed else 1,
        path_bias,
        opt.pool_size,
        opt.tags,
    )


def _better_option(a: ConjureOption | None, b: ConjureOption | None) -> ConjureOption | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if _option_rank_key(a) <= _option_rank_key(b) else b


def _conjure_guarantee_beats(cost: int, current: ConjureOption | None) -> bool:
    """True if a pool≤2 conjure guarantee at ``cost`` would beat ``current``."""
    if current is None:
        return True
    hypo = ConjureOption(
        tags=(),
        cost=cost,
        pool_size=1,
        guaranteed=True,
        command="",
        hell_mode="",
        expected_sessions=1.0,
        expected_currency=float(cost),
        path="conjure",
    )
    return _option_rank_key(hypo) < _option_rank_key(current)


def _bot_command(source: Source, tags: list[str], hell_mode: HellMode) -> str:
    joined = " ".join(tags)
    if source == "danbooru":
        return f"/conjure {joined}"
    if hell_mode == "slop":
        return f"/conjure_hell_slop {joined}"
    return f"/conjure_hell {joined}"


def _beckon_command(source: Source, tag: str, hell_mode: HellMode) -> str:
    # Bot only registers /beckon and /beckon_hell (no _slop). Hell draws use
    # build_r34_query (−ai_generated), so AI posts are not beckon targets.
    if source == "danbooru":
        return f"/beckon {tag}"
    return f"/beckon_hell {tag}"


def _is_beckonable(tag: PricedTag) -> bool:
    """Bot validate_general: category 0 only, no cosplay tags."""
    if tag.category != 0:
        return False
    if "(cosplay)" in tag.name:
        return False
    return True

def _danbooru_preview_url(raw: dict) -> str:
    """Best static thumb URL from a posts/{id}.json payload."""
    from bot.services.danbooru import _abs_url

    preview = _abs_url(raw.get("preview_file_url"))
    large = _abs_url(raw.get("large_file_url"))
    file_url = _abs_url(raw.get("file_url"))
    for url in (preview, large, file_url):
        if not url:
            continue
        ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
        if ext not in ("gif", "mp4", "webm", "zip", "swf"):
            return url
    return preview or large or file_url or ""


async def _httpx_get_retry(
    client,
    path: str,
    *,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
    label: str = "",
    attempts: int = 4,
):
    """GET with retries — Danbooru often drops mid-batch TLS when clients churn."""
    import httpx

    timeout = httpx.Timeout(30.0, connect=12.0)
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        if _cancelled(cancel_check):
            raise ValueError("Search cancelled.")
        try:
            return await client.get(path, timeout=timeout)
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.RemoteProtocolError,
            httpx.PoolTimeout,
        ) as exc:
            last = exc
            if attempt >= attempts:
                break
            wait = 0.7 * attempt
            _progress(
                progress,
                f"{label} network glitch ({type(exc).__name__}) — "
                f"retry {attempt}/{attempts} in {wait:.1f}s…",
            )
            await asyncio.sleep(wait)
    raise ValueError(
        f"{label or path} failed after {attempts} tries"
        + (f": {last}" if last else ".")
    )


async def _load_danbooru_tags(
    post_id: int,
    progress: ProgressCb | None,
    cancel_check: CancelCheck | None,
    *,
    client=None,
) -> tuple[list[PricedTag], str, list[str], PostMeta, str]:
    from bot.services.conjure_pricing import conjure_price_for_tag, conjure_tag_kind_label
    from bot.services.danbooru import DanbooruClient
    from bot.utils.booru_tags import is_meta_search_tag

    warnings: list[str] = []
    from conjure_finder.settings import auth_snapshot

    if not auth_snapshot()["danbooru"]:
        raise ValueError(
            "Danbooru API keys missing — open Settings…, enter login username + API key, Save."
        )

    own_client = client is None
    if own_client:
        client = DanbooruClient()
        await client.start()
    try:
        assert client._client
        # Confirm the HTTP client actually got auth (guards against stale CFG bugs).
        if client._client.auth is None:
            raise ValueError(
                "Danbooru credentials did not apply — re-open Settings…, Save, and retry."
            )
        from bot.core.rate_limit import DANBOORU_RL

        await DANBOORU_RL.acquire()
        r = await _httpx_get_retry(
            client._client,
            f"/posts/{int(post_id)}.json",
            progress=progress,
            cancel_check=cancel_check,
            label=f"Danbooru #{post_id}",
        )
        if r.status_code == 401:
            raise ValueError(
                "Danbooru 401 Unauthorized — use your Danbooru login name (not email) "
                "and a valid API key in Settings…, then Save and try again."
            )
        if r.status_code == 403:
            raise ValueError(
                "Danbooru 403 Forbidden — check username/API key in Settings…"
            )
        if r.status_code != 200:
            raise ValueError(f"Danbooru post {post_id} not found (HTTP {r.status_code}).")
        raw = r.json()
        file_ext = (raw.get("file_ext") or "").lower()
        preview_url = _danbooru_preview_url(raw)
        if file_ext in ("webm", "swf", "zip"):
            warnings.append(
                f"This post is .{file_ext}. The bot cannot deliver webm/swf/zip via /conjure — "
                "pick another format if possible."
            )

        buckets: list[tuple[str, int]] = []
        for tag in (raw.get("tag_string_general") or "").split():
            buckets.append((tag, 0))
        for tag in (raw.get("tag_string_meta") or "").split():
            buckets.append((tag, 5))
        for tag in (raw.get("tag_string_artist") or "").split():
            buckets.append((tag, 1))
        for tag in (raw.get("tag_string_copyright") or "").split():
            buckets.append((tag, 3))
        for tag in (raw.get("tag_string_character") or "").split():
            buckets.append((tag, 4))

        unique_buckets: list[tuple[str, int]] = []
        seen: set[str] = set()
        for name, cat_hint in buckets:
            name = name.strip().lower()
            if not name or name in seen or is_meta_search_tag(name):
                continue
            seen.add(name)
            unique_buckets.append((name, cat_hint))

        names = [n for n, _ in unique_buckets]
        _progress(
            progress,
            f"Pricing {len(names)} tags on #{post_id} (batch lookup)…",
        )
        if _cancelled(cancel_check):
            warnings.append("Cancelled while loading tags.")
            return [], file_ext, warnings, PostMeta(), preview_url

        # One HTTP call for ≤100 tags (chunked) — not one request per tag.
        info_map = await client.tag_info_many(names)
        priced: list[PricedTag] = []
        for name, cat_hint in unique_buckets:
            info = info_map.get(name)
            category = cat_hint
            count = 0
            if info:
                count = int(info.get("count") or 0)
                try:
                    api_cat = int(info.get("category", cat_hint))
                    if api_cat != cat_hint:
                        category = api_cat
                except (TypeError, ValueError):
                    pass
            price = conjure_price_for_tag(name, category)
            kind = conjure_tag_kind_label(name, category, price)
            post_count = count if count > 0 else 10_000_000
            priced.append(
                PricedTag(
                    name=name,
                    category=category,
                    price=price,
                    kind=kind,
                    post_count=post_count,
                )
            )
        _progress(progress, f"Priced {len(priced)} tags on #{post_id}.")
        rating = (raw.get("rating") or "e").lower()[:1]
        if rating not in ("g", "s", "q", "e"):
            rating = "e"
        general = set((raw.get("tag_string_general") or "").lower().split())
        meta = PostMeta(
            rating=rating,
            has_solo="solo" in general,
            artists=[t for t in priced if t.category == 1],
            characters=[t for t in priced if t.category == 4],
        )
        return priced, file_ext, warnings, meta, preview_url
    finally:
        if own_client:
            await client.stop()


async def _load_rule34_tags(
    post_id: int, progress: ProgressCb | None, cancel_check: CancelCheck | None
) -> tuple[list[PricedTag], str, list[str], HellMode, PostMeta, str]:
    from bot.services.conjure_pricing import conjure_price_for_tag, conjure_tag_kind_label
    from bot.services.rule34 import Rule34Client
    from bot.utils.booru_tags import is_meta_search_tag

    warnings: list[str] = []
    client = Rule34Client()
    await client.start()
    try:
        if not client.api_ready:
            raise ValueError("Rule34 API keys missing in .env (RULE34_API_KEY / RULE34_USER_ID).")
        post = await client.fetch_by_id(post_id, allow_ai=True)
        if not post:
            raise ValueError(f"Rule34 post {post_id} not found.")

        preview_url = (getattr(post, "preview_url", None) or "").strip()
        if not preview_url:
            for url in (
                getattr(post, "sample_url", None),
                getattr(post, "image_url", None),
                getattr(post, "file_url", None),
            ):
                if url and str(url).strip():
                    preview_url = str(url).strip()
                    break

        tag_names = [t for t in (post.tags or "").split() if t]
        if not tag_names:
            tag_names = list(
                dict.fromkeys(
                    list(post.character_tag_candidates)
                    + list(post.artist_tags)
                    + list(post.copyright_tags)
                    + list(post.general_tags)
                    + list(post.meta_tags)
                )
            )
        is_ai = "ai_generated" in tag_names
        hell_mode: HellMode = "slop" if is_ai else "hell"
        if is_ai:
            warnings.append("Post is AI — use /conjure_hell_slop (bot adds ai_generated).")
        else:
            warnings.append("Non-AI Rule34 — bot query adds -ai_generated (and -guro when room).")

        file_ext = ""
        for url in (post.file_url, post.image_url, post.sample_url):
            if url and "." in url.split("?")[0]:
                file_ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
                break
        if file_ext in ("webm", "swf", "zip"):
            warnings.append(
                f"This post looks like .{file_ext}. Delivery may fail the same way as in the bot."
            )

        import html
        from urllib.parse import unquote_plus

        def _norm_tag(raw: str) -> str:
            name = html.unescape(unquote_plus(raw.strip())).lower().replace(" ", "_")
            if "&#" in name or "&amp;" in name:
                name = html.unescape(name).replace(" ", "_")
            return name

        unique: list[str] = []
        seen: set[str] = set()
        for raw_name in tag_names:
            name = _norm_tag(raw_name)
            if not name or name in seen or is_meta_search_tag(name):
                continue
            if name in ("ai_generated", "-ai_generated", "-guro"):
                continue
            seen.add(name)
            unique.append(name)

        total = len(unique)
        _progress(progress, f"Pricing {total} tags…")
        from bot.core.config import CONJURE_PRICE_GENERAL

        # Rule34 drops/empties responses under burst load — keep this modest.
        sem = asyncio.Semaphore(4)
        stop = asyncio.Event()

        char_cands = {
            _norm_tag(c)
            for c in (getattr(post, "character_tag_candidates", ()) or ())
            if c
        }
        ct = (getattr(post, "character_tag", None) or "").strip()
        if ct and ct.lower() not in ("unknown", "?"):
            char_cands.add(_norm_tag(ct))
        artist_tags = {
            _norm_tag(a)
            for a in (getattr(post, "artist_tags", ()) or ())
            if a
        }
        if getattr(post, "artist_tag", None):
            at = _norm_tag(post.artist_tag)
            if at and at.lower() not in ("unknown", "?"):
                artist_tags.add(at)
        copyright_tags = {
            _norm_tag(c)
            for c in (getattr(post, "copyright_tags", ()) or ())
            if c
        }
        meta_tags = {
            _norm_tag(m)
            for m in (getattr(post, "meta_tags", ()) or ())
            if m
        }

        async def _one(name: str) -> PricedTag | None:
            if _cancelled(cancel_check) or stop.is_set():
                return None
            async with sem:
                if _cancelled(cancel_check) or stop.is_set():
                    return None
                row = await client.tag_index_row(name)
                cat: int | None = None
                count = 0
                if row:
                    try:
                        cat = int(row.get("type", row.get("category", 0)))
                    except (TypeError, ValueError):
                        cat = 0
                    try:
                        count = int(row.get("count") or 0)
                    except (TypeError, ValueError):
                        count = 0
                # HTML sidebars are authoritative when the API miss-categorizes
                # (or when apostrophe/entity mangling made the index miss).
                if name in char_cands:
                    cat = 4
                elif name in artist_tags:
                    cat = 1
                elif name in copyright_tags:
                    cat = 3
                elif name in meta_tags:
                    cat = 5
                elif cat is None:
                    cat = 0
                if count <= 0:
                    count = 10_000_000
                price = conjure_price_for_tag(name, cat)
                kind = conjure_tag_kind_label(name, cat, price)
                return PricedTag(
                    name=name,
                    category=cat,
                    price=price,
                    kind=kind,
                    post_count=count,
                )

        tasks = [asyncio.create_task(_one(n), name=f"r34-tag:{n}") for n in unique]
        priced: list[PricedTag] = []
        done = 0
        for fut in asyncio.as_completed(tasks):
            try:
                row = await fut
            except asyncio.CancelledError:
                continue
            done += 1
            if row is None:
                continue
            priced.append(row)
            if done == 1 or done == total or done % 5 == 0:
                _progress(progress, f"Pricing tags… {done}/{total}")
            if 0 < row.post_count <= GUARANTEE_POOL_MAX and row.price <= CONJURE_PRICE_GENERAL:
                stop.set()
                for t in tasks:
                    if not t.done():
                        t.cancel()
                _progress(
                    progress,
                    f"Solo guarantee while pricing: {row.name} (pool={row.post_count}).",
                )
                break

        if _cancelled(cancel_check):
            warnings.append("Cancelled while loading tags.")
        uniq: dict[str, PricedTag] = {t.name: t for t in priced}
        priced_list = list(uniq.values())
        artist_names = {normalize for normalize in (
            (a or "").strip().lower().replace(" ", "_") for a in artist_tags
        ) if normalize}
        char_names = {
            (c or "").strip().lower().replace(" ", "_") for c in char_cands if c
        }
        from bot.services.rule34 import normalize_r34_rating_letter

        meta = PostMeta(
            rating=normalize_r34_rating_letter(getattr(post, "rating", None)),
            has_solo="solo" in seen,
            is_ai=is_ai,
            artists=[
                t
                for t in priced_list
                if t.category == 1 or t.name in artist_names
            ],
            characters=[
                t
                for t in priced_list
                if t.category == 4 or t.name in char_names
            ],
        )
        return priced_list, file_ext, warnings, hell_mode, meta, preview_url
    finally:
        await client.stop()


def _estimate_pool(tags: list[PricedTag]) -> int:
    if len(tags) == 1:
        return tags[0].post_count
    return min(t.post_count for t in tags)


def _pair_sort_key(
    a: PricedTag, b: PricedTag, *, rarer_second: bool = False
) -> tuple:
    """Sort so likely small intersections are probed first.

    When ``rarer_second`` (premium×general crosses), prefer the rarer general
    even if ``min(counts)`` is dominated by the character's large count —
    otherwise ``cow_print_*`` sorts next to ``shoulder_blush`` and can win
    the race before a pool-1 guarantee is checked.
    """
    mn = min(a.post_count, b.post_count)
    if rarer_second:
        return (mn, b.post_count, a.post_count, b.name, a.name)
    return (mn, max(a.post_count, b.post_count), a.name, b.name)


def _ordered_pair(a: PricedTag, b: PricedTag) -> list[PricedTag]:
    return [a, b] if a.name <= b.name else [b, a]


def _collect_pairs(
    left: list[PricedTag],
    right: list[PricedTag],
    *,
    max_pairs: int,
    force_anchors: int,
    same_side: bool,
    rarer_second: bool = False,
    force_left_full: list[PricedTag] | None = None,
    right_full: list[PricedTag] | None = None,
) -> list[tuple[int, list[PricedTag]]]:
    """Build tag pairs, forcing coverage of the rarest anchors.

    Plain ``min(post_count)`` ranking misses small intersections like
    ``cuffed`` (678) ∩ ``mole_on_ass`` (3503) = 2 when many rarer×rarer
    pairs fill the check budget first — or when a flaky count leaves only
    a worse pair. Anchors guarantee the rarest tags are fully crossed.

    ``force_left_full`` × ``right_full`` always includes every character/artist
    paired with every general on the post (guarantee hunting).
    """
    by_key: dict[tuple[str, str], tuple[tuple, list[PricedTag]]] = {}

    def add(a: PricedTag, b: PricedTag) -> None:
        if a.name == b.name:
            return
        key = tuple(sorted((a.name, b.name)))
        if key in by_key:
            return
        tags = _ordered_pair(a, b)
        est = _pair_sort_key(a, b, rarer_second=rarer_second)
        by_key[key] = (est, tags)

    # Character/artist × every general — do this first so they survive the cap.
    if force_left_full and right_full:
        for a in force_left_full:
            for b in right_full:
                add(a, b)

    anchors = left[:force_anchors] if force_anchors else []
    for a in anchors:
        for b in right:
            add(a, b)

    ranked: list[tuple[PricedTag, PricedTag]] = []
    if same_side:
        for i, a in enumerate(left):
            for b in left[i + 1 :]:
                ranked.append((a, b))
    else:
        for a in left:
            for b in right:
                ranked.append((a, b))
    ranked.sort(key=lambda ab: _pair_sort_key(ab[0], ab[1], rarer_second=rarer_second))

    for a, b in ranked:
        if len(by_key) >= max_pairs:
            break
        add(a, b)

    rows = sorted(by_key.values(), key=lambda row: (row[0], row[1][0].name, row[1][1].name))
    # For premium×general, probe rarer generals first (est = general count).
    if rarer_second:
        return [(int(est[1]), tags) for est, tags in rows]
    return [(int(est[0]), tags) for est, tags in rows]


def _iter_candidates(
    priced: list[PricedTag],
) -> tuple[
    list[tuple[int, int, list[PricedTag]]],
    list[tuple[int, int, list[PricedTag]]],
]:
    """Return (main_pairs_and_small_singles, deferred_large_singles)."""
    from bot.core.config import CONJURE_PRICE_GENERAL, CONJURE_PRICE_PREMIUM

    generals = [t for t in priced if t.price == CONJURE_PRICE_GENERAL]
    premiums = [t for t in priced if t.price == CONJURE_PRICE_PREMIUM]
    out: list[tuple[int, int, list[PricedTag]]] = []

    for t in generals:
        out.append((CONJURE_PRICE_GENERAL, t.post_count, [t]))

    for t in premiums:
        out.append((CONJURE_PRICE_PREMIUM, t.post_count, [t]))

    all_g = sorted(generals, key=lambda t: (t.post_count, t.name))
    rare_g = all_g[:MAX_GENERAL_FOR_PAIRS]
    rare_p = sorted(premiums, key=lambda t: (t.post_count, t.name))
    # Always cross every character/artist with every general on the post.
    force_chars_artists = [t for t in rare_p if t.category in (1, 4)]
    if not force_chars_artists:
        force_chars_artists = list(rare_p)

    general_pair_cost = CONJURE_PRICE_GENERAL * 2
    for est, tags in _collect_pairs(
        rare_g,
        rare_g,
        max_pairs=MAX_PAIR_CHECKS_PER_TIER,
        force_anchors=FORCE_PAIR_ANCHORS,
        same_side=True,
    ):
        out.append((general_pair_cost, est, tags))

    cross_cost = CONJURE_PRICE_GENERAL + CONJURE_PRICE_PREMIUM
    # Cap must fit character×all generals (often ~50) plus other crosses.
    cross_cap = max(MAX_CROSS_CHECKS_PER_TIER, len(force_chars_artists) * max(len(all_g), 1) + 40)
    for est, tags in _collect_pairs(
        rare_p,
        rare_g,
        max_pairs=cross_cap,
        force_anchors=FORCE_PAIR_ANCHORS,
        same_side=False,
        rarer_second=True,
        force_left_full=force_chars_artists,
        right_full=all_g,
    ):
        out.append((cross_cost, est, tags))

    premium_pair_cost = CONJURE_PRICE_PREMIUM * 2
    for est, tags in _collect_pairs(
        rare_p,
        rare_p,
        max_pairs=MAX_PAIR_CHECKS_PER_TIER,
        force_anchors=FORCE_PAIR_ANCHORS,
        same_side=True,
    ):
        out.append((premium_pair_cost, est, tags))

    out.sort(key=lambda row: (row[0], row[1], row[2][0].name))
    seen: set[tuple[str, ...]] = set()
    # Large metadata singles are deferred (see _search_targets). Probing dozens
    # of them before pairs made live Danbooru searches take many minutes.
    main: list[tuple[int, int, list[PricedTag]]] = []
    deferred: list[tuple[int, int, list[PricedTag]]] = []
    for cost, est, tags in out:
        key = tuple(sorted(t.name for t in tags))
        if key in seen:
            continue
        seen.add(key)
        if len(tags) == 1 and tags[0].post_count > GUARANTEE_POOL_MAX:
            deferred.append((cost, est, tags))
        else:
            main.append((cost, est, tags))
    return main, deferred


class _CountSession:
    """Reuse one API client for the whole search."""

    # Local tool: hundreds of count probes. Bot DANBOORU_RL burst-backs off to
    # 2.5s/req which turns a 2-minute search into 10+. Keep a mild floor only.
    _finder_interval = 0.2
    _finder_lock: asyncio.Lock | None = None
    _finder_last = 0.0

    def __init__(self, source: Source, hell_mode: HellMode) -> None:
        self.source = source
        self.hell_mode = hell_mode
        self._danbooru = None
        self._rule34 = None

    @classmethod
    async def _finder_acquire(cls) -> None:
        if cls._finder_lock is None:
            cls._finder_lock = asyncio.Lock()
        async with cls._finder_lock:
            now = time.time()
            wait = cls._finder_interval - (now - cls._finder_last)
            if wait > 0:
                await asyncio.sleep(wait)
            cls._finder_last = time.time()

    async def start(self) -> None:
        if self.source == "danbooru":
            from bot.services.danbooru import DanbooruClient

            self._danbooru = DanbooruClient()
            await self._danbooru.start()
        else:
            from bot.services.rule34 import Rule34Client

            self._rule34 = Rule34Client()
            await self._rule34.start()

    async def aclose(self) -> None:
        if self._danbooru:
            await self._danbooru.stop()
            self._danbooru = None
        if self._rule34:
            await self._rule34.stop()
            self._rule34 = None

    async def __call__(self, tags: list[str]) -> int:
        if self.source == "danbooru":
            from bot.services.danbooru import conjure_danbooru_query

            return await self.count_query(conjure_danbooru_query(tags))

        from bot.utils.r34_tags import build_r34_query, build_r34_slopify_query

        raw = " ".join(tags)
        query = (
            build_r34_slopify_query(raw)
            if self.hell_mode == "slop"
            else build_r34_query(raw)
        )
        return await self.count_query(query)

    async def count_query(self, query: str) -> int:
        """Count posts for an arbitrary search string (conjure or reshape pools)."""
        if self.source == "danbooru":
            assert self._danbooru
            assert self._danbooru._client
            for attempt in range(5):
                try:
                    await self._finder_acquire()
                    r = await self._danbooru._client.get(
                        "/counts/posts.json", params={"tags": query}
                    )
                    if r.status_code != 200:
                        await asyncio.sleep(0.35 * (attempt + 1))
                        continue
                    n = int(r.json().get("counts", {}).get("posts", 0) or 0)
                    if 0 < n <= GUARANTEE_POOL_MAX:
                        from bot.services.danbooru import is_danbooru_media_visible

                        await self._finder_acquire()
                        pr = await self._danbooru._client.get(
                            "/posts.json",
                            params={"tags": query, "limit": min(n, GUARANTEE_POOL_MAX)},
                        )
                        if pr.status_code == 200:
                            payload = pr.json()
                            items = (
                                [payload]
                                if isinstance(payload, dict) and payload.get("id")
                                else (payload or [])
                            )
                            accessible = sum(
                                1 for raw in items if is_danbooru_media_visible(raw)
                            )
                            if accessible > 0:
                                return accessible
                    return n
                except Exception:
                    await asyncio.sleep(0.35 * (attempt + 1))
            return -1

        assert self._rule34
        last = 0
        for attempt in range(3):
            sample = await self._rule34._api_search(
                query, pid=0, limit=100, allow_ai=True
            )
            last = len(sample)
            if last > 0 or attempt == 2:
                return last
            await asyncio.sleep(0.35 * (attempt + 1))
        return last


@dataclass
class TargetSnap:
    """One acceptable post in an any-of group."""

    post_id: int
    priced: list[PricedTag]
    tag_set: set[str]
    file_ext: str
    meta: PostMeta
    hell_mode: HellMode
    warnings: list[str] = field(default_factory=list)
    preview_url: str = ""
    page_url: str = ""


def post_page_url(source: Source, post_id: int) -> str:
    if source == "danbooru":
        return f"https://danbooru.donmai.us/posts/{int(post_id)}"
    return f"https://rule34.xxx/index.php?page=post&s=view&id={int(post_id)}"


def _make_action_path_option(
    *,
    source: Source,
    hell_mode: HellMode,
    tag: str,
    pool: int,
    path: str,
    action_cost: int,
    action_label: str,
    hits: int,
    conjure_pool: int | None = None,
    owned: bool = False,
    option_tags: tuple[str, ...] | None = None,
) -> ConjureOption | None:
    """Build conjure-then-action (Author/reshape/refine) expected-cost option."""
    if pool <= 0 or hits <= 0:
        return None
    hits = min(hits, pool)
    tags = option_tags if option_tags is not None else (tag,)
    if owned:
        # Already on roster — only pay action clicks; no conjure peeks.
        e_clicks = (pool + 1) / float(hits + 1)
        guar = pool <= hits
        conjure_cost = 0
        e_cur = action_cost * e_clicks
        hit_note = f", {hits} acceptable" if hits > 1 else ""
        plan = f"(already have) {action_label} (~{e_clicks:.0f}× @{action_cost})"
        note = (
            f"Owned roster: skip conjure; {action_label} until any acceptable "
            f"(filter pool {pool}{hit_note})."
        )
        return ConjureOption(
            tags=tags,
            cost=conjure_cost,
            pool_size=pool,
            guaranteed=guar,
            command=plan,
            hell_mode=hell_mode,
            expected_sessions=e_clicks,
            expected_currency=e_cur,
            note=note,
            path=path,
        )
    e_clicks, guar = _expected_action_clicks(
        pool, hits=hits, conjure_pool=conjure_pool
    )
    if e_clicks == float("inf"):
        return None
    from bot.core.config import CONJURE_PRICE_PREMIUM

    conjure_cost = CONJURE_PRICE_PREMIUM
    e_cur = conjure_cost + action_cost * e_clicks
    cmd = _bot_command(source, [tag], hell_mode)
    hit_note = f", {hits} acceptable" if hits > 1 else ""
    if e_clicks <= 0:
        plan = cmd
        note = (
            f"Roster path ({path}): conjure alone covers pool {pool}"
            f"{hit_note} (free reroll)."
        )
    else:
        plan = f"{cmd}\nthen {action_label} (~{e_clicks:.0f}× @{action_cost})"
        cpool = conjure_pool if conjure_pool and conjure_pool > 0 else pool
        note = (
            f"Roster path: conjure {tag} (pool {cpool}), then {action_label} "
            f"until any acceptable shows (filter pool {pool}{hit_note})."
        )
    return ConjureOption(
        tags=tags,
        cost=conjure_cost,
        pool_size=pool,
        guaranteed=guar,
        command=plan,
        hell_mode=hell_mode,
        expected_sessions=1.0 + e_clicks,
        expected_currency=e_cur,
        note=note,
        path=path,
    )


def _refine_excludes_for_targets(targets: list[TargetSnap]) -> list[str]:
    """Exclude tags at least one target lacks (refine can still hit those)."""
    if not targets:
        return []
    return [
        ex
        for ex in REFINE_EXCLUDE_TAGS
        if any(ex not in t.tag_set for t in targets)
    ]


async def _evaluate_roster_paths(
    source: Source,
    hell_mode: HellMode,
    targets: list[TargetSnap],
    counter: _CountSession,
    *,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
    own_author: bool = False,
    own_character: bool = False,
) -> list[ConjureOption]:
    """Conjure artist/character once, then Author / reshape / reshape_m.

    ``hits`` = how many any-of targets that roster filter can still reach.
    When ``own_author`` / ``own_character`` is set, skip the 50 conjure cost
    (user already has that roster entry).
    """
    from bot.core.config import AUTHOR_COST, RESHAPE_COST
    from bot.utils.artist_tags import is_valid_artist_tag

    out: list[ConjureOption] = []

    # Dominant roster tags on this target set (majority / top frequency).
    # "Already have character/author" only discounts these, not one-off guests.
    artist_freq: dict[str, int] = {}
    char_freq: dict[str, int] = {}
    for tgt in targets:
        for a in {x.name for x in tgt.meta.artists}:
            artist_freq[a] = artist_freq.get(a, 0) + 1
        for c in {x.name for x in tgt.meta.characters}:
            char_freq[c] = char_freq.get(c, 0) + 1
    n_targets = max(len(targets), 1)
    majority = max(1, (n_targets + 1) // 2)

    def _dominant(freq: dict[str, int]) -> set[str]:
        if not freq:
            return set()
        best = max(freq.values())
        return {name for name, n in freq.items() if n >= majority or n == best}

    owned_artists = _dominant(artist_freq) if own_author else set()
    owned_characters = _dominant(char_freq) if own_character else set()

    def _mk(
        *,
        tag: str,
        pool: int,
        path: str,
        action_cost: int,
        action_label: str,
        hits: int,
        conjure_pool: int | None = None,
        option_tags: tuple[str, ...] | None = None,
    ) -> ConjureOption | None:
        owned = (path == "author" and own_author and tag in owned_artists) or (
            path in ("reshape", "reshape_m")
            and own_character
            and tag in owned_characters
        )
        # Ownership check uses underlying craft (author/reshape), not "refine".
        return _make_action_path_option(
            source=source,
            hell_mode=hell_mode,
            tag=tag,
            pool=pool,
            path=path,
            action_cost=action_cost,
            action_label=action_label,
            hits=hits,
            conjure_pool=conjure_pool,
            owned=owned,
            option_tags=option_tags,
        )

    # --- Author paths ---
    artist_map: dict[str, list[TargetSnap]] = {}
    for tgt in targets:
        for artist in tgt.meta.artists:
            if not is_valid_artist_tag(artist.name):
                continue
            artist_map.setdefault(artist.name, []).append(tgt)
    artists_ranked = sorted(
        artist_map.items(),
        key=lambda kv: (
            min(
                (a.post_count for t in kv[1] for a in t.meta.artists if a.name == kv[0]),
                default=10_000_000,
            ),
            kv[0],
        ),
    )
    for artist_name, artist_targets in artists_ranked[:8]:
        if _cancelled(cancel_check):
            break
        hits = len({t.post_id for t in artist_targets})
        _progress(progress, f"Roster path: Author via {artist_name} (hits={hits})…")
        pool = await counter.count_query(artist_name)
        if pool <= 0:
            counts = [
                a.post_count
                for t in artist_targets
                for a in t.meta.artists
                if a.name == artist_name and a.post_count < 10_000_000
            ]
            pool = min(counts) if counts else 0
        opt = _mk(
            tag=artist_name,
            pool=pool,
            path="author",
            action_cost=AUTHOR_COST,
            action_label="Author",
            hits=hits,
        )
        if opt:
            out.append(opt)

    # --- Character reshape / reshape_m ---
    char_names = sorted(
        {
            c.name
            for t in targets
            for c in t.meta.characters
        }
    )
    # Rank by rarest post_count among targets that have the char.
    def _char_count(name: str) -> int:
        vals = [
            c.post_count
            for t in targets
            for c in t.meta.characters
            if c.name == name
        ]
        return min(vals) if vals else 10_000_000

    for char_name in sorted(char_names, key=lambda n: (_char_count(n), n))[:8]:
        if _cancelled(cancel_check):
            break
        # Split targets that have this character by solo vs multi and rating.
        buckets: dict[tuple[str, str], list[TargetSnap]] = {}
        for tgt in targets:
            if char_name not in tgt.tag_set and not any(
                c.name == char_name for c in tgt.meta.characters
            ):
                continue
            path = "reshape" if tgt.meta.has_solo else "reshape_m"
            buckets.setdefault((path, tgt.meta.rating), []).append(tgt)

        for (path, rating), group in buckets.items():
            if _cancelled(cancel_check):
                break
            hits = len({t.post_id for t in group})
            if source == "danbooru":
                if path == "reshape":
                    query = f"solo {char_name} rating:{rating}"
                    label = f"reshape {rating.upper()}"
                else:
                    query = f"{char_name} -solo rating:{rating}"
                    label = f"reshape_m {rating.upper()}"
            else:
                from bot.services.rule34 import rating_query_tag
                from bot.utils.r34_tags import build_r34_query, build_r34_slopify_query

                rating_tag = rating_query_tag(rating)
                if path == "reshape":
                    raw = f"solo {char_name} {rating_tag}"
                    label = f"reshape {rating.upper()}"
                else:
                    raw = f"{char_name} -solo {rating_tag}"
                    label = f"reshape_m {rating.upper()}"
                query = (
                    build_r34_slopify_query(raw)
                    if hell_mode == "slop"
                    else build_r34_query(raw)
                )
            _progress(
                progress,
                f"Roster path: {label} via {char_name} (hits={hits})…",
            )
            # Full character tag = conjure draws; filtered query = reshape pool.
            conjure_pool = await counter.count_query(char_name)
            pool = await counter.count_query(query)
            if conjure_pool <= 0:
                counts = [
                    c.post_count
                    for t in group
                    for c in t.meta.characters
                    if c.name == char_name and c.post_count < 10_000_000
                ]
                conjure_pool = min(counts) if counts else 0
            opt = _mk(
                tag=char_name,
                pool=pool,
                path=path,
                action_cost=RESHAPE_COST,
                action_label=label,
                hits=hits,
                conjure_pool=conjure_pool,
            )
            if opt:
                out.append(opt)

    return out


async def _evaluate_refine_paths(
    source: Source,
    hell_mode: HellMode,
    targets: list[TargetSnap],
    counter: _CountSession,
    *,
    floor: float | None = None,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
    own_author: bool = False,
    own_character: bool = False,
) -> ConjureOption | None:
    """Targeted @refine: Author/reshape with one −exclude the targets lack.

    Skips ALL/mega refine (unreliable pool + many extra counts). Caps live
    probes so this pass stays cheaper than conjure tier search.
    """
    from bot.core.config import AUTHOR_COST, CONJURE_PRICE_PREMIUM, RESHAPE_COST
    from bot.utils.artist_tags import is_valid_artist_tag

    excludes = _refine_excludes_for_targets(targets)
    if not excludes:
        return None

    # Ownership: same dominant-tag rule as roster.
    artist_freq: dict[str, int] = {}
    char_freq: dict[str, int] = {}
    for tgt in targets:
        for a in {x.name for x in tgt.meta.artists}:
            artist_freq[a] = artist_freq.get(a, 0) + 1
        for c in {x.name for x in tgt.meta.characters}:
            char_freq[c] = char_freq.get(c, 0) + 1
    n_targets = max(len(targets), 1)
    majority = max(1, (n_targets + 1) // 2)

    def _dominant(freq: dict[str, int]) -> set[str]:
        if not freq:
            return set()
        best = max(freq.values())
        return {name for name, n in freq.items() if n >= majority or n == best}

    owned_artists = _dominant(artist_freq) if own_author else set()
    owned_characters = _dominant(char_freq) if own_character else set()

    # Rank rarest artists / characters; only probe top anchors.
    artist_map: dict[str, list[TargetSnap]] = {}
    for tgt in targets:
        for artist in tgt.meta.artists:
            if not is_valid_artist_tag(artist.name):
                continue
            artist_map.setdefault(artist.name, []).append(tgt)
    artists_ranked = sorted(
        artist_map.items(),
        key=lambda kv: (
            min(
                (a.post_count for t in kv[1] for a in t.meta.artists if a.name == kv[0]),
                default=10_000_000,
            ),
            kv[0],
        ),
    )

    def _char_count(name: str) -> int:
        vals = [
            c.post_count
            for t in targets
            for c in t.meta.characters
            if c.name == name
        ]
        return min(vals) if vals else 10_000_000

    char_names = sorted(
        {c.name for t in targets for c in t.meta.characters},
        key=lambda n: (_char_count(n), n),
    )

    # One rarest artist + one rarest character (or two of one kind if needed).
    anchors: list[tuple[str, str, list[TargetSnap]]] = []
    if artists_ranked:
        name, group = artists_ranked[0]
        anchors.append(("author", name, group))
    if char_names:
        name = char_names[0]
        group = [
            t
            for t in targets
            if name in t.tag_set
            or any(c.name == name for c in t.meta.characters)
        ]
        anchors.append(("character", name, group))
    # Fill remaining anchor slots with next-rarest of either kind.
    ai = 1
    ci = 1
    while len(anchors) < MAX_REFINE_ANCHORS:
        cand_a = artists_ranked[ai] if ai < len(artists_ranked) else None
        cand_c = char_names[ci] if ci < len(char_names) else None
        if cand_a is None and cand_c is None:
            break
        pick_artist = False
        if cand_a and cand_c:
            a_count = min(
                (
                    a.post_count
                    for t in cand_a[1]
                    for a in t.meta.artists
                    if a.name == cand_a[0]
                ),
                default=10_000_000,
            )
            pick_artist = a_count <= _char_count(cand_c)
        elif cand_a:
            pick_artist = True
        if pick_artist and cand_a:
            anchors.append(("author", cand_a[0], cand_a[1]))
            ai += 1
        elif cand_c:
            group = [
                t
                for t in targets
                if cand_c in t.tag_set
                or any(c.name == cand_c for c in t.meta.characters)
            ]
            anchors.append(("character", cand_c, group))
            ci += 1
        else:
            break

    best: ConjureOption | None = None
    checks = 0

    def _beats_floor(opt: ConjureOption) -> bool:
        if floor is None:
            return True
        return opt.expected_currency < floor - 1e-9

    for kind, tag_name, base_group in anchors:
        if _cancelled(cancel_check) or checks >= MAX_REFINE_CHECKS:
            break
        # Conjure draws the bare tag; refine only narrows the follow-up craft.
        conjure_pool = await counter.count_query(tag_name)
        checks += 1
        if conjure_pool <= 0:
            if kind == "character":
                counts = [
                    c.post_count
                    for t in base_group
                    for c in t.meta.characters
                    if c.name == tag_name and c.post_count < 10_000_000
                ]
            else:
                counts = [
                    a.post_count
                    for t in base_group
                    for a in t.meta.artists
                    if a.name == tag_name and a.post_count < 10_000_000
                ]
            conjure_pool = min(counts) if counts else 0

        for exclude in excludes:
            if _cancelled(cancel_check) or checks >= MAX_REFINE_CHECKS:
                break
            # Targets that still match this craft after −exclude.
            if kind == "author":
                hit_targets = [t for t in base_group if exclude not in t.tag_set]
                if not hit_targets:
                    continue
                hits = len({t.post_id for t in hit_targets})
                if source == "danbooru":
                    # Live bot: exclude eats solo → `{artist} -{neg}`.
                    query = f"{tag_name} -{exclude}"
                    craft_label = "Author"
                else:
                    # Live bot Author on r34 is solo + optional −exclude.
                    query = f"{tag_name} solo -{exclude}"
                    craft_label = "Author"
                owned = own_author and tag_name in owned_artists
                action_cost = AUTHOR_COST
            else:
                # Character refine — Danbooru drops solo (2-tag budget); r34 keeps it.
                buckets: dict[tuple[str, str], list[TargetSnap]] = {}
                for tgt in base_group:
                    if exclude in tgt.tag_set:
                        continue
                    if source == "danbooru":
                        # One pool per rating; solo/multi both included.
                        buckets.setdefault(("reshape", tgt.meta.rating), []).append(tgt)
                    else:
                        path = "reshape" if tgt.meta.has_solo else "reshape_m"
                        buckets.setdefault((path, tgt.meta.rating), []).append(tgt)
                for (path, rating), group in buckets.items():
                    if _cancelled(cancel_check) or checks >= MAX_REFINE_CHECKS:
                        break
                    hits = len({t.post_id for t in group})
                    if hits <= 0:
                        continue
                    if source == "danbooru":
                        query = f"{tag_name} -{exclude} rating:{rating}"
                        craft_label = f"reshape {rating.upper()}"
                    else:
                        from bot.services.rule34 import rating_query_tag
                        from bot.utils.r34_tags import (
                            build_r34_query,
                            build_r34_slopify_query,
                        )

                        rating_tag = rating_query_tag(rating)
                        if path == "reshape":
                            raw = f"solo {tag_name} {rating_tag} -{exclude}"
                            craft_label = f"reshape {rating.upper()}"
                        else:
                            raw = f"{tag_name} -solo {rating_tag} -{exclude}"
                            craft_label = f"reshape_m {rating.upper()}"
                        query = (
                            build_r34_slopify_query(raw)
                            if hell_mode == "slop"
                            else build_r34_query(raw)
                        )
                    # Floor prune: even a 1-click guarantee can't beat floor.
                    owned = own_character and tag_name in owned_characters
                    min_cur = (
                        float(RESHAPE_COST)
                        if owned
                        else float(CONJURE_PRICE_PREMIUM + RESHAPE_COST)
                    )
                    if floor is not None and min_cur >= floor - 1e-9:
                        continue
                    _progress(
                        progress,
                        f"Refine −{exclude}: {craft_label} via {tag_name}…",
                    )
                    pool = await counter.count_query(query)
                    checks += 1
                    if pool <= 0:
                        continue
                    label = f"refine −{exclude} → {craft_label}"
                    opt = _make_action_path_option(
                        source=source,
                        hell_mode=hell_mode,
                        tag=tag_name,
                        pool=pool,
                        path="refine",
                        action_cost=RESHAPE_COST,
                        action_label=label,
                        hits=hits,
                        conjure_pool=conjure_pool,
                        owned=owned,
                        option_tags=(tag_name, exclude),
                    )
                    if opt and _beats_floor(opt):
                        best = _better_option(best, opt)
                        if (
                            best
                            and best.guaranteed
                            and best.expected_currency
                            <= (0 if owned else CONJURE_PRICE_PREMIUM) + RESHAPE_COST + 1e-9
                        ):
                            return best
                continue  # author branch handled below; character done via buckets

            # Author branch (single query per exclude).
            min_cur = (
                float(AUTHOR_COST)
                if owned
                else float(CONJURE_PRICE_PREMIUM + AUTHOR_COST)
            )
            if floor is not None and min_cur >= floor - 1e-9:
                continue
            _progress(
                progress,
                f"Refine −{exclude}: Author via {tag_name}…",
            )
            pool = await counter.count_query(query)
            checks += 1
            if pool <= 0:
                continue
            label = f"refine −{exclude} → {craft_label}"
            opt = _make_action_path_option(
                source=source,
                hell_mode=hell_mode,
                tag=tag_name,
                pool=pool,
                path="refine",
                action_cost=action_cost,
                action_label=label,
                hits=hits,
                conjure_pool=conjure_pool,
                owned=owned,
                option_tags=(tag_name, exclude),
            )
            if opt and _beats_floor(opt):
                best = _better_option(best, opt)
                if best and best.guaranteed and best.expected_currency <= min_cur + 1e-9:
                    return best

    return best


async def _evaluate_beckon_paths(
    source: Source,
    hell_mode: HellMode,
    targets: list[TargetSnap],
    priced: list[PricedTag],
    counter: _CountSession,
    *,
    floor: float | None = None,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
) -> ConjureOption | None:
    """Single category-0 general tag beckon — up to 10 peeks, fixed cost.

    Bot has no /beckon_hell_slop; hell draws always −ai_generated, so skip AI posts.
    """
    if hell_mode == "slop":
        return None
    candidates = [
        t
        for t in priced
        if _is_beckonable(t) and _hits_for_tags(targets, [t.name]) > 0
    ]
    candidates.sort(key=lambda t: (t.post_count, t.name))
    candidates = candidates[:MAX_BECKON_CHECKS]
    if not candidates:
        return None

    best: ConjureOption | None = None
    for tag in candidates:
        if _cancelled(cancel_check):
            break
        hits = _hits_for_tags(targets, [tag.name])
        if hits <= 0:
            continue
        # Optimistic skip from metadata before burning a live count.
        est = tag.post_count if tag.post_count > 0 else 10_000_000
        if est >= 100:
            est = 100
        est_sessions = _expected_beckon_sessions(est, hits=min(hits, est))
        if floor is not None and BECKON_PRICE * est_sessions >= floor - 1e-9:
            # Rarer tags come first; once metadata can't beat floor, stop.
            if est > BECKON_PEEKS:
                break
            continue
        _progress(progress, f"Checking beckon: {tag.name}…")
        pool = await counter([tag.name])
        if pool >= 100:
            pool = 100
        if pool <= 0:
            continue
        hits = min(hits, pool)
        sessions = _expected_beckon_sessions(pool, hits=hits)
        e_cur = BECKON_PRICE * sessions
        if floor is not None and e_cur >= floor - 1e-9:
            if best is not None and e_cur >= best.expected_currency - 1e-9:
                continue
        guaranteed = pool <= BECKON_PEEKS
        hit_note = f" ({hits} acceptable)" if hits > 1 else ""
        option = ConjureOption(
            tags=(tag.name,),
            cost=BECKON_PRICE,
            pool_size=pool,
            guaranteed=guaranteed,
            command=_beckon_command(source, tag.name, hell_mode),
            hell_mode=hell_mode,
            expected_sessions=sessions,
            expected_currency=e_cur,
            note=(
                f"Beckon guarantee: up to {BECKON_PEEKS} peeks cover the whole pool."
                if guaranteed
                else (
                    f"~{sessions:.1f} beckon sessions expected{hit_note} "
                    f"(keep excludes one miss; shown-but-not-kept can reappear)."
                )
            ),
            path="beckon",
        )
        prev = best
        best = _better_option(best, option)
        if best is not prev:
            _progress(
                progress,
                f"Best beckon: {tag.name} — pool {pool}{hit_note}, "
                f"~{option.expected_currency:.0f} currency expected.",
            )
        if guaranteed and (floor is None or e_cur < floor - 1e-9):
            # Cheapest beckon guarantee is always BECKON_PRICE; rarer tags first.
            break
        if floor is None or (best and best.expected_currency < floor):
            floor = best.expected_currency if best else floor
    return best


def _merge_priced(targets: list[TargetSnap]) -> list[PricedTag]:
    """Union of tags; keep rarest count and first-seen category/price."""
    best: dict[str, PricedTag] = {}
    for tgt in targets:
        for tag in tgt.priced:
            prev = best.get(tag.name)
            if prev is None or tag.post_count < prev.post_count:
                best[tag.name] = tag
            elif (
                prev is not None
                and tag.post_count == prev.post_count
                and tag.price < prev.price
            ):
                best[tag.name] = tag
    return list(best.values())


def _hits_for_tags(targets: list[TargetSnap], names: list[str]) -> int:
    need = set(names)
    return sum(1 for t in targets if need <= t.tag_set)


async def _load_target(
    parsed: ParsedPostUrl,
    progress: ProgressCb | None,
    cancel_check: CancelCheck | None,
    *,
    danbooru_client=None,
) -> TargetSnap:
    if parsed.source == "danbooru":
        _progress(progress, f"Loading Danbooru post #{parsed.post_id}…")
        priced, file_ext, warnings, meta, preview_url = await _load_danbooru_tags(
            parsed.post_id,
            progress,
            cancel_check,
            client=danbooru_client,
        )
        hell_mode: HellMode = ""
    else:
        _progress(progress, f"Loading Rule34 post #{parsed.post_id}…")
        priced, file_ext, warnings, hell_mode, meta, preview_url = await _load_rule34_tags(
            parsed.post_id, progress, cancel_check
        )
    if not priced:
        raise ValueError(f"No usable tags found on post #{parsed.post_id}.")
    return TargetSnap(
        post_id=parsed.post_id,
        priced=priced,
        tag_set={t.name for t in priced},
        file_ext=file_ext,
        meta=meta,
        hell_mode=hell_mode,
        warnings=list(warnings),
        preview_url=preview_url,
        page_url=post_page_url(parsed.source, parsed.post_id),
    )


async def _search_targets(
    source: Source,
    targets: list[TargetSnap],
    hell_mode: HellMode,
    *,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
) -> FindResult:
    post_ids = tuple(t.post_id for t in targets)
    warnings: list[str] = []
    for t in targets:
        warnings.extend(t.warnings)
    if len(targets) > 1:
        warnings.append(
            f"Any-of group: success = any of {len(targets)} posts "
            f"({', '.join(f'#{i}' for i in post_ids)})."
        )

    priced = _merge_priced(targets)
    result = FindResult(
        source=source,
        post_id=post_ids[0],
        post_ids=post_ids,
        file_ext=targets[0].file_ext,
        warnings=warnings,
        tags_on_post=len(priced),
    )

    # Fast path: cheap single-tag conjure guarantee that covers ≥1 target.
    # Only early-exit when cost beats beckon (general 25 < 30); premium solo
    # guarantees still compete with beckon in the full search.
    # Always live-count — metadata pool≤2 can be stale (false guarantee).
    solo_hits = [t for t in priced if 0 < t.post_count <= GUARANTEE_POOL_MAX]
    if solo_hits:
        solo_hits.sort(key=lambda t: (t.price, t.post_count, t.name))
        for pick in solo_hits:
            if pick.price >= BECKON_PRICE:
                continue
            if _hits_for_tags(targets, [pick.name]) <= 0:
                continue
            _progress(progress, f"Verifying solo guarantee: {pick.name}…")
            counter = _CountSession(source, hell_mode)
            await counter.start()
            try:
                verified = await counter([pick.name])
            finally:
                await counter.aclose()
            if verified <= 0:
                continue
            if verified > GUARANTEE_POOL_MAX:
                continue
            pool = verified
            opt = ConjureOption(
                tags=(pick.name,),
                cost=pick.price,
                pool_size=pool,
                guaranteed=True,
                command=_bot_command(source, [pick.name], hell_mode),
                hell_mode=hell_mode,
                expected_sessions=1.0,
                expected_currency=float(pick.price),
                note="Guarantee: 1 conjure + free reroll covers the whole pool.",
                path="conjure",
            )
            result.best = opt
            result.guaranteed = True
            result.checked = 1
            _progress(
                progress,
                f"Solo guarantee: {pick.name} (pool={pool}, cost={pick.price}). Done.",
            )
            return result

    counter = _CountSession(source, hell_mode)
    await counter.start()
    roster_best: ConjureOption | None = None
    beckon_best: ConjureOption | None = None
    conjure_best: ConjureOption | None = None
    try:
        _progress(progress, "Checking Author / reshape roster paths…")
        roster_opts = await _evaluate_roster_paths(
            source,
            hell_mode,
            targets,
            counter,
            progress=progress,
            cancel_check=cancel_check,
        )
        for opt in roster_opts:
            roster_best = _better_option(roster_best, opt)
        if roster_best:
            _progress(
                progress,
                f"Best roster path: {roster_best.path} — "
                f"~{roster_best.expected_currency:.0f} cur "
                f"(pool {roster_best.pool_size}).",
            )

        # Targeted refine after plain roster — floor prune keeps it cheap.
        floor_for_refine = (
            roster_best.expected_currency if roster_best is not None else None
        )
        _progress(progress, "Checking refine paths (−exclude crafts)…")
        refine_best = await _evaluate_refine_paths(
            source,
            hell_mode,
            targets,
            counter,
            floor=floor_for_refine,
            progress=progress,
            cancel_check=cancel_check,
        )
        if refine_best:
            result.checked += 1
            roster_best = _better_option(roster_best, refine_best)
            _progress(
                progress,
                f"Best refine: {' '.join(refine_best.tags)} — "
                f"~{refine_best.expected_currency:.0f} cur "
                f"(pool {refine_best.pool_size}).",
            )

        floor_for_beckon = (
            roster_best.expected_currency if roster_best is not None else None
        )
        _progress(progress, "Checking beckon paths (sparse general tags)…")
        beckon_best = await _evaluate_beckon_paths(
            source,
            hell_mode,
            targets,
            priced,
            counter,
            floor=floor_for_beckon,
            progress=progress,
            cancel_check=cancel_check,
        )
        if beckon_best:
            result.checked += 1
            _progress(
                progress,
                f"Best beckon: {' '.join(beckon_best.tags)} — "
                f"~{beckon_best.expected_currency:.0f} cur "
                f"(pool {beckon_best.pool_size}).",
            )

        candidates, deferred_singles = _iter_candidates(priced)
        _progress(
            progress,
            f"Checking up to {len(candidates)} conjure combos "
            f"(+{len(deferred_singles)} deferred singles; cheapest first; stop on guarantee)…",
        )

        best_non_guarantee: ConjureOption | None = None
        failed_count_probes = 0
        tiers: list[tuple[int, list[tuple[int, list[PricedTag]]]]] = []
        for cost, _est, tag_objs in candidates:
            if tiers and tiers[-1][0] == cost:
                tiers[-1][1].append((_est, tag_objs))
            else:
                tiers.append((cost, [(_est, tag_objs)]))

        sem = asyncio.Semaphore(8 if source == "danbooru" else 3)

        def _floor_opt() -> ConjureOption | None:
            return _better_option(
                _better_option(best_non_guarantee, roster_best), beckon_best
            )

        def _option_from_probe(
            tag_objs: list[PricedTag],
            cost: int,
            pool: int,
            hits: int,
        ) -> ConjureOption:
            hits = min(hits, pool)
            names = [t.name for t in tag_objs]
            guaranteed = pool <= GUARANTEE_POOL_MAX
            sessions = _expected_sessions(pool, hits=hits)
            hit_note = f" ({hits} acceptable)" if hits > 1 else ""
            return ConjureOption(
                tags=tuple(names),
                cost=cost,
                pool_size=pool,
                guaranteed=guaranteed,
                command=_bot_command(source, names, hell_mode),
                hell_mode=hell_mode,
                expected_sessions=sessions,
                expected_currency=cost * sessions,
                note=(
                    "Guarantee: 1 conjure + free reroll covers the whole pool."
                    if guaranteed
                    else (
                        f"~{sessions:.1f} conjure sessions expected"
                        f"{hit_note} (free reroll + same-tag pity)."
                    )
                ),
                path="conjure",
            )

        def _consider(
            option: ConjureOption,
            *,
            guarantee: ConjureOption | None,
        ) -> ConjureOption | None:
            nonlocal best_non_guarantee
            label = " ".join(option.tags)
            if option.guaranteed:
                if guarantee is None or (
                    option.pool_size,
                    option.expected_currency,
                    option.tags,
                ) < (
                    guarantee.pool_size,
                    guarantee.expected_currency,
                    guarantee.tags,
                ):
                    guarantee = option
                    _progress(
                        progress,
                        f"Found guarantee: {label} (pool={option.pool_size}, "
                        f"cost={option.cost}). Stopping this tier.",
                    )
                return guarantee
            if (
                best_non_guarantee is None
                or option.expected_currency
                < best_non_guarantee.expected_currency - 1e-9
                or (
                    abs(
                        option.expected_currency
                        - best_non_guarantee.expected_currency
                    )
                    < 1e-9
                    and (
                        option.pool_size < best_non_guarantee.pool_size
                        or (
                            option.pool_size == best_non_guarantee.pool_size
                            and option.expected_sessions
                            < best_non_guarantee.expected_sessions
                        )
                    )
                )
            ):
                best_non_guarantee = option
                _progress(
                    progress,
                    f"Best so far: {label} — pool {option.pool_size}, "
                    f"~{option.expected_currency:.0f} currency expected.",
                )
            return guarantee

        async def _probe(
            cost: int, est: int, tag_objs: list[PricedTag]
        ) -> tuple[list[PricedTag], int, int, int, int]:
            """Return (tags, cost, est, pool, hits). pool <0 = sentinel."""
            names = [t.name for t in tag_objs]
            hits = _hits_for_tags(targets, names)
            if hits <= 0:
                return tag_objs, cost, est, _POOL_SKIP, 0
            floor_opt = _floor_opt()
            floor = floor_opt.expected_currency if floor_opt is not None else None
            # Skip only when even a live pool≤2 guarantee cannot beat floor.
            # Never trust metadata est here — inflated counts skipped real
            # small pools under the old cost*E[sessions(est)] check.
            if (
                floor is not None
                and len(tag_objs) == 1
                and est > GUARANTEE_POOL_MAX
                and cost >= floor - 1e-9
            ):
                return tag_objs, cost, est, _POOL_SKIP, hits
            async with sem:
                if _cancelled(cancel_check):
                    return tag_objs, cost, est, _POOL_CANCELLED, hits
                pool = await counter(names)
                if pool < 0:
                    return tag_objs, cost, est, _POOL_FAILED, hits
                if pool >= 100:
                    pool = 100
                return tag_objs, cost, est, pool, hits

        for cost, rows in tiers:
            if _cancelled(cancel_check):
                result.warnings.append("Search cancelled.")
                break
            # Skip only when even a live pool≤2 guarantee at this cost cannot
            # beat the current floor. Equal expected currency still matters:
            # a cost-75 guarantee beats a cost-50 pool-4 (~75 cur) non-guarantee.
            floor_opt = _floor_opt()
            if not _conjure_guarantee_beats(cost, floor_opt):
                beat = floor_opt.expected_currency if floor_opt else cost
                _progress(
                    progress,
                    f"Skipping conjure cost {cost}+ — already ~{beat:.0f} cur.",
                )
                conjure_best = best_non_guarantee
                break
            _progress(
                progress,
                f"Checking cost {cost} ({len(rows)} combos, rarest first)…",
            )
            pending = [
                asyncio.create_task(_probe(cost, est, tag_objs))
                for est, tag_objs in rows
            ]
            guarantee: ConjureOption | None = None
            failed_rows: list[tuple[int, list[PricedTag]]] = []
            tier_done = 0
            tier_total = len(pending)
            try:
                for fut in asyncio.as_completed(pending):
                    try:
                        tag_objs, _cost, _est, pool, hits = await fut
                    except asyncio.CancelledError:
                        continue
                    tier_done += 1
                    if (
                        tier_done == 1
                        or tier_done == tier_total
                        or tier_done % 5 == 0
                    ):
                        floor_now = _floor_opt()
                        best_bit = (
                            f", best ~{floor_now.expected_currency:.0f} cur"
                            if floor_now is not None
                            else ""
                        )
                        _progress(
                            progress,
                            f"Cost {cost}: {tier_done}/{tier_total} combos"
                            f"{best_bit}…",
                        )
                    if pool == _POOL_SKIP:
                        continue
                    if pool == _POOL_FAILED:
                        failed_count_probes += 1
                        failed_rows.append((_est, tag_objs))
                        continue
                    if pool == _POOL_CANCELLED:
                        continue
                    result.checked += 1
                    if pool <= 0 or hits <= 0:
                        continue
                    option = _option_from_probe(tag_objs, cost, pool, hits)
                    prev = guarantee
                    guarantee = _consider(option, guarantee=guarantee)
                    if guarantee is not None and prev is None:
                        # Cancel rest of this cost tier. Do NOT do this for
                        # non-guarantees (pool 3) — that race missed cheaper
                        # pool≤2 paths still in flight.
                        for p in pending:
                            if not p.done():
                                p.cancel()
                        break
            finally:
                for p in pending:
                    if not p.done():
                        p.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

            # Flaky Danbooru counts used to return -1 and be skipped — that
            # left runner-ups like skin_fang+solipsist while bent_over+artist
            # (pool 1) never landed. Retry failures once before leaving the tier.
            if (
                guarantee is None
                and failed_rows
                and not _cancelled(cancel_check)
            ):
                _progress(
                    progress,
                    f"Retrying {len(failed_rows)} failed pool count(s) "
                    f"at cost {cost}…",
                )
                for est, tag_objs in failed_rows:
                    if _cancelled(cancel_check):
                        break
                    tag_objs, _cost, _est, pool, hits = await _probe(
                        cost, est, tag_objs
                    )
                    if pool == _POOL_FAILED:
                        # One more serial attempt outside the busy semaphore.
                        names = [t.name for t in tag_objs]
                        await asyncio.sleep(0.5)
                        if _cancelled(cancel_check):
                            break
                        pool = await counter(names)
                        if pool < 0:
                            continue
                        if pool >= 100:
                            pool = 100
                    elif pool in (_POOL_SKIP, _POOL_CANCELLED) or pool <= 0:
                        continue
                    if hits <= 0:
                        continue
                    result.checked += 1
                    option = _option_from_probe(tag_objs, cost, pool, hits)
                    guarantee = _consider(option, guarantee=guarantee)
                    if guarantee is not None:
                        break

            if guarantee is not None:
                conjure_best = _better_option(guarantee, best_non_guarantee)
                _progress(
                    progress,
                    f"Found conjure guarantee at cost {guarantee.cost}: "
                    f"{' '.join(guarantee.tags)} (pool={guarantee.pool_size}).",
                )
                break
        else:
            conjure_best = best_non_guarantee

        if failed_count_probes and not (
            conjure_best is not None and conjure_best.guaranteed
        ):
            result.warnings.append(
                f"{failed_count_probes} pool count(s) failed during search; "
                "result may be suboptimal — retry if this looks wrong."
            )

        # Deferred large singles: only those that could still beat current best.
        floor_opt = _floor_opt()
        floor_cur = floor_opt.expected_currency if floor_opt is not None else None
        if conjure_best is not None and (
            floor_cur is None or conjure_best.expected_currency < floor_cur
        ):
            floor_cur = conjure_best.expected_currency
        worth = [
            (c, e, t)
            for c, e, t in deferred_singles
            if floor_cur is None or c < floor_cur - 1e-9
        ]
        if worth and not _cancelled(cancel_check):
            msg = (
                f"Checking {len(worth)} deferred singles that could beat "
                f"~{floor_cur:.0f} cur…"
                if floor_cur is not None
                else f"Checking {len(worth)} deferred singles…"
            )
            _progress(progress, msg)
            worth.sort(key=lambda row: (row[0], row[1], row[2][0].name))
            pending = [
                asyncio.create_task(_probe(c, e, t)) for c, e, t in worth
            ]
            deferred_done = 0
            deferred_total = len(pending)
            try:
                for fut in asyncio.as_completed(pending):
                    try:
                        tag_objs, cost, _est, pool, hits = await fut
                    except asyncio.CancelledError:
                        continue
                    deferred_done += 1
                    if (
                        deferred_done == 1
                        or deferred_done == deferred_total
                        or deferred_done % 5 == 0
                    ):
                        _progress(
                            progress,
                            f"Deferred singles: {deferred_done}/{deferred_total}…",
                        )
                    if pool == _POOL_SKIP:
                        continue
                    result.checked += 1
                    if pool == _POOL_FAILED or pool == _POOL_CANCELLED or pool <= 0 or hits <= 0:
                        continue
                    hits = min(hits, pool)
                    names = [t.name for t in tag_objs]
                    guaranteed = pool <= GUARANTEE_POOL_MAX
                    sessions = _expected_sessions(pool, hits=hits)
                    option = ConjureOption(
                        tags=tuple(names),
                        cost=cost,
                        pool_size=pool,
                        guaranteed=guaranteed,
                        command=_bot_command(source, names, hell_mode),
                        hell_mode=hell_mode,
                        expected_sessions=sessions,
                        expected_currency=cost * sessions,
                        note=(
                            "Guarantee: 1 conjure + free reroll covers the whole pool."
                            if guaranteed
                            else (
                                f"~{sessions:.1f} conjure sessions expected "
                                f"(free reroll + same-tag pity)."
                            )
                        ),
                        path="conjure",
                    )
                    if guaranteed:
                        conjure_best = _better_option(conjure_best, option)
                        _progress(
                            progress,
                            f"Deferred single guarantee: {' '.join(names)} "
                            f"(pool={pool}, cost={cost}).",
                        )
                        for p in pending:
                            if not p.done():
                                p.cancel()
                        break
                    if (
                        best_non_guarantee is None
                        or option.expected_currency
                        < best_non_guarantee.expected_currency - 1e-9
                    ):
                        best_non_guarantee = option
                        conjure_best = _better_option(conjure_best, option)
            finally:
                for p in pending:
                    if not p.done():
                        p.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            conjure_best = _better_option(conjure_best, best_non_guarantee)
    finally:
        await counter.aclose()

    result.best = _better_option(
        _better_option(conjure_best, roster_best), beckon_best
    )
    if result.best is None:
        result.warnings.append("No matching tag combination returned any posts.")
    else:
        result.guaranteed = result.best.guaranteed
        if result.best.path == "beckon":
            result.warnings.append(
                "Cheapest route is /beckon (sparse general tag), not pure conjure."
            )
        elif result.best.path == "refine":
            result.warnings.append(
                "Cheapest route is @refine (roster craft with −exclude), not pure conjure."
            )
        elif result.best.path != "conjure":
            result.warnings.append(
                f"Cheapest route is roster path ({result.best.path}), not pure conjure."
            )
        elif not result.best.guaranteed:
            result.warnings.append(
                "No single-conjure guarantee found among checked combos; "
                "showing lowest expected spend."
            )
        if roster_best and conjure_best and result.best is conjure_best:
            _progress(
                progress,
                f"Picked conjure over roster (~{roster_best.expected_currency:.0f} cur).",
            )
        elif roster_best and result.best is roster_best:
            _progress(
                progress,
                f"Picked roster path over conjure "
                f"(~{getattr(conjure_best, 'expected_currency', float('inf')):.0f} cur).",
            )
        elif beckon_best and result.best is beckon_best:
            _progress(
                progress,
                f"Picked beckon over conjure/roster "
                f"(~{getattr(conjure_best, 'expected_currency', float('inf')):.0f} cur conjure).",
            )
    return result

async def find_cheapest_conjure(
    url: str,
    *,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
) -> FindResult:
    return await find_cheapest_any(
        [url], progress=progress, cancel_check=cancel_check
    )


async def find_cheapest_any(
    urls: list[str],
    *,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
) -> FindResult:
    """Cheapest path that can hit any post in ``urls`` (any-of group)."""
    t0 = time.perf_counter()
    if not urls:
        raise ValueError("No URLs in group.")
    parsed_list = [parse_post_url(u) for u in urls]
    sources = {p.source for p in parsed_list}
    if len(sources) > 1:
        raise ValueError(
            "Any-of group must be all Danbooru or all Rule34 — mixed sites in one line."
        )
    source = parsed_list[0].source
    # De-dupe by post id, keep order.
    uniq: list[ParsedPostUrl] = []
    seen_ids: set[int] = set()
    for p in parsed_list:
        if p.post_id in seen_ids:
            continue
        seen_ids.add(p.post_id)
        uniq.append(p)

    targets: list[TargetSnap] = []
    for p in uniq:
        if _cancelled(cancel_check):
            raise ValueError("Search cancelled.")
        targets.append(await _load_target(p, progress, cancel_check))

    hell_modes = {t.hell_mode for t in targets if t.hell_mode}
    if source == "rule34":
        if hell_modes == {"slop"}:
            hell_mode: HellMode = "slop"
        elif hell_modes == {"hell"}:
            hell_mode = "hell"
        elif "slop" in hell_modes and "hell" in hell_modes:
            hell_mode = "hell"
            for t in targets:
                t.warnings.append(
                    "Mixed AI / non-AI in any-of group — scoring with /conjure_hell "
                    "(non-AI). Prefer a same-mode group for AI posts."
                )
        else:
            hell_mode = "hell"
    else:
        hell_mode = ""

    result = await _search_targets(
        source,
        targets,
        hell_mode,
        progress=progress,
        cancel_check=cancel_check,
    )
    result.elapsed_sec = time.perf_counter() - t0
    return result


async def find_for_parsed(
    parsed: ParsedPostUrl,
    *,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
) -> FindResult:
    return await find_cheapest_any(
        [parsed.url], progress=progress, cancel_check=cancel_check
    )
