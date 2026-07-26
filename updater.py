"""Client-side version check + self-update for the frozen .exe.

Flow:
  1. GET ``http://{host}:{update_port}/version.json``
  2. If remote version > local, download beside the exe as ``*.exe.new``
  3. Write ``_update_bridge.bat`` that waits for this process to exit, then
     renames the old exe out of the way, moves the new one in, and relaunches
  4. Launch the bat and quit

Windows cannot reliably ``move`` over an exe that was just running; renaming the
old file aside first is the supported pattern. Retries + ping-delays avoid the
broken ``timeout`` command in non-interactive consoles.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from link_bridge import __version__
from link_bridge.config import BridgeConfig, app_dir

logger = logging.getLogger(__name__)

DEFAULT_UPDATE_PORT = 8766
EXE_NAME = "HaremLinkBridge.exe"
MANIFEST_NAME = "version.json"
UPDATE_BAT = "_update_bridge.bat"
CREATE_NO_WINDOW = 0x08000000


@dataclass
class UpdateInfo:
    version: str
    url: str
    sha256: str = ""
    size: int = 0


def parse_version(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in (v or "0").strip().lstrip("v").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num or 0))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def manifest_url(cfg: BridgeConfig) -> str:
    custom = (getattr(cfg, "update_url", None) or "").strip()
    if custom:
        return custom
    host = (cfg.host or "").strip() or "108.165.174.158"
    port = int(getattr(cfg, "update_port", 0) or DEFAULT_UPDATE_PORT)
    return f"http://{host}:{port}/{MANIFEST_NAME}"


def fetch_manifest(cfg: BridgeConfig, *, timeout: float = 8.0) -> UpdateInfo | None:
    url = manifest_url(cfg)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("update check failed (%s): %s", url, exc)
        return None
    if not isinstance(raw, dict):
        return None
    version = str(raw.get("version") or "").strip()
    if not version:
        return None
    file_url = str(raw.get("url") or "").strip()
    if not file_url:
        base = url.rsplit("/", 1)[0]
        file_url = f"{base}/{raw.get('filename') or EXE_NAME}"
    return UpdateInfo(
        version=version,
        url=file_url,
        sha256=str(raw.get("sha256") or "").strip().lower(),
        size=int(raw.get("size") or 0),
    )


def check_for_update(cfg: BridgeConfig) -> UpdateInfo | None:
    """Return remote update info when newer than this build; else None."""
    info = fetch_manifest(cfg)
    if info is None:
        return None
    if not is_newer(info.version, __version__):
        return None
    return info


def _exe_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return app_dir() / EXE_NAME


def download_update(info: UpdateInfo, dest: Path, *, timeout: float = 120.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists():
        tmp.unlink()
    req = urllib.request.Request(info.url, headers={"User-Agent": f"HaremLinkBridge/{__version__}"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
    digest = h.hexdigest()
    if info.sha256 and digest != info.sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"sha256 mismatch: got {digest}, expected {info.sha256}")
    if dest.exists():
        dest.unlink()
    tmp.replace(dest)
    return dest


def build_update_bat(*, pid: int, current: Path, new_exe: Path) -> str:
    """Return cmd.exe script text that swaps ``new_exe`` onto ``current`` after ``pid`` exits."""
    target = str(current)
    new_path = str(new_exe)
    bak_name = current.name + ".bak"
    exe_name = current.name
    log_path = str(current.parent / "_update_fail.txt")
    # ren only accepts a bare filename as the destination name.
    return "\r\n".join(
        [
            "@echo off",
            "setlocal EnableExtensions",
            f'set "TARGET={target}"',
            f'set "NEW={new_path}"',
            f'set "BAKNAME={bak_name}"',
            f'set "EXENAME={exe_name}"',
            f'set "LOG={log_path}"',
            "set /a TRIES=0",
            ":waitloop",
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
            "if not errorlevel 1 (",
            "  ping -n 2 127.0.0.1 >nul",
            "  goto waitloop",
            ")",
            "ping -n 2 127.0.0.1 >nul",
            ":retry",
            "set /a TRIES+=1",
            'if exist "%TARGET%.bak" del /F /Q "%TARGET%.bak" >nul 2>&1',
            'if exist "%TARGET%" (',
            '  ren "%TARGET%" "%BAKNAME%" >nul 2>&1',
            ")",
            'if exist "%TARGET%" (',
            "  if %TRIES% LSS 40 (",
            "    ping -n 2 127.0.0.1 >nul",
            "    goto retry",
            "  )",
            '  echo rename_old_failed> "%LOG%"',
            "  exit /b 1",
            ")",
            'move /Y "%NEW%" "%TARGET%" >nul',
            'if not exist "%TARGET%" (',
            '  echo move_new_failed> "%LOG%"',
            '  if exist "%TARGET%.bak" ren "%TARGET%.bak" "%EXENAME%" >nul 2>&1',
            "  exit /b 1",
            ")",
            'del /F /Q "%TARGET%.bak" >nul 2>&1',
            'if exist "%LOG%" del /F /Q "%LOG%" >nul 2>&1',
            'start "" "%TARGET%"',
            'del "%~f0"',
            "",
        ]
    )


def apply_update_and_restart(new_exe: Path) -> None:
    """Replace the running frozen exe via a short-lived bat, then exit."""
    current = _exe_path()
    if not getattr(sys, "frozen", False):
        logger.info("dev mode: downloaded update to %s (not applying)", new_exe)
        return
    bat = app_dir() / UPDATE_BAT
    bat.write_text(
        build_update_bat(pid=os.getpid(), current=current, new_exe=new_exe),
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat)],
        cwd=str(app_dir()),
        close_fds=True,
        creationflags=CREATE_NO_WINDOW,
    )


def run_update(cfg: BridgeConfig, info: UpdateInfo) -> Path:
    dest = app_dir() / f"{EXE_NAME}.new"
    path = download_update(info, dest)
    apply_update_and_restart(path)
    return path
