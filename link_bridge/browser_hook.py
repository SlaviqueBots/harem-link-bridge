"""Local HTTP hook so a browser userscript can send booru post URLs to Bridge."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

UrlHandler = Callable[[str, dict[str, Any]], None]


class _HookHandler(BaseHTTPRequestHandler):
    server_version = "HaremLinkBridgeHook/1.0"
    _on_url: UrlHandler | None = None

    def log_message(self, fmt: str, *args) -> None:
        logger.debug("browser_hook " + fmt, *args)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _parse_request(self) -> tuple[str, dict[str, Any]]:
        parsed = urlparse(self.path)
        if parsed.path not in ("/send", "/"):
            return "", {}
        qs = parse_qs(parsed.query or "")
        meta: dict[str, Any] = {}
        if qs.get("source"):
            meta["source"] = str(qs["source"][0] or "").strip()
        if qs.get("action"):
            meta["action"] = str(qs["action"][0] or "").strip().lower()
        if qs.get("url"):
            return str(qs["url"][0] or "").strip(), meta
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return "", meta
        try:
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return "", meta
        if not isinstance(body, dict):
            return "", meta
        if body.get("source"):
            meta["source"] = str(body.get("source") or "").strip()
        if body.get("action"):
            meta["action"] = str(body.get("action") or "").strip().lower()
        return str(body.get("url") or "").strip(), meta

    def _reply(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        url, meta = self._parse_request()
        if not url:
            self._reply(400, {"ok": False, "error": "missing url"})
            return
        handler = type(self)._on_url
        if handler is None:
            self._reply(503, {"ok": False, "error": "bridge not ready"})
            return
        try:
            handler(url, meta)
        except Exception as exc:
            logger.debug("browser_hook handler failed", exc_info=True)
            self._reply(500, {"ok": False, "error": str(exc)})
            return
        self._reply(200, {"ok": True})

    do_POST = do_GET


class BrowserHookServer:
    """127.0.0.1 listener; safe to start/stop with the Bridge GUI."""

    def __init__(self, port: int, on_url: UrlHandler) -> None:
        self.port = int(port)
        self._on_url = on_url
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        _HookHandler._on_url = self._on_url

        def _factory(*args, **kwargs):
            return _HookHandler(*args, **kwargs)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), _factory)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info("browser hook listening on http://127.0.0.1:%s/send", self.port)

    def stop(self) -> None:
        httpd = self._httpd
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        self._httpd = None
        self._thread = None
