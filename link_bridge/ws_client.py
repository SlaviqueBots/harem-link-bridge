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
        self._ping_task: asyncio.Task | None = None
        self.bot_username: str = ""
        self.themes_admin: bool = False

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
            replaced = False
            try:
                await self._session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                text = str(exc)
                replaced = "4003" in text or "replaced" in text.lower()
                if replaced:
                    self.on_status(
                        "Disconnected: another Link Bridge took this session "
                        "(close other copies — published exe + DEV fight)."
                    )
                else:
                    self.on_status(f"Disconnected: {exc}")
                logger.info("bridge session ended: %s", exc)
            if self._stop.is_set():
                break
            # After a replace, wait longer so we don't ping-pong with the winner.
            if replaced:
                delay = max(delay, 12.0)
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
        try:
            async with connect(
                url, open_timeout=12, ping_interval=20, ping_timeout=20
            ) as ws:
                self._ws = ws
                hello = self._hello_payload()
                await ws.send(json.dumps(hello))
                raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
                msg = json.loads(raw)
                if msg.get("op") != "hello_ok":
                    raise RuntimeError(f"hello rejected: {msg!r}")
                self.bot_username = str(msg.get("bot_username") or "").lstrip("@")
                self.themes_admin = bool(msg.get("themes_admin"))
                # Always sync pause state after hello (clears sticky server pause).
                await ws.send(
                    json.dumps({"op": "pause" if self._paused else "resume"})
                )
                self.on_status(
                    "Connected." + (" (paused)" if self._paused else "")
                )
                self.on_message(msg)
                self._ping_task = asyncio.create_task(self._ping_loop(ws))
                try:
                    async for message in ws:
                        if self._stop.is_set():
                            break
                        await self._handle(message)
                finally:
                    ping = self._ping_task
                    self._ping_task = None
                    if ping is not None:
                        ping.cancel()
                        try:
                            await ping
                        except asyncio.CancelledError:
                            pass
        except Exception as exc:
            # Surface websocket close code 4003 ("replaced") clearly.
            name = type(exc).__name__
            text = str(exc)
            if "4003" in text or (
                "ConnectionClosed" in name and "replaced" in text.lower()
            ):
                raise RuntimeError(
                    "received 4003 (private use) replaced; then sent 4003 "
                    "(private use) replaced"
                ) from exc
            raise
        finally:
            self._ws = None
            self._fail_pending("disconnected")

    async def _ping_loop(self, ws: Any) -> None:
        """Application-level keepalive — catches half-open sessions sooner."""
        while not self._stop.is_set():
            try:
                await asyncio.sleep(45.0)
            except asyncio.CancelledError:
                raise
            if self._stop.is_set() or self._ws is not ws:
                break
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except Exception:
                break

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
            "sets_rename_ok",
            "sets_rename_err",
            "sets_delete_ok",
            "sets_delete_err",
            "register_cup_ok",
            "register_cup_err",
            "dm_craft_ok",
            "dm_craft_err",
            "themes_list_ok",
            "themes_list_err",
            "themes_save_ok",
            "themes_save_err",
            "browse_users_ok",
            "browse_users_err",
            "market_page_ok",
            "market_page_err",
            "market_buy_ok",
            "market_buy_err",
            "omni_state_ok",
            "omni_state_err",
            "omni_tap_ok",
            "omni_tap_err",
            "checkres_url_ok",
            "checkres_url_err",
            "conjure_result_dm_ok",
            "conjure_result_dm_err",
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
            elif op.startswith("themes_list"):
                key = "themes_list"
            elif op.startswith("themes_save"):
                key = "themes_save"
            elif op.startswith("market_page"):
                key = "market_page"
            elif op.startswith("market_buy"):
                key = "market_buy"
            elif op.startswith("omni_state"):
                cid = int(body.get("char_id") or 0)
                key = f"omni_state:{cid}" if cid else "omni_state"
            elif op.startswith("omni_tap"):
                cid = int(body.get("char_id") or 0)
                key = f"omni_tap:{cid}" if cid else "omni_tap"
            elif op.startswith("checkres_url"):
                key = "checkres_url"
            elif op.startswith("conjure_result_dm"):
                key = "conjure_result_dm"
            elif op.startswith("browse_users"):
                kind = str(body.get("kind") or "roster").strip().lower() or "roster"
                key = f"browse_users:{kind}"
                # Also accept a bare key for older pending waits.
                fut = self._pending.pop(key, None)
                if fut is None:
                    fut = self._pending.pop("browse_users", None)
                if fut is not None and not fut.done():
                    fut.set_result(body)
                self.on_message(body)
                return
            elif op.startswith("sets_rename"):
                key = "sets_rename"
            elif op.startswith("sets_delete"):
                key = "sets_delete"
            else:
                key = "sets_list"
            fut = self._pending.pop(key, None)
            if fut is None and ":" in key:
                fut = self._pending.pop(key.split(":")[0], None)
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

    async def request_sets_list(
        self, *, user: str = "", timeout: float = 20.0
    ) -> dict[str, Any]:
        return await self._request(
            "sets_list",
            {
                "op": "sets_list",
                "user": (user or "").strip().lstrip("@"),
            },
            timeout=timeout,
        )

    async def request_sets_rename(
        self, old: str, new: str, *, timeout: float = 20.0
    ) -> dict[str, Any]:
        return await self._request(
            "sets_rename",
            {
                "op": "sets_rename",
                "old": (old or "").strip(),
                "new": (new or "").strip(),
            },
            timeout=timeout,
        )

    async def request_sets_delete(
        self, name: str, *, timeout: float = 20.0
    ) -> dict[str, Any]:
        return await self._request(
            "sets_delete",
            {
                "op": "sets_delete",
                "name": (name or "").strip(),
            },
            timeout=timeout,
        )

    async def request_sets_present(
        self,
        name: str,
        *,
        target: str = "group",
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        dest = "dm" if str(target or "").strip().lower() == "dm" else "group"
        return await self._request(
            "sets_present",
            {
                "op": "sets_present",
                "set_name": (name or "").strip(),
                "target": dest,
            },
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

    async def request_checkres_url(
        self, url: str, *, timeout: float = 90.0
    ) -> dict[str, Any]:
        return await self._request(
            "checkres_url",
            {"op": "checkres_url", "url": str(url or "").strip()},
            timeout=timeout,
        )

    async def request_conjure_result_dm(
        self, url: str, text: str, *, command: str = "", timeout: float = 30.0
    ) -> dict[str, Any]:
        return await self._request(
            "conjure_result_dm",
            {
                "op": "conjure_result_dm",
                "url": str(url or "").strip(),
                "text": str(text or ""),
                "command": str(command or "").strip(),
            },
            timeout=timeout,
        )

    async def request_themes_list(self, *, timeout: float = 30.0) -> dict[str, Any]:
        return await self._request(
            "themes_list",
            {"op": "themes_list"},
            timeout=timeout,
        )

    async def request_themes_save(
        self,
        main: list[str],
        secondary: list[str],
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        return await self._request(
            "themes_save",
            {
                "op": "themes_save",
                "main": list(main or []),
                "secondary": list(secondary or []),
            },
            timeout=timeout,
        )

    async def request_browse_users(
        self, kind: str, *, timeout: float = 20.0
    ) -> dict[str, Any]:
        k = (kind or "roster").strip().lower()
        allowed = {
            "tamed",
            "sets",
            "roster",
            "roster_done",
            "roster_undone",
            "roster_flavoured",
            "roster_unflavoured",
            "done",
            "undone",
            "flavoured",
            "unflavoured",
        }
        if k not in allowed:
            k = "roster"
        return await self._request(
            f"browse_users:{k}",
            {"op": "browse_users", "kind": k},
            timeout=timeout,
        )

    async def request_market_page(
        self,
        page: int = 0,
        page_size: int = 96,
        *,
        q: str = "",
        min_price: str = "",
        max_price: str = "",
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "op": "market_page",
            "page": int(page),
            "page_size": int(page_size),
            "q": (q or "").strip(),
        }
        if (min_price or "").strip() != "":
            payload["min_price"] = (min_price or "").strip()
        if (max_price or "").strip() != "":
            payload["max_price"] = (max_price or "").strip()
        return await self._request("market_page", payload, timeout=timeout)

    async def request_market_buy(
        self, listing_id: int, *, timeout: float = 45.0
    ) -> dict[str, Any]:
        return await self._request(
            "market_buy",
            {"op": "market_buy", "listing_id": int(listing_id)},
            timeout=timeout,
        )

    async def request_omni_state(
        self, char_id: int, *, mode: str = "omni", timeout: float = 20.0
    ) -> dict[str, Any]:
        return await self._request(
            f"omni_state:{int(char_id)}",
            {
                "op": "omni_state",
                "char_id": int(char_id),
                "mode": str(mode or "omni"),
            },
            timeout=timeout,
        )

    async def request_omni_tap(
        self,
        char_id: int,
        craft: str,
        arg: str | None = None,
        *,
        mode: str = "omni",
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "op": "omni_tap",
            "char_id": int(char_id),
            "craft": str(craft or ""),
            "mode": str(mode or "omni"),
        }
        if arg is not None:
            payload["arg"] = arg
        return await self._request(f"omni_tap:{int(char_id)}", payload, timeout=timeout)

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
