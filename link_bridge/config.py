"""Load / save companion settings beside the exe (update-safe)."""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


CONFIG_NAME = "harem_link_bridge.json"


def app_dir() -> Path:
    """Directory that holds the config file (next to the .exe when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


@dataclass
class BridgeConfig:
    host: str = "108.165.174.158"
    port: int = 8765
    # Legacy master-token auth (owner install until re-paired).
    token: str = ""
    user_id: int = 0
    # Device pairing (adopters): no shared secrets.
    device_id: str = ""
    device_token: str = ""
    paused: bool = False
    # When True, pause while Windows is locked (Win+L) and resume on unlock.
    pause_on_lock: bool = True
    start_hidden: bool = False
    open_browser: bool = True
    autostart: bool = False
    # Auto-update (HTTP beside the WS host, default port 8766).
    check_updates: bool = True
    update_port: int = 8766
    update_url: str = ""  # optional full URL to version.json
    # After opening omnicraft in DM, try to raise Telegram.exe (no deep links).
    focus_telegram: bool = True
    # Last window geometry, e.g. "900x760+120+80". Empty = use DEFAULT_GEOMETRY.
    window_geometry: str = ""

    def ws_url(self) -> str:
        host = (self.host or "").strip() or "127.0.0.1"
        return f"ws://{host}:{int(self.port)}"

    def ensure_device_id(self) -> str:
        if not (self.device_id or "").strip():
            self.device_id = str(uuid.uuid4())
        return self.device_id

    def is_paired(self) -> bool:
        return bool((self.device_id or "").strip() and (self.device_token or "").strip())

    def can_legacy_connect(self) -> bool:
        return bool((self.token or "").strip() and int(self.user_id or 0) > 0)

    def can_connect(self) -> bool:
        return self.is_paired() or self.can_legacy_connect()


def config_path() -> Path:
    return app_dir() / CONFIG_NAME


def load_config() -> BridgeConfig:
    path = config_path()
    if not path.is_file():
        cfg = BridgeConfig()
        cfg.ensure_device_id()
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cfg = BridgeConfig()
        cfg.ensure_device_id()
        return cfg
    if not isinstance(raw, dict):
        cfg = BridgeConfig()
        cfg.ensure_device_id()
        return cfg
    cfg = BridgeConfig(
        host=str(raw.get("host") or BridgeConfig.host),
        port=int(raw.get("port") or BridgeConfig.port),
        token=str(raw.get("token") or ""),
        user_id=int(raw.get("user_id") or 0),
        device_id=str(raw.get("device_id") or ""),
        device_token=str(raw.get("device_token") or ""),
        paused=bool(raw.get("paused", False)),
        pause_on_lock=bool(raw.get("pause_on_lock", True)),
        start_hidden=bool(raw.get("start_hidden", False)),
        open_browser=bool(raw.get("open_browser", True)),
        autostart=bool(raw.get("autostart", False)),
        check_updates=bool(raw.get("check_updates", True)),
        update_port=int(raw.get("update_port") or BridgeConfig.update_port),
        update_url=str(raw.get("update_url") or ""),
        focus_telegram=bool(raw.get("focus_telegram", True)),
        window_geometry=str(raw.get("window_geometry") or ""),
    )
    cfg.ensure_device_id()
    return cfg


def save_config(cfg: BridgeConfig) -> Path:
    path = config_path()
    cfg.ensure_device_id()
    path.write_text(
        json.dumps(asdict(cfg), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def new_pair_device_id() -> str:
    return str(uuid.uuid4())
