"""Client-side version check + self-update for the frozen .exe.

Flow (same approach as Harem Link Bridge):
  1. GET ``http://{host}:{update_port}/version.json``
  2. If remote version > local, download beside the exe as ``*.exe.new``
  3. Write ``_update_conjure_finder.ps1`` that waits for this process to exit,
     swaps the exe (rename old aside, copy new in), then opens the folder so
     the user can start the new build themselves — no auto-relaunch (unreliable
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

from conjure_finder import __version__
from conjure_finder.bootstrap import ROOT

logger = logging.getLogger(__name__)

DEFAULT_UPDATE_HOST = "108.165.174.158"
DEFAULT_UPDATE_PORT = 8767
# Local frozen name (PyInstaller ``--name=Conjure Finder``).
EXE_NAME = "Conjure Finder.exe"
# Space-free name on the update HTTP server / in the manifest URL.
REMOTE_EXE_NAME = "ConjureFinder.exe"
MANIFEST_NAME = "version.json"
UPDATE_BAT = "_update_conjure_finder.bat"
UPDATE_PS1 = "_update_conjure_finder.ps1"
CREATE_NO_WINDOW = 0x08000000

# Sidecar files / tools that MUST survive an auto-update. The PowerShell swap
# only touches Conjure Finder.exe (+ .new / .bak) and its own helper scripts.
PROTECTED_SIDECARS: tuple[str, ...] = (
    "conjure_finder.env",
    "Sparse Tag Browser.exe",
    "SparseTagBrowser.exe",
    "sparse_tags_favourites.json",
    "sparse_tags_prefs.json",
    "sparse_tags_keys_path.txt",
)


@dataclass
class UpdateConfig:
    check_updates: bool = True
    update_url: str = ""
    update_host: str = DEFAULT_UPDATE_HOST
    update_port: int = DEFAULT_UPDATE_PORT


@dataclass
class UpdateInfo:
    version: str
    url: str
    sha256: str = ""
    size: int = 0


def load_update_config() -> UpdateConfig:
    check_raw = (os.environ.get("CHECK_UPDATES") or "true").strip().lower()
    check = check_raw not in ("0", "false", "no", "off")
    port_raw = (os.environ.get("UPDATE_PORT") or "").strip()
    try:
        port = int(port_raw) if port_raw else DEFAULT_UPDATE_PORT
    except ValueError:
        port = DEFAULT_UPDATE_PORT
    return UpdateConfig(
        check_updates=check,
        update_url=(os.environ.get("UPDATE_URL") or "").strip(),
        update_host=(os.environ.get("UPDATE_HOST") or DEFAULT_UPDATE_HOST).strip()
        or DEFAULT_UPDATE_HOST,
        update_port=port,
    )


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


def manifest_url(cfg: UpdateConfig) -> str:
    custom = (cfg.update_url or "").strip()
    if custom:
        return custom
    host = (cfg.update_host or "").strip() or DEFAULT_UPDATE_HOST
    port = int(cfg.update_port or DEFAULT_UPDATE_PORT)
    return f"http://{host}:{port}/{MANIFEST_NAME}"


def fetch_manifest(cfg: UpdateConfig, *, timeout: float = 8.0) -> UpdateInfo | None:
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
        file_url = f"{base}/{raw.get('filename') or REMOTE_EXE_NAME}"
    return UpdateInfo(
        version=version,
        url=file_url,
        sha256=str(raw.get("sha256") or "").strip().lower(),
        size=int(raw.get("size") or 0),
    )


def check_for_update(cfg: UpdateConfig | None = None) -> UpdateInfo | None:
    """Return remote update info when newer than this build; else None."""
    cfg = cfg or load_update_config()
    info = fetch_manifest(cfg)
    if info is None:
        return None
    if not is_newer(info.version, __version__):
        return None
    return info


def _exe_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return ROOT / EXE_NAME


def download_update(info: UpdateInfo, dest: Path, *, timeout: float = 120.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists():
        tmp.unlink()
    req = urllib.request.Request(
        info.url, headers={"User-Agent": f"ConjureFinder/{__version__}"}
    )
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
    """PowerShell swap only — never relaunches the app.

    Hard rule: only ``Conjure Finder.exe`` (+ ``.new`` / ``.bak``) and this
    script's own helper files are touched. Co-located Sparse Tag Browser,
    ``conjure_finder.env``, and ``sparse_tags_*.json`` are never deleted.
    """
    target = str(current)
    new_path = str(new_exe)
    bak = str(current) + ".bak"
    log_path = str(current.parent / "_update_fail.txt")
    note_path = str(current.parent / "_UPDATE_START_HERE.txt")
    expected_leaf = EXE_NAME

    def q(p: str) -> str:
        return "'" + p.replace("'", "''") + "'"

    protected_list = ", ".join(PROTECTED_SIDECARS)

    return "\r\n".join(
        [
            "$ErrorActionPreference = 'Continue'",
            f"$pidToWait = {int(pid)}",
            f"$target = {q(target)}",
            f"$new = {q(new_path)}",
            f"$bak = {q(bak)}",
            f"$log = {q(log_path)}",
            f"$note = {q(note_path)}",
            f"$expectedLeaf = {q(expected_leaf)}",
            "function Write-Fail([string]$msg) {",
            "  Set-Content -LiteralPath $log -Value $msg -Encoding UTF8",
            "}",
            "try {",
            "  # Refuse to run if someone pointed the updater at the wrong file.",
            "  if ([IO.Path]::GetFileName($target) -ne $expectedLeaf) {",
            "    Write-Fail ('refusing_update_wrong_target:' + $target)",
            "    exit 1",
            "  }",
            "  if (-not $new.EndsWith(($expectedLeaf + '.new'))) {",
            "    Write-Fail ('refusing_update_wrong_new:' + $new)",
            "    exit 1",
            "  }",
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
            f"  # Protected sidecars (never deleted by this script): {protected_list}",
            "  Set-Content -LiteralPath $note -Value 'Update installed. Double-click Conjure Finder.exe to start.' -Encoding UTF8",
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
    directory = ROOT
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


def run_update(cfg: UpdateConfig | None, info: UpdateInfo) -> Path:
    # Always download beside the running exe under a fixed name — never overwrite
    # Sparse Tag Browser.exe or user JSON / env files.
    dest = ROOT / f"{EXE_NAME}.new"
    if dest.name != f"{EXE_NAME}.new":
        raise RuntimeError(f"refusing update dest {dest!s}")
    path = download_update(info, dest)
    apply_update_and_restart(path)
    return path
