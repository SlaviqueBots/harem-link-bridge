"""Optional outbound proxy for Harem Link Bridge (off by default).

Two modes:
  - boorus only (default): booru/CDN hosts go via the proxy, the bot
    server WebSocket and updater stay direct.
  - route all: everything (WS, updates, booru) goes via the proxy.

Two types: HTTP(S) and SOCKS5. Providers hand out ``host`` + ``http port``
+ ``socks5 port`` + ``login`` + ``password`` (sometimes as one
``login:password@ip:port`` string) — the Setup tab has fields for each
plus a paste box that parses that URI form.

Routing is applied process-wide via proxy env vars, so every stdlib /
httpx / websockets / curl_cffi caller follows it with no per-callsite
config plumbing:
  - httpx (Conjure engine, Danbooru/Rule34 clients, previews) reads
    trust_env by default (socks via ``socksio``).
  - websockets ``connect(proxy=True)`` (default) reads env (socks via
    ``python-socks``).
  - urllib call sites go through :func:`open_url` here, which honors
    NO_PROXY and uses curl_cffi for the SOCKS case (stdlib has no SOCKS).
"""

from __future__ import annotations

import contextlib
import io
import os
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

# Hosts that count as "booru traffic" for the boorus-only mode.
BOORU_SUFFIXES = ("donmai.us", "rule34.xxx", "soybooru.com")

_PROXY_ENV_KEYS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)
_NO_PROXY_KEYS = ("no_proxy", "NO_PROXY")

# Keys we set in this process (scheme, url) — cleared when disabled so a
# mid-session toggle-off never leaves a stale proxy behind, while a user's
# own pre-existing env is left alone.
_LAST_SET: dict[str, str] = {}


def parse_proxy_uri(text: str) -> dict[str, str | int]:
    """Parse ``login:password@host:port`` (scheme optional) into parts.

    Accepts ``http://login:password@ip:port``, ``socks5://…``, bare
    ``ip:port``, ``user@host:port``. Raises ValueError with a short
    message on bad input.
    """
    raw = (text or "").strip().strip("'\"")
    if not raw:
        raise ValueError("empty proxy URI")
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlparse(raw)
    except Exception as exc:
        raise ValueError(f"bad proxy URI: {exc}") from exc
    host = (parts.hostname or "").strip()
    if not host:
        raise ValueError("proxy URI needs a host (login:password@ip:port)")
    port = parts.port or 0
    if not port:
        raise ValueError("proxy URI needs a port (…@ip:port)")
    user = urllib.parse.unquote(parts.username or "")
    password = urllib.parse.unquote(parts.password or "")
    return {"host": host, "port": int(port), "user": user, "password": password}


def _quote_cred(text: str) -> str:
    return urllib.parse.quote((text or ""), safe="")


