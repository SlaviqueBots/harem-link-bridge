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


class BridgeClient:
    """Reconnecting WebSocket client. Runs inside an asyncio event loop."""

    def __init__(
        self,
        cfg: BridgeConfig,
        *,
        on_status: StatusCb | None = None,
        on_open_url: OpenCb | None = None,
    ) -> None:
        self.cfg = cfg
        self.on_status = on_status or (lambda _s: None)
        self.on_open_url = on_open_url or (lambda _u: None)
        self._ws: Any = None
        self._stop = asyncio.Event()
        self._paused = bool(cfg.paused)
        self._mute_auc_dm = bool(cfg.mute_auc_dm)
        self._task: asyncio.Task | None = None

    @property
    def paused(self) -> bool:
        return self._paused

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

    async def set_mute_auc_dm(self, muted: bool) -> None:
        self._mute_auc_dm = bool(muted)
        self.cfg.mute_auc_dm = self._mute_auc_dm
        ws = self._ws
        if ws is None:
            return
        op = "mute_auc_dm" if self._mute_auc_dm else "unmute_auc_dm"
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
            if self._paused:
                await ws.send(json.dumps({"op": "pause"}))
            if self._mute_auc_dm:
                await ws.send(json.dumps({"op": "mute_auc_dm"}))
            self.on_status(
                "Connected."
                + (" (paused)" if self._paused else "")
                + (" (auc DMs muted)" if self._mute_auc_dm else "")
            )
            async for message in ws:
                if self._stop.is_set():
                    break
                await self._handle(message)
        self._ws = None

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
