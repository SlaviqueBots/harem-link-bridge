"""Bulk wishlist: rank cheap acquisition paths over many related posts.

Avoids dumping dozens of unrelated posts into a single any-of merge (which
explodes pair probes). Metadata-first clustering + capped live counts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from conjure_finder.engine import (
    BECKON_PEEKS,
    BECKON_PRICE,
    GUARANTEE_POOL_MAX,
    ConjureOption,
    HellMode,
    PricedTag,
    ProgressCb,
    CancelCheck,
    Source,
    TargetSnap,
    _CountSession,
    _beckon_command,
    _bot_command,
    _cancelled,
    _evaluate_refine_paths,
    _evaluate_roster_paths,
    _expected_beckon_sessions,
    _expected_sessions,
    _hits_for_tags,
    _is_beckonable,
    _load_target,
    _merge_priced,
    _option_rank_key,
    _progress,
)
from conjure_finder.urls import ParsedPostUrl, flatten_wishlist_urls, parse_post_url

# Caps keep API load modest for 20–50 post wishlists.
MAX_BULK_LIVE_CONJURE = 40
MAX_BULK_LIVE_BECKON = 30
MAX_BULK_PATHS = 12
MAX_SHARED_TAG_CANDIDATES = 60

# Live count probes clip at 100 — treating that as the real pool made
# /beckon ass look "cheap". Anything that hits the ceiling is useless here.
LIVE_POOL_CEILING = 100

# Metadata gates: never even probe ocean-sized tags.
BECKON_META_MAX = 40  # beckon only shines on sparse generals
CONJURE_SINGLE_META_MAX = 300
CONJURE_PAIR_META_MAX = 5_000

# Ultra-common / quality noise — never probe even if counts look wrong.
_BULK_TAG_DENY = frozenset(
    {
        "1girl",
        "1boy",
        "2girls",
        "solo",
        "ass",
        "breasts",
        "large_breasts",
        "huge_breasts",
        "nipples",
        "nude",
        "completely_nude",
        "highres",
        "absurdres",
        "incredibly_absurdres",
        "commentary",
        "commentary_request",
        "english_commentary",
        "translated",
        "translation_request",
        "official_art",
        "looking_at_viewer",
        "blush",
        "smile",
        "open_mouth",
        "closed_mouth",
        "simple_background",
        "white_background",
        "cowboy_shot",
        "upper_body",
        "full_body",
        "long_hair",
        "short_hair",
        "bangs",
        "bare_shoulders",
        "navel",
        "cleavage",
        "thighs",
        "pantyhose",
        "skirt",
        "shirt",
        "gloves",
        "indoors",
        "outdoors",
        "day",
        "night",
        "rating:e",
        "rating:q",
        "rating:s",
        "rating:g",
    }
)


@dataclass(frozen=True)
class BulkCoveredPost:
    post_id: int
    preview_url: str
    page_url: str


@dataclass(frozen=True)
class BulkPath:
    option: ConjureOption
    covered: tuple[BulkCoveredPost, ...]


@dataclass
class BulkResult:
    source: Source
    paths: list[BulkPath]
    warnings: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    urls: list[str] = field(default_factory=list)
    own_author: bool = False
    own_character: bool = False
    checked: int = 0
    wishlist_size: int = 0


def _covered_for_tags(
    targets: list[TargetSnap], names: list[str] | tuple[str, ...]
) -> tuple[BulkCoveredPost, ...]:
    need = set(names)
    return tuple(
        BulkCoveredPost(
            post_id=t.post_id,
            preview_url=t.preview_url,
            page_url=t.page_url,
        )
        for t in targets
        if need <= t.tag_set
    )


def _covered_for_roster(
    targets: list[TargetSnap], opt: ConjureOption
) -> tuple[BulkCoveredPost, ...]:
    tag = opt.tags[0] if opt.tags else ""
    exclude = opt.tags[1] if opt.path == "refine" and len(opt.tags) > 1 else ""
    out: list[BulkCoveredPost] = []
    for t in targets:
        if exclude and exclude in t.tag_set:
            continue
        if opt.path == "author":
            if not any(a.name == tag for a in t.meta.artists):
                continue
        elif opt.path in ("reshape", "reshape_m"):
            if not any(c.name == tag for c in t.meta.characters):
                continue
            want_solo = opt.path == "reshape"
            if bool(t.meta.has_solo) != want_solo:
                continue
        elif opt.path == "refine":
            # Labels look like "refine −greyscale → Author" / "… → reshape E".
            cmd = (opt.command or "").lower()
            if "→ author" in cmd:
                if not any(a.name == tag for a in t.meta.artists):
                    continue
            else:
                if not any(c.name == tag for c in t.meta.characters):
                    continue
        else:
            continue
        out.append(
            BulkCoveredPost(
                post_id=t.post_id,
                preview_url=t.preview_url,
                page_url=t.page_url,
            )
        )
    return tuple(out)


def _est_pool(tag: PricedTag) -> int:
    return tag.post_count if tag.post_count > 0 else 10_000_000


def _bulk_tag_ok(tag: PricedTag) -> bool:
    name = tag.name
    if name in _BULK_TAG_DENY:
        return False
    if name.endswith("_background") or name.endswith("_commentary"):
        return False
    return True


def _live_pool_usable(pool: int) -> bool:
    """False when the probe hit the API ceiling (real pool may be millions)."""
    return 0 < pool < LIVE_POOL_CEILING


def _metadata_conjure_candidates(
    targets: list[TargetSnap], priced: list[PricedTag]
) -> list[tuple[float, int, list[PricedTag]]]:
    """(est_currency, hits, tags) — rare singles/pairs only. Never fake pool=100."""
    rows: list[tuple[float, int, list[PricedTag]]] = []
    for tag in priced:
        if not _bulk_tag_ok(tag):
            continue
        hits = _hits_for_tags(targets, [tag.name])
        if hits <= 0:
            continue
        pool = _est_pool(tag)
        if pool > CONJURE_SINGLE_META_MAX:
            continue
        sessions = _expected_sessions(pool, hits=min(hits, pool))
        if sessions == float("inf"):
            continue
        est = tag.price * sessions
        # Shared rare tags are the whole point of bulk.
        if hits > 1:
            est *= 0.75
        rows.append((est, hits, [tag]))

    # Shared rare pairs: both on ≥2 posts, at least one side actually rare.
    shared = [
        t
        for t in priced
        if _bulk_tag_ok(t)
        and _hits_for_tags(targets, [t.name]) >= 2
        and _est_pool(t) <= CONJURE_PAIR_META_MAX
    ]
    shared.sort(key=lambda t: (_est_pool(t), t.name))
    shared = shared[:30]
    seen_pair: set[tuple[str, str]] = set()
    for i, a in enumerate(shared):
        for b in shared[i + 1 :]:
            # Skip ocean×ocean; need a rare anchor.
            if min(_est_pool(a), _est_pool(b)) > CONJURE_SINGLE_META_MAX:
                continue
            names = [a.name, b.name]
            hits = _hits_for_tags(targets, names)
            if hits < 2:
                continue
            key = tuple(sorted(names))
            if key in seen_pair:
                continue
            seen_pair.add(key)
            # Optimistic pair est uses rarer tag (intersection ≤ min).
            pool = min(_est_pool(a), _est_pool(b))
            sessions = _expected_sessions(pool, hits=min(hits, pool))
            if sessions == float("inf"):
                continue
            cost = a.price + b.price
            rows.append((cost * sessions * 0.75, hits, [a, b]))

    # Character/artist × rare general (even if character pool is huge).
    premiums = [
        t
        for t in priced
        if t.category in (1, 4) and _bulk_tag_ok(t)
    ]
    rares = [
        t
        for t in priced
        if t.price <= 25
        and _bulk_tag_ok(t)
        and _est_pool(t) <= CONJURE_SINGLE_META_MAX
    ]
    rares.sort(key=lambda t: (_est_pool(t), t.name))
    for prem in premiums[:12]:
        for gen in rares[:20]:
            names = [prem.name, gen.name]
            hits = _hits_for_tags(targets, names)
            if hits <= 0:
                continue
            key = tuple(sorted(names))
            if key in seen_pair:
                continue
            seen_pair.add(key)
            pool = _est_pool(gen)  # intersection dominated by rare general
            sessions = _expected_sessions(pool, hits=min(hits, pool))
            if sessions == float("inf"):
                continue
            cost = prem.price + gen.price
            est = cost * sessions
            if hits > 1:
                est *= 0.75
            rows.append((est, hits, [prem, gen]))

    rows.sort(key=lambda r: (r[0], -r[1], r[2][0].name))
    out: list[tuple[float, int, list[PricedTag]]] = []
    seen: set[tuple[str, ...]] = set()
    for est, hits, tags in rows:
        key = tuple(sorted(t.name for t in tags))
        if key in seen:
            continue
        seen.add(key)
        out.append((est, hits, tags))
        if len(out) >= MAX_SHARED_TAG_CANDIDATES:
            break
    return out


def _metadata_beckon_candidates(
    targets: list[TargetSnap], priced: list[PricedTag]
) -> list[tuple[float, int, PricedTag]]:
    """Only genuinely sparse generals — never ass/solo/etc."""
    rows: list[tuple[float, int, PricedTag]] = []
    for tag in priced:
        if not _is_beckonable(tag) or not _bulk_tag_ok(tag):
            continue
        hits = _hits_for_tags(targets, [tag.name])
        if hits <= 0:
            continue
        pool = _est_pool(tag)
        if pool > BECKON_META_MAX:
            continue
        sessions = _expected_beckon_sessions(pool, hits=min(hits, pool))
        if sessions == float("inf"):
            continue
        est = BECKON_PRICE * sessions
        if hits > 1:
            est *= 0.7
        rows.append((est, hits, tag))
    rows.sort(key=lambda r: (r[0], -r[1], r[2].name))
    return rows[: MAX_BULK_LIVE_BECKON + 10]


def _resolve_hell_mode(source: Source, targets: list[TargetSnap]) -> HellMode:
    if source != "rule34":
        return ""
    hell_modes = {t.hell_mode for t in targets if t.hell_mode}
    if hell_modes == {"slop"}:
        return "slop"
    if hell_modes == {"hell"}:
        return "hell"
    if "slop" in hell_modes and "hell" in hell_modes:
        for t in targets:
            t.warnings.append(
                "Mixed AI / non-AI in wishlist — scoring with /conjure_hell (non-AI)."
            )
        return "hell"
    return "hell"


async def find_bulk_paths(
    urls: list[str],
    *,
    own_author: bool = False,
    own_character: bool = False,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
) -> BulkResult:
    """Rank acquisition paths that can hit any post in the wishlist."""
    t0 = time.perf_counter()
    if not urls:
        raise ValueError("No URLs in wishlist.")

    parsed_list = [parse_post_url(u) for u in urls]
    sources = {p.source for p in parsed_list}
    if len(sources) > 1:
        raise ValueError(
            "Wishlist must be all Danbooru or all Rule34 — split mixed sites."
        )
    source = parsed_list[0].source

    uniq: list[ParsedPostUrl] = []
    seen_ids: set[int] = set()
    for p in parsed_list:
        if p.post_id in seen_ids:
            continue
        seen_ids.add(p.post_id)
        uniq.append(p)

    targets: list[TargetSnap] = []
    load_warnings: list[str] = []
    shared_db = None
    if source == "danbooru":
        from bot.services.danbooru import DanbooruClient

        shared_db = DanbooruClient()
        await shared_db.start()
    try:
        for i, p in enumerate(uniq, 1):
            if _cancelled(cancel_check):
                raise ValueError("Search cancelled.")
            _progress(
                progress,
                f"Loading wishlist {i}/{len(uniq)} — post #{p.post_id}…",
            )
            try:
                targets.append(
                    await _load_target(
                        p,
                        progress,
                        cancel_check,
                        danbooru_client=shared_db,
                    )
                )
            except Exception as exc:
                load_warnings.append(f"Skipped #{p.post_id}: {exc}")
                _progress(progress, f"Skipped #{p.post_id}: {exc}")
    finally:
        if shared_db is not None:
            await shared_db.stop()

    if not targets:
        detail = "; ".join(load_warnings[:3]) if load_warnings else "unknown error"
        raise ValueError(f"Could not load any wishlist posts. {detail}")

    hell_mode = _resolve_hell_mode(source, targets)
    warnings: list[str] = list(load_warnings)
    for t in targets:
        warnings.extend(t.warnings)
    warnings.append(
        f"Bulk wishlist: {len(targets)}/{len(uniq)} posts loaded — "
        f"ranking paths that hit any of them."
    )
    if own_author:
        warnings.append("Ownership: already have author (Author paths skip conjure 50).")
    if own_character:
        warnings.append(
            "Ownership: already have character (reshape paths skip conjure 50)."
        )

    priced = _merge_priced(targets)
    counter = _CountSession(source, hell_mode)
    await counter.start()
    checked = 0
    collected: list[BulkPath] = []

    try:
        # --- Metadata → live conjure probes ---
        conjure_cands = _metadata_conjure_candidates(targets, priced)[
            :MAX_BULK_LIVE_CONJURE
        ]
        _progress(
            progress,
            f"Live-counting {len(conjure_cands)} conjure candidates…",
        )
        for _est, _hits, tag_objs in conjure_cands:
            if _cancelled(cancel_check):
                warnings.append("Search cancelled.")
                break
            names = [t.name for t in tag_objs]
            hits = _hits_for_tags(targets, names)
            if hits <= 0:
                continue
            cost = sum(t.price for t in tag_objs)
            _progress(progress, f"Checking conjure: {' '.join(names)}…")
            pool = await counter(names)
            checked += 1
            if pool < 0 or not _live_pool_usable(pool):
                continue
            hits = min(hits, pool)
            sessions = _expected_sessions(pool, hits=hits)
            guaranteed = pool <= GUARANTEE_POOL_MAX
            hit_note = f" ({hits} acceptable)" if hits > 1 else ""
            opt = ConjureOption(
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
                        f"~{sessions:.1f} conjure sessions expected{hit_note} "
                        f"(free reroll + same-tag pity)."
                    )
                ),
                path="conjure",
            )
            covered = _covered_for_tags(targets, names)
            if covered:
                collected.append(BulkPath(option=opt, covered=covered))

        # --- Beckon (sparse generals only; skip live ceiling) ---
        if hell_mode != "slop":
            beckon_cands = _metadata_beckon_candidates(targets, priced)[
                :MAX_BULK_LIVE_BECKON
            ]
            _progress(
                progress,
                f"Live-counting {len(beckon_cands)} beckon candidates…",
            )
            for _est, _hits, tag in beckon_cands:
                if _cancelled(cancel_check):
                    break
                hits = _hits_for_tags(targets, [tag.name])
                if hits <= 0:
                    continue
                _progress(progress, f"Checking beckon: {tag.name}…")
                pool = await counter([tag.name])
                checked += 1
                if pool < 0 or not _live_pool_usable(pool):
                    continue
                hits = min(hits, pool)
                sessions = _expected_beckon_sessions(pool, hits=hits)
                guaranteed = pool <= BECKON_PEEKS
                hit_note = f" ({hits} acceptable)" if hits > 1 else ""
                opt = ConjureOption(
                    tags=(tag.name,),
                    cost=BECKON_PRICE,
                    pool_size=pool,
                    guaranteed=guaranteed,
                    command=_beckon_command(source, tag.name, hell_mode),
                    hell_mode=hell_mode,
                    expected_sessions=sessions,
                    expected_currency=BECKON_PRICE * sessions,
                    note=(
                        f"Beckon guarantee: up to {BECKON_PEEKS} peeks cover the whole pool."
                        if guaranteed
                        else (
                            f"~{sessions:.1f} beckon sessions expected{hit_note}."
                        )
                    ),
                    path="beckon",
                )
                covered = _covered_for_tags(targets, [tag.name])
                if covered:
                    collected.append(BulkPath(option=opt, covered=covered))

        # --- Roster (with ownership) ---
        if not _cancelled(cancel_check):
            _progress(progress, "Checking Author / reshape roster paths…")
            roster_opts = await _evaluate_roster_paths(
                source,
                hell_mode,
                targets,
                counter,
                progress=progress,
                cancel_check=cancel_check,
                own_author=own_author,
                own_character=own_character,
            )
            checked += len(roster_opts)
            for opt in roster_opts:
                covered = _covered_for_roster(targets, opt)
                if covered:
                    collected.append(BulkPath(option=opt, covered=covered))

            # Targeted refine (capped); floor from best collected so far.
            floor = None
            if collected:
                floor = min(bp.option.expected_currency for bp in collected)
            _progress(progress, "Checking refine paths (−exclude crafts)…")
            refine_best = await _evaluate_refine_paths(
                source,
                hell_mode,
                targets,
                counter,
                floor=floor,
                progress=progress,
                cancel_check=cancel_check,
                own_author=own_author,
                own_character=own_character,
            )
            if refine_best:
                checked += 1
                covered = _covered_for_roster(targets, refine_best)
                if covered:
                    collected.append(BulkPath(option=refine_best, covered=covered))
    finally:
        await counter.aclose()

    # Rank + dedupe by command/path/tags
    collected.sort(key=lambda bp: (_option_rank_key(bp.option), -len(bp.covered)))
    paths: list[BulkPath] = []
    seen_keys: set[tuple] = set()
    for bp in collected:
        key = (
            bp.option.path,
            bp.option.tags,
            tuple(c.post_id for c in bp.covered),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        paths.append(bp)
        if len(paths) >= MAX_BULK_PATHS:
            break

    return BulkResult(
        source=source,
        paths=paths,
        warnings=warnings,
        elapsed_sec=time.perf_counter() - t0,
        urls=[p.url for p in uniq],
        own_author=own_author,
        own_character=own_character,
        checked=checked,
        wishlist_size=len(targets),
    )


async def find_bulk_paths_from_text(
    raw: str,
    *,
    own_author: bool = False,
    own_character: bool = False,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
) -> BulkResult:
    return await find_bulk_paths(
        flatten_wishlist_urls(raw),
        own_author=own_author,
        own_character=own_character,
        progress=progress,
        cancel_check=cancel_check,
    )
