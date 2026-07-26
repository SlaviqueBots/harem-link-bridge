"""Write harem_link_bridge.json from .env / koara_secrets (no questions)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow `python -m link_bridge.sync_config` from repo root.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_env_map() -> dict[str, str]:
    out: dict[str, str] = {}
    candidates = [
        _ROOT / ".env",
        _ROOT / "scripts" / "koara_secrets.local.env",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out.setdefault(k.strip(), v.strip())
    # Process env wins for anything already exported.
    for k in (
        "PC_BRIDGE_TOKEN",
        "PC_BRIDGE_PORT",
        "ADMIN_USER_ID",
        "KOARA_HOST",
    ):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def sync_config(*, start_hidden: bool = False) -> Path:
    from link_bridge.config import BridgeConfig, save_config, load_config

    env = _load_env_map()
    existing = load_config()
    # Companion always dials the public Koara host (not PC_BRIDGE_HOST=0.0.0.0).
    host = (
        env.get("KOARA_HOST")
        or existing.host
        or "108.165.174.158"
    ).strip()
    if host in ("0.0.0.0", "127.0.0.1", "::"):
        host = "108.165.174.158"
    port = int(env.get("PC_BRIDGE_PORT") or existing.port or 8765)
    cfg = BridgeConfig(
        host=host,
        port=port,
        # Keep pairing secrets; only fill legacy token if not yet paired.
        token="" if existing.is_paired() else (existing.token or ""),
        user_id=0 if existing.is_paired() else int(existing.user_id or 0),
        device_id=existing.device_id,
        device_token=existing.device_token,
        paused=existing.paused,
        start_hidden=start_hidden if start_hidden else existing.start_hidden,
        open_browser=existing.open_browser,
        focus_browser=existing.focus_browser,
        autostart=existing.autostart,
        check_updates=existing.check_updates,
        update_port=existing.update_port,
        update_url=existing.update_url,
    )
    # Owner bootstrap only: seed legacy token when nothing is paired yet.
    if not cfg.is_paired() and not cfg.can_legacy_connect():
        token = (env.get("PC_BRIDGE_TOKEN") or "").strip()
        uid = int(env.get("ADMIN_USER_ID") or 0)
        if token and uid:
            cfg.token = token
            cfg.user_id = uid
    path = save_config(cfg)
    _ensure_desktop_shortcut_windows()
    return path


def _ensure_desktop_shortcut_windows() -> None:
    """Create Desktop\\Harem Link Bridge.lnk when running on Windows."""
    if sys.platform != "win32":
        return
    try:
        folder = _ROOT / "Harem Link Bridge"
        target = folder / "Harem Link Bridge.vbs"
        if not target.is_file():
            return
        desktop = Path.home() / "Desktop"
        if not desktop.is_dir():
            desktop = Path.home() / "OneDrive" / "Desktop"
        if not desktop.is_dir():
            return
        lnk = desktop / "Harem Link Bridge.lnk"
        # PowerShell COM shortcut — no extra deps.
        import subprocess

        cmd = (
            "$ws = New-Object -ComObject WScript.Shell; "
            f"$sc = $ws.CreateShortcut(r'{lnk}'); "
            f"$sc.TargetPath = r'{target}'; "
            f"$sc.WorkingDirectory = r'{folder}'; "
            "$sc.WindowStyle = 1; "
            "$sc.Description = 'Open harem bot links in your browser'; "
            "$sc.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


def main() -> int:
    path = sync_config()
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
