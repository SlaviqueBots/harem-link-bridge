"""WebSocket client talking to the bot's pc_bridge server."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from link_bridge.config import BridgeConfig

logger = logging.getLogger(__name__)

StatusCb = Callable[[str], None]
OpenCb = Callable[[str], None]
JsonCb = Callable[[dict[str, Any]], None]


class BridgeClient:
    """Reconnecting WebSocket client. Runs inside an asyncio event loop."""

    def __init__(
        self,
        cfg: BridgeConfig,
        *,
        on_status: StatusCb | None = None,
        on_open_url: OpenCb | None = None,
        on_message: JsonCb | None = None,
    ) -> None:
        self.cfg = cfg
        self.on_status = on_status or (lambda _s: None)
        self.on_open_url = on_open_url or (lambda _u: None)
        self.on_message = on_message or (lambda _m: None)
        self._ws: Any = None
        self._stop = asyncio.Event()
        self._paused = bool(cfg.paused)
        self._task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self.bot_username: str = ""

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def request_stop(self) -> None:
        self._stop.set()

    async def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        self.cfg.paused = self._paused
        ws = self._ws
        if ws is None:
            return
        op = "pause" if self._paused else "resume"
        try:
            await ws.send(json.dumps({"op": op}))
        except Exception:
            logger.exception("failed to send %s", op)

    async def run_forever(self) -> None:
        self._stop.clear()
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.on_status(f"Disconnected: {exc}")
                logger.info("bridge session ended: %s", exc)
            if self._stop.is_set():
                break
            self.on_status(f"Reconnecting in {delay:.0f}s…")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 1.7, 30.0)
        self.on_status("Stopped.")

    async def _session(self) -> None:
        try:
            from websockets.asyncio.client import connect
        except ImportError:
            from websockets import connect  # type: ignore

        url = self.cfg.ws_url()
        self.on_status(f"Connecting to {url}…")
        async with connect(url, open_timeout=12, ping_interval=20, ping_timeout=20) as ws:
            self._ws = ws
            hello = self._hello_payload()
            await ws.send(json.dumps(hello))
            raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
            msg = json.loads(raw)
            if msg.get("op") != "hello_ok":
                raise RuntimeError(f"hello rejected: {msg!r}")
            self.bot_username = str(msg.get("bot_username") or "").lstrip("@")
            # Always sync pause state after hello (clears sticky server pause).
            await ws.send(json.dumps({"op": "pause" if self._paused else "resume"}))
            self.on_status(
                "Connected." + (" (paused)" if self._paused else "")
            )
            async for message in ws:
                if self._stop.is_set():
                    break
                await self._handle(message)
        self._ws = None
        self._fail_pending("disconnected")

    def _hello_payload(self) -> dict:
        if self.cfg.is_paired():
            return {
                "op": "hello",
                "device_id": self.cfg.device_id,
                "device_token": self.cfg.device_token,
            }
        return {
            "op": "hello",
            "token": self.cfg.token,
            "user_id": int(self.cfg.user_id),
        }

    def _fail_pending(self, reason: str) -> None:
        pending = list(self._pending.items())
        self._pending.clear()
        for _key, fut in pending:
            if not fut.done():
                fut.set_exception(RuntimeError(reason))

    async def _handle(self, message: str | bytes) -> None:
        try:
            body = json.loads(message)
        except json.JSONDecodeError:
            return
        op = body.get("op")
        if op == "open":
            url = (body.get("url") or "").strip()
            if url:
                self.on_open_url(url)
        elif op == "pong":
            pass
        elif op in (
            "roster_page_ok",
            "roster_page_err",
            "open_omni_ok",
            "open_omni_err",
            "post_grid_ok",
            "post_grid_err",
            "sets_list_ok",
            "sets_list_err",
            "register_cup_ok",
            "register_cup_err",
            "dm_craft_ok",
            "dm_craft_err",
        ):
            if op.startswith("roster_page"):
                key = "roster_page"
            elif op.startswith("open_omni"):
                key = "open_omni"
            elif op.startswith("post_grid"):
                key = "post_grid"
            elif op.startswith("register_cup"):
                key = "register_cup"
            elif op.startswith("dm_craft"):
                key = "dm_craft"
            else:
                key = "sets_list"
            fut = self._pending.pop(key, None)
            if fut is not None and not fut.done():
                fut.set_result(body)
            self.on_message(body)
        else:
            self.on_message(body)

    async def request_roster_page(
        self,
        page: int = 0,
        page_size: int = 96,
        *,
        q: str = "",
        done: int = 0,
        set_name: str = "",
        kind: str = "",
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        return await self._request(
            "roster_page",
            {
                "op": "roster_page",
                "page": int(page),
                "page_size": int(page_size),
                "q": (q or "").strip(),
                "done": int(done),
                "set": (set_name or "").strip(),
                "kind": (kind or "").strip(),
            },
            timeout=timeout,
        )

    async def request_sets_list(self, *, timeout: float = 20.0) -> dict[str, Any]:
        return await self._request(
            "sets_list",
            {"op": "sets_list"},
            timeout=timeout,
        )

    async def request_open_omni(
        self, char_id: int, *, timeout: float = 45.0
    ) -> dict[str, Any]:
        return await self._request(
            "open_omni",
            {"op": "open_omni", "char_id": int(char_id)},
            timeout=timeout,
        )

    async def request_post_grid(
        self,
        char_id: int,
        *,
        target: str = "group",
        timeout: float = 45.0,
    ) -> dict[str, Any]:
        dest = (target or "group").strip().lower()
        if dest not in ("dm", "group"):
            dest = "group"
        return await self._request(
            "post_grid",
            {
                "op": "post_grid",
                "char_id": int(char_id),
                "target": dest,
            },
            timeout=timeout,
        )

    async def request_register_cup(
        self, char_id: int, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        return await self._request(
            "register_cup",
            {"op": "register_cup", "char_id": int(char_id)},
            timeout=timeout,
        )

    async def request_dm_craft(
        self, char_id: int, craft: str, *, timeout: float = 120.0
    ) -> dict[str, Any]:
        return await self._request(
            "dm_craft",
            {"op": "dm_craft", "char_id": int(char_id), "craft": str(craft or "omni")},
            timeout=timeout,
        )

    async def _request(
        self, key: str, payload: dict, *, timeout: float
    ) -> dict[str, Any]:
        ws = self._ws
        if ws is None:
            raise RuntimeError("not connected")
        old = self._pending.pop(key, None)
        if old is not None and not old.done():
            old.set_exception(RuntimeError("superseded"))
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[key] = fut
        await ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            self._pending.pop(key, None)
            raise

    async def pair_begin(self) -> dict:
        """Open a short-lived WS, request a pairing code + deep link."""
        self.cfg.ensure_device_id()
        return await self._pair_roundtrip(
            {"op": "pair_begin", "device_id": self.cfg.device_id}
        )

    async def pair_poll(self, code: str) -> dict:
        return await self._pair_roundtrip(
            {
                "op": "pair_poll",
                "code": code,
                "device_id": self.cfg.device_id,
            }
        )

    async def pair_claim(self, code: str) -> dict:
        self.cfg.ensure_device_id()
        return await self._pair_roundtrip(
            {
                "op": "pair_claim",
                "code": code.strip(),
                "device_id": self.cfg.device_id,
            }
        )

    async def _pair_roundtrip(self, payload: dict) -> dict:
        try:
            from websockets.asyncio.client import connect
        except ImportError:
            from websockets import connect  # type: ignore

        url = self.cfg.ws_url()
        async with connect(url, open_timeout=12) as ws:
            await ws.send(json.dumps(payload))
            raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
            return json.loads(raw)
