"""Extract a still frame from a remote video for Tk previews.

Uses the ffmpeg binary shipped with ``imageio-ffmpeg`` (bundled into the
standalone exe). Falls back to PATH ``ffmpeg`` only for local DEV.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE = Path(tempfile.gettempdir()) / "HaremLinkBridgeVideoStills"
_ffmpeg_exe: str | None | bool = False  # False = unresolved


def _resolve_ffmpeg() -> str | None:
    global _ffmpeg_exe
    if _ffmpeg_exe is not False:
        return _ffmpeg_exe if isinstance(_ffmpeg_exe, str) else None
    exe: str | None = None
    try:
        import imageio_ffmpeg

        candidate = imageio_ffmpeg.get_ffmpeg_exe()
        if candidate and Path(candidate).is_file():
            exe = candidate
    except Exception:
        logger.debug("imageio_ffmpeg unavailable", exc_info=True)
    if not exe:
        exe = shutil.which("ffmpeg")
    _ffmpeg_exe = exe
    return exe


def ffmpeg_available() -> bool:
    return bool(_resolve_ffmpeg())


def _cache_path(url: str) -> Path:
    key = hashlib.sha256((url or "").encode("utf-8", errors="replace")).hexdigest()[:40]
    return _CACHE / f"{key}.jpg"


def extract_video_still_bytes(url: str, *, timeout: float = 45.0) -> bytes:
    """Return JPEG bytes for the first frame of ``url``."""
    target = (url or "").strip()
    if not target:
        raise RuntimeError("empty video url")
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not available (imageio-ffmpeg missing)")

    _CACHE.mkdir(parents=True, exist_ok=True)
    dest = _cache_path(target)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest.read_bytes()

    tmp = dest.with_suffix(".part.jpg")
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        "0",
        "-i",
        target,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-update",
        "1",
        str(tmp),
    ]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=max(5.0, float(timeout)),
            creationflags=flags,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg timed out") from exc
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:240]
        raise RuntimeError(err or f"ffmpeg failed ({proc.returncode})")
    try:
        tmp.replace(dest)
    except OSError:
        data = tmp.read_bytes()
        try:
            tmp.unlink()
        except OSError:
            pass
        return data
    return dest.read_bytes()
