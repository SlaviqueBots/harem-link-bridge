"""Local daily-tournament alarm (soft looping chime). Time comes from the bot."""

from __future__ import annotations

import logging
import math
import struct
import tempfile
import wave
from typing import Any

logger = logging.getLogger(__name__)

LEAD_SEC = 60.0
_WAV_PATH = ""


def delay_ms_until_ring(now: float, start_ts: float | None) -> int | None:
    """Milliseconds until the 1-minute warning, 0 if due now, None if skip."""
    if start_ts is None:
        return None
    start = float(start_ts)
    fire_at = start - LEAD_SEC
    if now >= start:
        return None
    if now >= fire_at:
        return 0
    return max(1, int((fire_at - now) * 1000))


def should_ring(
    now: float,
    start_ts: float | None,
    *,
    enabled: bool,
    fired_day: str,
    day: str,
) -> bool:
    if not enabled or not day or day == fired_day:
        return False
    return delay_ms_until_ring(now, start_ts) == 0


def _soft_loop_wav() -> str:
    global _WAV_PATH
    if _WAV_PATH:
        return _WAV_PATH
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    rate = 22050
    duration = 2.6
    n = int(rate * duration)
    amp = 1600
    frames = bytearray()
    fade = int(rate * 0.09)
    for i in range(n):
        t = i / rate
        env_a = math.exp(-t * 1.55)
        env_b = math.exp(-(t - 1.25) * 1.55) if t >= 1.25 else 0.0
        env_b = max(0.0, env_b)
        seam = 1.0
        if i < fade:
            seam = i / fade
        elif i > n - fade:
            seam = max(0.0, (n - i) / fade)
        wave_s = env_a * math.sin(2 * math.pi * 329.63 * t) + env_b * math.sin(
            2 * math.pi * 392.00 * t
        )
        sample = int(amp * seam * wave_s)
        sample = max(-32767, min(32767, sample))
        frames.extend(struct.pack("<h", sample))
    with wave.open(tmp.name, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    tmp.close()
    _WAV_PATH = tmp.name
    return _WAV_PATH


def start_loop() -> None:
    try:
        import winsound
    except ImportError:
        return
    try:
        path = _soft_loop_wav()
        winsound.PlaySound(
            path,
            winsound.SND_FILENAME
            | winsound.SND_ASYNC
            | winsound.SND_LOOP
            | winsound.SND_NODEFAULT,
        )
    except Exception:
        logger.debug("tournament alarm start failed", exc_info=True)


def stop_loop() -> None:
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        logger.debug("tournament alarm stop failed", exc_info=True)


def parse_ok(body: dict[str, Any]) -> dict[str, Any] | None:
    if (body or {}).get("op") != "tournament_time_ok":
        return None
    hour = body.get("hour")
    start_ts = body.get("start_ts")
    try:
        hour_i = int(hour) if hour is not None else None
    except (TypeError, ValueError):
        hour_i = None
    try:
        start_f = float(start_ts) if start_ts is not None else None
    except (TypeError, ValueError):
        start_f = None
    return {
        "day": str(body.get("day") or ""),
        "finalized": bool(body.get("finalized")),
        "hour": hour_i,
        "start_ts": start_f,
        "now_ts": float(body.get("now_ts") or 0.0),
    }
