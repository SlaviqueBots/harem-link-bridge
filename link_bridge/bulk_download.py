"""Bulk download of your done cards' full images (Setup button).

Gentle by design: pages the roster slowly, downloads with 2 workers and a
stagger between starts, backs off on 429/RetryAfter. Skips URLs already in
the permanent cache. Cancellable. UI-free: the GUI supplies page/fetch fns
and progress callbacks; all Tk updates happen in those callbacks.

No Tk imports here - safe in threads and unit tests.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)

STAGGER_SEC = 0.4
WORKERS = 2
RETRY_BACKOFF_SEC = 5.0
MAX_RETRIES = 2


def pick_full_url(item: dict) -> str:
    """Full-res URL for permanent storage (file first, then image, then preview)."""
    for key in ("file_url", "image_url", "preview_url"):
        try:
            url = str(item.get(key) or "").strip()
        except Exception:
            continue
        if url.startswith("http"):
            return url
    return ""


class BulkDoneDownload:
    """Paged gentle download run. Call start() once; cancel() anytime."""

    def __init__(
        self,
        *,
        page_size: int = 96,
        fetch_page: Callable[[int], tuple[list[dict], int]],
        fetch_bytes: Callable[[str], bytes],
        is_cached: Callable[[str], bool],
        store: Callable[[str, bytes], None],
        on_progress: Callable[[int, int, int, int, int], None] | None = None,
        on_done: Callable[[dict], None] | None = None,
        stagger_sec: float = STAGGER_SEC,
        workers: int = WORKERS,
    ) -> None:
        self._page_size = max(1, int(page_size))
        self._fetch_page = fetch_page
        self._fetch_bytes = fetch_bytes
        self._is_cached = is_cached
        self._store = store
        self._on_progress = on_progress
        self._on_done = on_done
        self._stagger = max(0.0, float(stagger_sec))
        self._workers = max(1, int(workers))
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._done = 0
        self._total = 0
        self._bytes = 0
        self._cached = 0
        self._failed = 0
        self._error = ""

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def start(self) -> threading.Thread:
        if self._thread is not None:
            return self._thread
        self._thread = threading.Thread(
            target=self._run, name="hlb-bulk-dl", daemon=True
        )
        self._thread.start()
        return self._thread

    def _report(self) -> None:
        if self._on_progress is None:
            return
        try:
            self._on_progress(
                self._done, self._total, self._bytes, self._cached, self._failed
            )
        except Exception:
            logger.debug("bulk progress callback failed", exc_info=True)

    def _tick(self, *, cached: bool = False, failed: bool = False, nbytes: int = 0) -> None:
        with self._lock:
            self._done += 1
            if cached:
                self._cached += 1
            if failed:
                self._failed += 1
            self._bytes += max(0, int(nbytes))
        self._report()

    def _download_one(self, url: str) -> None:
        if self._cancel.is_set():
            self._tick(failed=True)
            return
        try:
            if self._is_cached(url):
                self._tick(cached=True)
                return
        except Exception:
            pass
        data = b""
        for attempt in range(MAX_RETRIES + 1):
            if self._cancel.is_set():
                self._tick(failed=True)
                return
            try:
                data = self._fetch_bytes(url) or b""
                break
            except Exception as exc:
                text = str(exc).lower()
                if "429" in text or "retry" in text or "too many" in text:
                    time.sleep(RETRY_BACKOFF_SEC)
                    continue
                if attempt < MAX_RETRIES:
                    time.sleep(self._stagger)
                    continue
                logger.debug("bulk download failed %s: %s", url[:80], exc)
                break
        if not data:
            self._tick(failed=True)
            return
        try:
            self._store(url, bytes(data))
        except Exception:
            logger.debug("bulk store failed", exc_info=True)
            self._tick(failed=True)
            return
        self._tick(nbytes=len(data))

    def _run(self) -> None:
        urls: list[str] = []
        try:
            page = 0
            while not self._cancel.is_set():
                items, total = self._fetch_page(page)
                if page == 0:
                    with self._lock:
                        self._total = max(0, int(total))
                    self._report()
                if not items:
                    break
                for item in items:
                    url = pick_full_url(item if isinstance(item, dict) else {})
                    if url and url not in urls:
                        urls.append(url)
                with self._lock:
                    if self._total <= 0:
                        self._total = len(urls)
                page += 1
                if len(urls) >= max(1, int(total)):
                    break
                time.sleep(self._stagger)
        except Exception as exc:
            logger.debug("bulk paging failed", exc_info=True)
            with self._lock:
                self._error = str(exc)[:160]
        with self._lock:
            # The bar measures unique pictures (dups across cards fetch once).
            self._total = len(urls)
            self._done = 0
        self._report()
        if urls and not self._cancel.is_set():
            with ThreadPoolExecutor(
                max_workers=self._workers, thread_name_prefix="hlb-bulk"
            ) as pool:
                futures = []
                for url in urls:
                    if self._cancel.is_set():
                        break
                    futures.append(pool.submit(self._download_one, url))
                    time.sleep(self._stagger)
                for fut in futures:
                    try:
                        fut.result()
                    except Exception:
                        pass
        # Anything left unticked (cancelled mid-flight) is counted failed
        # so the bar always closes at 100%.
        with self._lock:
            remaining = max(0, self._total - self._done)
            self._done += remaining
            if remaining:
                self._failed += remaining
        summary = {
            "done": self._done,
            "total": self._total,
            "bytes": self._bytes,
            "cached": self._cached,
            "failed": self._failed,
            "cancelled": self._cancel.is_set(),
            "error": self._error,
        }
        if self._on_done is not None:
            try:
                self._on_done(summary)
            except Exception:
                logger.debug("bulk done callback failed", exc_info=True)
