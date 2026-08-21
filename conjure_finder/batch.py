"""Batch runner: per-site queues, Danbooru and Rule34 in parallel."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from conjure_finder.engine import FindResult, find_cheapest_any
from conjure_finder.urls import parse_post_url

ProgressCb = Callable[[str, str], None]  # (site, message)
CancelCheck = Callable[[], bool]
# (index, total, label, payload) — label is a display string for the job
ItemDoneCb = Callable[[int, int, str, FindResult | Exception], None]


def _job_label(urls: list[str]) -> str:
    if len(urls) == 1:
        return urls[0]
    try:
        ids = [str(parse_post_url(u).post_id) for u in urls]
        src = parse_post_url(urls[0]).source
        return f"{src} any-of #{'|'.join(ids)}"
    except Exception:
        return f"any-of ({len(urls)} urls)"


async def run_batch(
    jobs: list[list[str]],
    *,
    progress: ProgressCb | None = None,
    cancel_check: CancelCheck | None = None,
    on_item_done: ItemDoneCb | None = None,
) -> list[FindResult | Exception]:
    """Process URL jobs with one sequential queue per site; sites run concurrently.

    Each job is a list of URLs (length 1 = single target; length >1 = any-of).
    """
    total = len(jobs)
    results: list[FindResult | Exception | None] = [None] * total

    danbooru: list[tuple[int, list[str]]] = []
    rule34: list[tuple[int, list[str]]] = []
    parse_errors: list[tuple[int, list[str], Exception]] = []

    for i, urls in enumerate(jobs):
        if not urls:
            parse_errors.append((i, urls, ValueError("Empty URL group.")))
            continue
        try:
            parsed = [parse_post_url(u) for u in urls]
            sources = {p.source for p in parsed}
            if len(sources) > 1:
                raise ValueError(
                    "Any-of group must be all Danbooru or all Rule34 — "
                    "put mixed sites on separate lines."
                )
            source = parsed[0].source
        except Exception as exc:
            parse_errors.append((i, urls, exc))
            continue
        if source == "danbooru":
            danbooru.append((i, urls))
        else:
            rule34.append((i, urls))

    for i, urls, exc in parse_errors:
        results[i] = exc
        if on_item_done:
            on_item_done(i + 1, total, _job_label(urls) if urls else "(empty)", exc)

    def _progress(site: str, msg: str) -> None:
        if progress:
            progress(site, msg)

    async def _run_queue(site: str, site_jobs: list[tuple[int, list[str]]]) -> None:
        if not site_jobs:
            return
        for n, (idx, urls) in enumerate(site_jobs, 1):
            if cancel_check and cancel_check():
                err = ValueError("Cancelled.")
                results[idx] = err
                if on_item_done:
                    on_item_done(idx + 1, total, _job_label(urls), err)
                break
            label = _job_label(urls)
            _progress(site, f"[{n}/{len(site_jobs)}] starting…")

            def site_progress(
                msg: str, _site: str = site, _n: int = n, _t: int = len(site_jobs)
            ) -> None:
                _progress(_site, f"[{_n}/{_t}] {msg}")

            try:
                result = await find_cheapest_any(
                    urls,
                    progress=site_progress,
                    cancel_check=cancel_check,
                )
                results[idx] = result
                if on_item_done:
                    on_item_done(idx + 1, total, label, result)
            except Exception as exc:
                results[idx] = exc
                if on_item_done:
                    on_item_done(idx + 1, total, label, exc)
            _progress(site, f"[{n}/{len(site_jobs)}] done")

        if not (cancel_check and cancel_check()):
            _progress(site, "idle")

    await asyncio.gather(
        _run_queue("danbooru", danbooru),
        _run_queue("rule34", rule34),
    )

    if progress:
        progress("danbooru", "")
        progress("rule34", "")

    out: list[FindResult | Exception] = []
    for item in results:
        if item is None:
            out.append(ValueError("Skipped."))
        else:
            out.append(item)
    return out