def http_proxy_url(cfg) -> str:
    """``http://user:pass@host:port`` or ``""`` when incomplete."""
    host = (getattr(cfg, "proxy_host", "") or "").strip()
    try:
        port = int(getattr(cfg, "proxy_http_port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or port <= 0:
        return ""
    user = getattr(cfg, "proxy_user", "") or ""
    password = getattr(cfg, "proxy_pass", "") or ""
    if user:
        return f"http://{_quote_cred(user)}:{_quote_cred(password)}@{host}:{port}"
    return f"http://{host}:{port}"


def socks_proxy_url(cfg) -> str:
    """``socks5h://user:pass@host:port`` or ``""`` when incomplete."""
    host = (getattr(cfg, "proxy_host", "") or "").strip()
    try:
        port = int(getattr(cfg, "proxy_socks_port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or port <= 0:
        return ""
    user = getattr(cfg, "proxy_user", "") or ""
    password = getattr(cfg, "proxy_pass", "") or ""
    if user:
        return f"socks5h://{_quote_cred(user)}:{_quote_cred(password)}@{host}:{port}"
    return f"socks5h://{host}:{port}"


def active_proxy(cfg) -> tuple[str, str]:
    """``(scheme, url)`` — scheme is ``"http"`` / ``"socks5"`` / ``""``."""
    if not bool(getattr(cfg, "proxy_enabled", False)):
        return "", ""
    ptype = (getattr(cfg, "proxy_type", "http") or "http").strip().lower()
    if ptype.startswith("socks"):
        url = socks_proxy_url(cfg)
        return ("socks5", url) if url else ("", "")
    url = http_proxy_url(cfg)
    return ("http", url) if url else ("", "")


def is_booru_host(host: str) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    return bool(h) and any(h == s or h.endswith("." + s) for s in BOORU_SUFFIXES)


def is_local_host(host: str) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if h in ("localhost", "::1"):
        return True
    if h.startswith("127.") or h.startswith("10.") or h.startswith("192.168."):
        return True
    if h.startswith("172."):
        try:
            second = int(h.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


def should_proxy_host(cfg, host: str) -> bool:
    """True when ``host`` must go via the proxy under ``cfg``."""
    scheme, url = active_proxy(cfg)
    if not url:
        return False
    if is_local_host(host):
        return False
    if bool(getattr(cfg, "proxy_route_all", False)):
        return True
    return is_booru_host(host)


def should_proxy_url(cfg, url: str) -> bool:
    try:
        host = urlparse(url or "").hostname or ""
    except Exception:
        return False
    return should_proxy_host(cfg, host)


def _env_proxy_for(url: str) -> tuple[str, str]:
    """Active proxy from process env for ``url`` (honors no_proxy)."""
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        return "", ""
    if not host or is_local_host(host):
        return "", ""
    if urllib.request.proxy_bypass_environment(host):
        return "", ""
    scheme = (urlparse(url or "").scheme or "http").lower()
    getproxies = urllib.request.getproxies_environment()
    proxy = getproxies.get(scheme) or getproxies.get("all") or ""
    if not proxy:
        return "", ""
    pscheme = (urlparse(proxy).scheme or "").lower()
    if pscheme.startswith("socks"):
        return "socks5", proxy
    return "http", proxy


def apply_to_process(cfg) -> str:
    """Apply ``cfg`` proxy to process env. Returns active url or ``""``.

    Disabled → removes only vars this module previously set (never touch
    a user's own env). Boorus-only keeps the bot server direct via
    NO_PROXY on the configured WS host.
    """
    global _LAST_SET
    for key in list(_LAST_SET):
        if os.environ.get(key) == _LAST_SET[key]:
            with contextlib.suppress(Exception):
                del os.environ[key]
    _LAST_SET = {}

    scheme, url = active_proxy(cfg)
    if not url:
        return ""
    pairs = {
        "http_proxy": url,
        "https_proxy": url,
        "all_proxy": url,
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "ALL_PROXY": url,
    }
    for key, val in pairs.items():
        os.environ[key] = val
    _LAST_SET = dict(pairs)

    bypass = {"localhost", "127.0.0.1", "::1"}
    if not bool(getattr(cfg, "proxy_route_all", False)):
        host = (getattr(cfg, "host", "") or "").strip()
        if host:
            bypass.add(host)
    no_proxy = ",".join(sorted(bypass))
    os.environ["no_proxy"] = no_proxy
    os.environ["NO_PROXY"] = no_proxy
    return url


def curl_proxies_for(url: str) -> dict[str, str]:
    """``proxies`` dict for curl_cffi from process env (``{}`` = direct)."""
    scheme, proxy = _env_proxy_for(url)
    if not proxy:
        return {}
    return {"http": proxy, "https": proxy}


class _CIHeaders(dict):
    """Case-insensitive header dict for the SOCKS adapter path."""

    def get(self, key, default=None):  # type: ignore[override]
        if key is None:
            return default
        low = str(key).lower()
        for k, v in self.items():
            if str(k).lower() == low:
                return v
        return default


class _CurlRespAdapter:
    """Minimal urlopen-shaped wrapper around a curl_cffi response."""

    def __init__(self, resp) -> None:
        self._buf = io.BytesIO(bytes(resp.content or b""))
        headers = getattr(resp, "headers", {}) or {}
        # curl_cffi Headers are already case-insensitive — keep them as-is.
        # Plain dicts get a case-insensitive shim below.
        if hasattr(headers, "get"):
            try:
                headers.get("content-type")
                self.headers = headers
            except Exception:
                self.headers = _CIHeaders(dict(getattr(headers, "items", lambda: [])()))
        else:
            try:
                items = dict(headers.items())
            except Exception:
                items = {}
            self.headers = _CIHeaders(items)
        self.status = int(getattr(resp, "status_code", 200) or 200)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read() if n is None or n < 0 else self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def getheader(self, name: str, default=None):
        return self.headers.get((name or "").lower(), default)


def open_url(req, *, timeout: float = 20.0):
    """Proxy-aware ``urllib.request.urlopen`` replacement.

    - direct / HTTP proxy → stdlib opener (NO_PROXY honored);
    - SOCKS proxy → curl_cffi (stdlib cannot do SOCKS).
    Returns a context manager with ``.read()`` / ``.headers``.
    """
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req or "")
    headers = dict(getattr(req, "headers", {}) or {})
    scheme, proxy = _env_proxy_for(url)
    if scheme == "socks5":
        from curl_cffi.requests import Session

        session_proxies = {"http": proxy, "https": proxy}
        try:
            timeout_f = float(timeout or 20.0)
        except (TypeError, ValueError):
            timeout_f = 20.0
        with Session(impersonate="chrome120", timeout=timeout_f) as sess:
            resp = sess.get(url, headers=headers or None, proxies=session_proxies)
            if int(getattr(resp, "status_code", 0) or 0) >= 400:
                raise urllib.error.HTTPError(
                    url,
                    int(resp.status_code),
                    f"HTTP {resp.status_code}",
                    {},
                    io.BytesIO(bytes(resp.content or b"")),
                )
            return _CurlRespAdapter(resp)
    if scheme == "http" and proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        if isinstance(req, urllib.request.Request):
            return opener.open(req, timeout=timeout)
        return opener.open(url, timeout=timeout)
    if isinstance(req, urllib.request.Request):
        return urllib.request.urlopen(req, timeout=timeout)
    return urllib.request.urlopen(url, timeout=timeout)


def test_proxy(cfg, *, timeout: float = 15.0) -> tuple[bool, str]:
    """Fetch one tiny Danbooru API page via the active proxy."""
    scheme, url = active_proxy(cfg)
    if not url:
        return False, "proxy disabled or host/port missing"
    probe = "https://danbooru.donmai.us/posts.json?limit=1"
    req = urllib.request.Request(probe, headers={"User-Agent": "HaremLinkBridge/proxy-test"})
    try:
        with open_url(req, timeout=timeout) as resp:
            body = resp.read(4096)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:220]
    if not body:
        return False, "empty reply"
    return True, f"ok via {scheme} proxy ({len(body)} bytes)"
