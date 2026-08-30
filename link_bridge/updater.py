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
from typing import Callable

from link_bridge import __version__
from link_bridge.config import BridgeConfig, app_dir

logger = logging.getLogger(__name__)

DEFAULT_UPDATE_PORT = 8766
EXE_NAME = "HaremLinkBridge.exe"
USERSCRIPT_NAME = "harem_bridge_send.user.js"
MANIFEST_NAME = "version.json"
UPDATE_BAT = "_update_bridge.bat"
UPDATE_PS1 = "_update_bridge.ps1"
CREATE_NO_WINDOW = 0x08000000

# Sidecar files beside the exe that MUST survive an auto-update.
PROTECTED_SIDECARS: tuple[str, ...] = (
    "conjure_finder.env",
    "harem_link_bridge.json",
    USERSCRIPT_NAME,
    "conjure_finder_findings.json",
    "crafting_plans.json",
)


@dataclass
class SidecarFile:
    filename: str
    url: str
    sha256: str = ""
    size: int = 0


@dataclass
class UpdateInfo:
    version: str
    url: str
    sha256: str = ""
    size: int = 0
    userscript: SidecarFile | None = None
    url_github: str = ""


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


def _parse_userscript(raw: dict, *, manifest_url: str) -> SidecarFile | None:
    block = raw.get("userscript")
    if not isinstance(block, dict):
        return None
    filename = str(block.get("filename") or USERSCRIPT_NAME).strip() or USERSCRIPT_NAME
    file_url = str(block.get("url") or "").strip()
    if not file_url:
        base = manifest_url.rsplit("/", 1)[0]
        file_url = f"{base}/{filename}"
    return SidecarFile(
        filename=filename,
        url=file_url,
        sha256=str(block.get("sha256") or "").strip().lower(),
        size=int(block.get("size") or 0),
    )


def _parse_manifest(raw: dict, *, manifest_url: str) -> UpdateInfo | None:
    if not isinstance(raw, dict):
        return None
    version = str(raw.get("version") or "").strip()
    if not version:
        return None
    file_url = str(raw.get("url") or "").strip()
    if not file_url:
        base = manifest_url.rsplit("/", 1)[0]
        file_url = f"{base}/{raw.get('filename') or EXE_NAME}"
    url_github = str(raw.get("url_github") or "").strip()
    return UpdateInfo(
        version=version,
        url=file_url,
        sha256=str(raw.get("sha256") or "").strip().lower(),
        size=int(raw.get("size") or 0),
        userscript=_parse_userscript(raw, manifest_url=manifest_url),
        url_github=url_github,
    )


def fetch_manifest(cfg: BridgeConfig, *, timeout: float = 8.0) -> UpdateInfo | None:
    url = manifest_url(cfg)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("update check failed (%s): %s", url, exc)
        return None
    return _parse_manifest(raw, manifest_url=url)


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


def userscript_path() -> Path:
    return app_dir() / USERSCRIPT_NAME


def download_sidecar(
    item: SidecarFile,
    dest: Path,
    *,
    timeout: float = 30.0,
) -> Path:
    """Download a small sidecar file (userscript) beside the exe."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists():
        tmp.unlink()
    req = urllib.request.Request(
        item.url, headers={"User-Agent": f"HaremLinkBridge/{__version__}"}
    )
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 64)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
    digest = h.hexdigest()
    if item.sha256 and digest != item.sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"sha256 mismatch for {dest.name}: got {digest}, expected {item.sha256}")
    if item.size and tmp.stat().st_size != int(item.size):
        size = tmp.stat().st_size
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch for {dest.name}: got {size}, expected {item.size}")
    if dest.exists():
        dest.unlink()
    tmp.replace(dest)
    return dest


def download_userscript(info: UpdateInfo) -> Path | None:
    """Write the browser userscript next to the exe when the manifest includes it."""
    item = info.userscript
    if item is None:
        return None
    dest = app_dir() / item.filename
    path = download_sidecar(item, dest)
    logger.info("userscript updated: %s", path)
    return path


def download_update(
    info: UpdateInfo,
    dest: Path,
    cfg: BridgeConfig,
    *,
    timeout: float = 30.0,
    on_progress: Callable[[int, int], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Download the exe beside the running build (GitHub CDN first, then Koara server)."""
    urls = ordered_download_urls(info, cfg)
    last_exc: Exception | None = None
    for i, url in enumerate(urls):
        if on_status is not None:
            try:
                if "github.com" in url.lower():
                    label = "GitHub CDN"
                elif "108.165.174.158" in url or (cfg.host and cfg.host in url):
                    label = "Koara Server"
                else:
                    label = url.split("/")[2] if "/" in url else url
                on_status(f"Downloading from {label}…")
            except Exception:
                pass
        try:
            attempt = UpdateInfo(
                version=info.version,
                url=url,
                sha256=info.sha256,
                size=info.size,
                url_github=info.url_github,
            )
            return _download_update_once(
                attempt, dest, timeout=timeout, on_progress=on_progress
            )
        except Exception as exc:
            last_exc = exc
            logger.info("update download failed (%s): %s", url, exc)
            if i + 1 < len(urls):
                logger.info("retrying update from next URL")
    assert last_exc is not None
    raise last_exc


