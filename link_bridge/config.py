"""Load / save companion settings beside the exe (update-safe)."""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


CONFIG_NAME = "harem_link_bridge.json"

# Optional override used by ``python -m link_bridge --config PATH`` (local DEV runs).
_CONFIG_PATH_OVERRIDE: Path | None = None


def set_config_path(path: Path | None) -> None:
    global _CONFIG_PATH_OVERRIDE
    _CONFIG_PATH_OVERRIDE = Path(path).resolve() if path is not None else None


def app_dir() -> Path:
    """Directory that holds the config file (next to the .exe when frozen)."""
    if _CONFIG_PATH_OVERRIDE is not None:
        return _CONFIG_PATH_OVERRIDE.parent
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path() -> Path:
    if _CONFIG_PATH_OVERRIDE is not None:
        return _CONFIG_PATH_OVERRIDE
    return app_dir() / CONFIG_NAME


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
    # True = tight justified gallery (default). False = square crop grid.
    natural_thumbs: bool = True
    # Gallery row height multiplier (0.5–2.0). 1.5 ≈ older larger look on big screens.
    preview_scale: float = 1.5
    # Middle-click post destination: "group" (main harem) or "dm".
    middle_click_target: str = "group"
    # Left-click: prefer original file_url from the booru (PC downloads directly).
    prefer_original_open: bool = True
    # Flavour/note editor geometry, e.g. "420x220+100+80". Empty = center on main.
    text_edit_geometry: str = ""
    # Last window geometry, e.g. "900x760+120+80". Empty = use DEFAULT_GEOMETRY.
    window_geometry: str = ""
    # Tk state: "normal" or "zoomed" (Windows maximized).
    window_state: str = "normal"
    # Soft beep when an in-client omni craft lands a new image.
    omni_beep: bool = False
    # Omni panel: load original/sample instead of the small preview (larger pane).
    omni_full_image: bool = False
    # Last Omnicraft host geometry, e.g. "720x520+80+60". Empty = center default.
    omni_window_geometry: str = ""
    # App chrome: "dark" (default) or "light" (classic bright look).
    ui_theme: str = "dark"
    # Extra UI scale on top of Windows DPI (0.75–2.0). 1.0 = follow display DPI.
    ui_scale: float = 1.0
    # Left-click opens in-client Omnicraft instead of the image viewer.
    left_click_omni: bool = False
    # Roster: hide cards that already belong to any set.
    hide_in_any_set: bool = False
    # Mouse-wheel scroll strength (0.25–6.0). Default 3.0 with smooth easing.
    scroll_speed: float = 3.0
    # Local HTTP hook for browser userscript (127.0.0.1 only).
    browser_hook_enabled: bool = True
    browser_hook_port: int = 8767

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
        natural_thumbs=bool(raw.get("natural_thumbs", True)),
        preview_scale=_clamp_preview_scale(raw.get("preview_scale", 1.5)),
        middle_click_target=_normalize_post_target(
            raw.get("middle_click_target", "group")
        ),
        prefer_original_open=bool(raw.get("prefer_original_open", True)),
        text_edit_geometry=str(raw.get("text_edit_geometry") or ""),
        window_geometry=str(raw.get("window_geometry") or ""),
        window_state=str(raw.get("window_state") or "normal"),
        omni_beep=bool(raw.get("omni_beep", False)),
        omni_full_image=bool(raw.get("omni_full_image", False)),
        omni_window_geometry=str(raw.get("omni_window_geometry") or ""),
        ui_theme=_normalize_ui_theme(raw.get("ui_theme", "dark")),
        ui_scale=_clamp_ui_scale(raw.get("ui_scale", 1.0)),
        left_click_omni=bool(raw.get("left_click_omni", False)),
        hide_in_any_set=bool(raw.get("hide_in_any_set", False)),
        scroll_speed=_clamp_scroll_speed(raw.get("scroll_speed", 3.0)),
        browser_hook_enabled=bool(raw.get("browser_hook_enabled", True)),
        browser_hook_port=int(raw.get("browser_hook_port") or 8767),
    )
    cfg.ensure_device_id()
    return cfg


def _clamp_preview_scale(raw: object) -> float:
    try:
        v = float(raw)
    except Exception:
        v = 1.5
    return max(0.5, min(2.0, round(v, 2)))


def _clamp_scroll_speed(raw: object) -> float:
    try:
        v = float(raw)
    except Exception:
        v = 3.0
    return max(0.25, min(6.0, round(v, 2)))


def _normalize_post_target(raw: object) -> str:
    text = str(raw or "group").strip().lower()
    return "dm" if text == "dm" else "group"


def _normalize_ui_theme(raw: object) -> str:
    text = str(raw or "dark").strip().lower()
    return "light" if text == "light" else "dark"


def _clamp_ui_scale(raw: object) -> float:
    try:
        v = float(raw)
    except Exception:
        v = 1.0
    return max(0.75, min(2.0, round(v, 2)))


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
