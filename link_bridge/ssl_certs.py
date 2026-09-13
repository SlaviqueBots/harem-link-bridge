"""Ensure HTTPS CA bundle is usable (esp. frozen PyInstaller builds).

httpx/ssl raise a bare ``[Errno 2] No such file or directory`` when
``SSL_CERT_FILE`` points at a deleted ``_MEI*`` folder from a previous run,
or when certifi's ``cacert.pem`` was not bundled. Fix both before any HTTPS.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CA_KEYS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


def _is_usable_bundle(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def ensure_ssl_certs() -> str | None:
    """Point SSL env vars at a real cacert.pem. Returns the path used, or None."""
    candidates: list[Path] = []

    for key in _CA_KEYS:
        raw = (os.environ.get(key) or "").strip()
        if raw:
            candidates.append(Path(raw))

    try:
        import certifi

        candidates.append(Path(certifi.where()))
    except Exception:
        logger.debug("certifi unavailable", exc_info=True)

    chosen: Path | None = None
    for path in candidates:
        if _is_usable_bundle(path):
            chosen = path
            break

    if chosen is None:
        # Purge stale pointers (deleted _MEI* dirs) so httpx falls back to the
        # OS trust store instead of raising bare [Errno 2] at client creation.
        for key in _CA_KEYS:
            raw = (os.environ.get(key) or "").strip()
            if raw and not _is_usable_bundle(Path(raw)):
                try:
                    del os.environ[key]
                except KeyError:
                    pass
        logger.warning(
            "No usable CA bundle found — HTTPS (Danbooru/Rule34) may fail with "
            "[Errno 2] No such file or directory"
        )
        return None

    resolved = str(chosen.resolve())
    for key in _CA_KEYS:
        prev = (os.environ.get(key) or "").strip()
        if prev != resolved:
            os.environ[key] = resolved
    return resolved