def koara_exe_url(cfg: BridgeConfig | None = None, *, host: str = "", port: int = 0) -> str:
    """Direct download URL on the bot update host (always published with the exe)."""
    if cfg is not None:
        host = (cfg.host or "").strip() or "108.165.174.158"
        port = int(getattr(cfg, "update_port", 0) or DEFAULT_UPDATE_PORT)
    else:
        host = (host or "").strip() or "108.165.174.158"
        port = int(port or DEFAULT_UPDATE_PORT)
    return f"http://{host}:{port}/{EXE_NAME}"


def ordered_download_urls(info: UpdateInfo, cfg: BridgeConfig) -> list[str]:
    """GitHub CDN first for fast download speeds; fallback to Koara server."""
    seen: set[str] = set()
    out: list[str] = []

    def add(url: str) -> None:
        u = (url or "").strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)

    # 1. Primary: GitHub URL (if specified in manifest or GitHub mirror)
    github_url = getattr(info, "url_github", "") or (
        info.url if "github.com" in (info.url or "").lower() else ""
    )
    if not github_url and info.version:
        github_url = (
            f"https://github.com/SlaviqueBots/slavique-harem-bot/releases/"
            f"download/v{info.version}/{EXE_NAME}"
        )

    if github_url:
        add(github_url)

    # 2. Fallback: Koara server
    add(koara_exe_url(cfg))
    add(info.url)
    add(_koara_fallback_url(info.url))
    return out


def _koara_fallback_url(primary: str) -> str:
    u = (primary or "").strip()
    if "github.com" not in u.lower():
        return ""
    return f"http://108.165.174.158:{DEFAULT_UPDATE_PORT}/{EXE_NAME}"


def _download_update_once(
    info: UpdateInfo,
    dest: Path,
    *,
    timeout: float = 30.0,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists():
        tmp.unlink()
    req = urllib.request.Request(info.url, headers={"User-Agent": f"HaremLinkBridge/{__version__}"})
    h = hashlib.sha256()
    done = 0
    total = int(info.size or 0)
    if on_progress is not None:
        try:
            on_progress(0, total)
        except Exception:
            pass
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {info.url}") from exc
    with resp, tmp.open("wb") as out:
        if not total:
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
        if on_progress is not None and total:
            try:
                on_progress(0, total)
            except Exception:
                pass
        expected_min = int(info.size or 0)
        if expected_min > 5_000_000 and 0 < total < 1_000_000:
            raise RuntimeError(
                f"unexpected download size {total} bytes (expected ~{expected_min})"
            )
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if on_progress is not None:
                try:
                    on_progress(done, total)
                except Exception:
                    pass
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
    """PowerShell script that swaps the exe and relaunches the app.

    Hard rule: only ``HaremLinkBridge.exe`` (+ ``.new`` / ``.bak``) and helper
    scripts are touched. ``conjure_finder.env``, config JSON, and the userscript
    beside the exe are never deleted.
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
            "    Start-Sleep -Milliseconds 300",
            "  }",
            "  Start-Sleep -Milliseconds 600",
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
            "      Start-Sleep -Milliseconds 300",
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
            "      Start-Sleep -Milliseconds 300",
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
            "  $workdir = Split-Path -Parent $target",
            "  Start-Sleep -Milliseconds 300",
            "  # Force the new one-file exe to create its own _MEI temp directory.",
            "  # The helper inherited PyInstaller state from the old process.",
            "  $env:PYINSTALLER_RESET_ENVIRONMENT = '1'",
            "  try {",
            "    Start-Process -FilePath $target -WorkingDirectory $workdir",
            "  } catch {",
            "    Start-Process -FilePath 'cmd.exe' -ArgumentList ('/c start \"\" \"' + $target + '\"') -WorkingDirectory $workdir -WindowStyle Hidden",
            "  }",
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
    """Replace the running frozen exe via PowerShell and relaunch."""
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
    # Release singleton mutex before terminating
    try:
        from link_bridge.singleton import release_singleton

        release_singleton()
    except Exception:
        pass

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


def run_update(
    cfg: BridgeConfig,
    info: UpdateInfo,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    dest = app_dir() / f"{EXE_NAME}.new"
    if dest.name != f"{EXE_NAME}.new":
        raise RuntimeError(f"refusing update dest {dest!s}")
    if on_status is not None:
        try:
            on_status("Fetching browser userscript…")
        except Exception:
            pass
    try:
        download_userscript(info)
    except Exception as exc:
        logger.warning("userscript download failed (continuing with exe update): %s", exc)
    path = download_update(
        info, dest, cfg, on_progress=on_progress, on_status=on_status
    )
    apply_update_and_restart(path)
    return path
