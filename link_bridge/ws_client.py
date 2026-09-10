"""Runtime is Harem Link Bridge 1.4.19 exe bytecode (see sibling .exe.pyc).

1.4.19's frozen client never grew ``request_tournament_time``, so the alarm
could not learn today's start. Patch that on top of the exe module.
"""
from __future__ import annotations

import json
import marshal
from pathlib import Path

_code = marshal.loads(Path(__file__).with_suffix(".exe.pyc").read_bytes())
exec(_code, globals())

_orig_handle = BridgeClient._handle  # type: ignore[name-defined]


async def _handle_with_tournament_time(self, message):  # noqa: ANN001
    try:
        body = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        return await _orig_handle(self, message)
    op = body.get("op") if isinstance(body, dict) else None
    if op in ("tournament_time_ok", "tournament_time_err"):
        fut = self._pending.pop("tournament_time", None)
        if fut is not None and not fut.done():
            fut.set_result(body)
        self.on_message(body)
        return None
    return await _orig_handle(self, message)


async def request_tournament_time(self, *, timeout: float = 20.0):  # noqa: ANN001
    return await self._request(
        "tournament_time",
        {"op": "tournament_time"},
        timeout=timeout,
    )


BridgeClient._handle = _handle_with_tournament_time  # type: ignore[name-defined]
BridgeClient.request_tournament_time = request_tournament_time  # type: ignore[name-defined]

# B1: market sell/gift ops the frozen 1.4.19 client never grew. Same patch
# pattern as tournament_time: generic _request + explicit reply routing.
_BRIDGE_MARKET_OPS = ("market_sell", "market_gift")


async def request_market_sell(self, char_id: int, price: int, *, timeout: float = 20.0):  # noqa: ANN001
    # NOTE: timeout is required - the frozen _request takes it as a
    # mandatory kwarg (all frozen callers pass CALL_KW timeout).
    return await self._request(
        "market_sell",
        {"op": "market_sell", "char_id": int(char_id), "price": int(price)},
        timeout=timeout,
    )


async def request_market_gift(self, char_id: int, target: str, *, timeout: float = 20.0):  # noqa: ANN001
    return await self._request(
        "market_gift",
        {"op": "market_gift", "char_id": int(char_id), "target_user_id": str(target)},
        timeout=timeout,
    )


async def request_market_page_mine(  # noqa: ANN001
    self,
    page: int,
    page_size: int,
    q: str = "",
    min_price: str = "",
    max_price: str = "",
    *,
    timeout: float = 20.0,
):
    # Mine tab: same market_page op + mine_only flag, but a distinct pending
    # key so a fast All<->Mine switch can never resolve the wrong future.
    return await self._request(
        "market_page_mine",
        {
            "op": "market_page",
            "page": int(page),
            "page_size": int(page_size),
            "q": (q or "").strip(),
            "min_price": (min_price or "").strip(),
            "max_price": (max_price or "").strip(),
            "mine_only": 1,
        },
        timeout=timeout,
    )


_prev_market_handle = BridgeClient._handle  # type: ignore[name-defined]


async def _handle_with_bridge_market(self, message):  # noqa: ANN001
    try:
        body = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        return await _prev_market_handle(self, message)
    op = body.get("op") if isinstance(body, dict) else None
    if isinstance(op, str) and op.rsplit("_", 1)[0] in _BRIDGE_MARKET_OPS and op.rsplit("_", 1)[-1] in ("ok", "err"):
        key = op.rsplit("_", 1)[0]
        fut = self._pending.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_result(body)
        self.on_message(body)
        return None
    if (
        isinstance(body, dict)
        and op == "market_page_ok"
        and body.get("mine_only")
    ):
        # Mine-tab reply: resolve the dedicated pending key, never the
        # shared market_page one (see request_market_page_mine).
        fut = self._pending.pop("market_page_mine", None)
        if fut is not None and not fut.done():
            fut.set_result(body)
        self.on_message(body)
        return None
    return await _prev_market_handle(self, message)


BridgeClient._handle = _handle_with_bridge_market  # type: ignore[name-defined]
BridgeClient.request_market_sell = request_market_sell  # type: ignore[name-defined]
BridgeClient.request_market_gift = request_market_gift  # type: ignore[name-defined]
BridgeClient.request_market_page_mine = request_market_page_mine  # type: ignore[name-defined]
