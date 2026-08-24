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


def ensure_ssl_certs() -> str | None:
    """Point SSL env vars at a real cacert.pem. Returns the path used, or None."""
    candidates: list[Path] = []

    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
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
        try:
            if path.is_file() and path.stat().st_size > 0:
                chosen = path
                break
        except OSError:
            continue

    if chosen is None:
        logger.warning(
            "No usable CA bundle found — HTTPS (Danbooru/Rule34) may fail with "
            "[Errno 2] No such file or directory"
        )
        return None

    resolved = str(chosen.resolve())
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        prev = (os.environ.get(key) or "").strip()
        if prev != resolved:
            os.environ[key] = resolved
    return resolved
