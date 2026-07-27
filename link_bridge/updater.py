"""Client-side version check + self-update for the frozen .exe.

Flow:
  1. GET ``http://{host}:{update_port}/version.json``
  2. If remote version > local, download beside the exe as ``*.exe.new``
  3. Write ``_update_bridge.ps1`` that waits for this process to exit, swaps the
     exe (rename old aside, copy new in), then opens the folder so the user can
     start the new build themselves — no auto-relaunch (that path was unreliable
     on Windows / PyInstaller)
  4. Launch PowerShell via ``cmd start`` (fully detached) and quit
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
UPDATE_PS1 = "_update_bridge.ps1"
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
    if info.size and tmp.stat().st_size != int(info.size):
        size = tmp.stat().st_size
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch: got {size}, expected {info.size}")
    if dest.exists():
        dest.unlink()
    tmp.replace(dest)
    return dest


def build_update_ps1(*, pid: int, current: Path, new_exe: Path) -> str:
    """PowerShell swap only — never relaunches the app."""
    target = str(current)
    new_path = str(new_exe)
    bak = str(current) + ".bak"
    log_path = str(current.parent / "_update_fail.txt")
    note_path = str(current.parent / "_UPDATE_START_HERE.txt")

    def q(p: str) -> str:
        return "'" + p.replace("'", "''") + "'"

    return "\r\n".join(
        [
            "$ErrorActionPreference = 'Continue'",
            f"$pidToWait = {int(pid)}",
            f"$target = {q(target)}",
            f"$new = {q(new_path)}",
            f"$bak = {q(bak)}",
            f"$log = {q(log_path)}",
            f"$note = {q(note_path)}",
            "function Write-Fail([string]$msg) {",
            "  Set-Content -LiteralPath $log -Value $msg -Encoding UTF8",
            "}",
            "try {",
            "  $deadline = (Get-Date).AddSeconds(120)",
            "  while ((Get-Date) -lt $deadline) {",
            "    $proc = Get-Process -Id $pidToWait -ErrorAction SilentlyContinue",
            "    if (-not $proc) { break }",
            "    Start-Sleep -Milliseconds 400",
            "  }",
            "  Start-Sleep -Seconds 2",
            "  if (Test-Path -LiteralPath $bak) {",
            "    Remove-Item -LiteralPath $bak -Force -ErrorAction SilentlyContinue",
            "  }",
            "  $renamed = $false",
            "  for ($i = 0; $i -lt 50; $i++) {",
            "    if (-not (Test-Path -LiteralPath $target)) { $renamed = $true; break }",
            "    try {",
            "      Move-Item -LiteralPath $target -Destination $bak -Force -ErrorAction Stop",
            "      $renamed = $true",
            "      break",
            "    } catch {",
            "      Start-Sleep -Milliseconds 500",
            "    }",
            "  }",
            "  if (-not $renamed -and (Test-Path -LiteralPath $target)) {",
            "    Write-Fail 'rename_old_failed'",
            "    exit 1",
            "  }",
            "  $copied = $false",
            "  for ($i = 0; $i -lt 50; $i++) {",
            "    try {",
            "      Copy-Item -LiteralPath $new -Destination $target -Force -ErrorAction Stop",
            "      Remove-Item -LiteralPath $new -Force -ErrorAction SilentlyContinue",
            "      $copied = $true",
            "      break",
            "    } catch {",
            "      Start-Sleep -Milliseconds 500",
            "    }",
            "  }",
            "  if (-not $copied -or -not (Test-Path -LiteralPath $target)) {",
            "    Write-Fail 'copy_new_failed'",
            "    if (Test-Path -LiteralPath $bak) {",
            "      Move-Item -LiteralPath $bak -Destination $target -Force -ErrorAction SilentlyContinue",
            "    }",
            "    exit 1",
            "  }",
            "  Remove-Item -LiteralPath $bak -Force -ErrorAction SilentlyContinue",
            "  if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue }",
            "  Set-Content -LiteralPath $note -Value 'Update installed. Double-click HaremLinkBridge.exe to start.' -Encoding UTF8",
            "  try { Start-Process explorer.exe -ArgumentList ('/select,' + $target) } catch {}",
            "  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue",
            "  exit 0",
            "} catch {",
            "  Write-Fail $_.Exception.Message",
            "  exit 1",
            "}",
            "",
        ]
    )


def build_update_bat(*, pid: int, current: Path, new_exe: Path) -> str:
    """Tiny launcher that starts the PowerShell updater (kept for tests / fallback)."""
    ps1 = current.parent / UPDATE_PS1
    return "\r\n".join(
        [
            "@echo off",
            f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ps1}"',
            'del "%~f0" >nul 2>&1',
            "",
        ]
    )


def apply_update_and_restart(new_exe: Path) -> None:
    """Replace the running frozen exe via PowerShell (no relaunch), then exit."""
    current = _exe_path()
    if not getattr(sys, "frozen", False):
        logger.info("dev mode: downloaded update to %s (not applying)", new_exe)
        return
    directory = app_dir()
    ps1 = directory / UPDATE_PS1
    bat = directory / UPDATE_BAT
    ps1.write_text(
        build_update_ps1(pid=os.getpid(), current=current, new_exe=new_exe),
        encoding="utf-8",
    )
    bat.write_text(
        build_update_bat(pid=os.getpid(), current=current, new_exe=new_exe),
        encoding="utf-8",
    )
    # ``cmd /c start`` fully detaches the updater from our process tree.
    subprocess.Popen(
        [
            "cmd.exe",
            "/c",
            "start",
            "",
            "/MIN",
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(ps1),
        ],
        cwd=str(directory),
        close_fds=True,
        creationflags=CREATE_NO_WINDOW,
    )


def run_update(cfg: BridgeConfig, info: UpdateInfo) -> Path:
    dest = app_dir() / f"{EXE_NAME}.new"
    path = download_update(info, dest)
    apply_update_and_restart(path)
    return path
