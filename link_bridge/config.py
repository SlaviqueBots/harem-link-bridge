"""Load / save companion settings (update-safe).

Frozen builds store data under ``%LOCALAPPDATA%\\HaremLinkBridge`` so replacing
the .exe never wipes pairing, window layout, or other prefs.  On every launch we
merge pairing from any legacy ``harem_link_bridge.json`` beside older exe paths
if the AppData copy would otherwise look unpaired or still has factory defaults for
UI scale, toggles, and window layout.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


CONFIG_NAME = "harem_link_bridge.json"
_APPDATA_DIRNAME = "HaremLinkBridge"
_MIGRATE_FILES = (
    CONFIG_NAME,
    "crafting_plans.json",
    "market_hidden.json",
    "primed_hidden.json",
    "harem_bridge_send.user.js",
    "conjure_finder.env",
    "conjure_finder_findings.json",
)
_PAIRING_KEYS = ("device_id", "device_token", "token", "user_id")
_CONFIG_DEFAULTS: dict | None = None

# Optional override used by ``python -m link_bridge --config PATH`` (local DEV runs).
_CONFIG_PATH_OVERRIDE: Path | None = None
_MIGRATED_FOR_EXE: str | None = None


def set_config_path(path: Path | None) -> None:
    global _CONFIG_PATH_OVERRIDE
    _CONFIG_PATH_OVERRIDE = Path(path).resolve() if path is not None else None


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _legacy_data_dirs() -> list[Path]:
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        dirs.append(path)

    _add(_exe_dir())
    _add(_exe_dir().parent)
    _add(_exe_dir().parent / "Harem Link Bridge")
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        for folder in sorted(desktop.glob("HaremLinkBridge*")):
            if folder.is_dir():
                _add(folder)
    return dirs


def _config_defaults() -> dict:
    global _CONFIG_DEFAULTS
    if _CONFIG_DEFAULTS is None:
        _CONFIG_DEFAULTS = asdict(BridgeConfig())
    return _CONFIG_DEFAULTS


def _value_at_default(key: str, val: object) -> bool:
    defaults = _config_defaults()
    if key not in defaults:
        return val in ("", None, [], {})
    default = defaults[key]
    if isinstance(default, bool):
        return bool(val) == default
    if isinstance(default, int):
        try:
            return int(val) == default
        except (TypeError, ValueError):
            return True
    if isinstance(default, float):
        try:
            return abs(float(val) - default) < 0.005
        except (TypeError, ValueError):
            return True
    return val == default


def _prefs_richness(raw: dict) -> int:
    score = 0
    for key, val in raw.items():
        if key in _PAIRING_KEYS:
            continue
        if not _value_at_default(key, val):
            score += 1
    return score


def _appdata_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    path = Path(root) / _APPDATA_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_config_dict(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _pairing_score(raw: dict) -> int:
    score = 0
    if str(raw.get("device_token") or "").strip():
        score += 100
    token = str(raw.get("token") or "").strip()
    try:
        uid = int(raw.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if token and uid > 0:
        score += 50
    return score


def _can_connect_dict(raw: dict) -> bool:
    if str(raw.get("device_token") or "").strip() and str(raw.get("device_id") or "").strip():
        return True
    token = str(raw.get("token") or "").strip()
    try:
        uid = int(raw.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    return bool(token and uid > 0)


def _merge_pairing_fields(target: dict, source: dict) -> bool:
    """Copy pairing keys from source when target cannot connect. Returns changed."""
    if _can_connect_dict(target) or not _can_connect_dict(source):
        return False
    changed = False
    for key in _PAIRING_KEYS:
        if key not in source:
            continue
        val = source.get(key)
        if key == "user_id":
            try:
                val = int(val or 0)
            except (TypeError, ValueError):
                val = 0
        elif key != "user_id":
            val = str(val or "").strip()
        if target.get(key) != val and val not in ("", 0, None):
            target[key] = val
            changed = True
    return changed


def _merge_best_pairing(target: dict, sources: list[dict]) -> bool:
    """Take pairing fields only from complete pairs in a single source file."""
    pairs: list[tuple[str, str]] = []
    best_legacy: tuple[str, int, str] | None = None
    for raw in sources:
        did = str(raw.get("device_id") or "").strip()
        dt = str(raw.get("device_token") or "").strip()
        if did and dt:
            pairs.append((did, dt))
        tok = str(raw.get("token") or "").strip()
        try:
            uid = int(raw.get("user_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        if tok and uid > 0:
            best_legacy = (tok, uid, did)

    changed = False
    cur_id = str(target.get("device_id") or "").strip()
    cur_tok = str(target.get("device_token") or "").strip()
    if cur_id and cur_tok and not any(did == cur_id and dt == cur_tok for did, dt in pairs):
        target["device_token"] = ""
        changed = True
        cur_tok = ""

    if pairs:
        did, dt = pairs[0]
        for key, val in (("device_id", did), ("device_token", dt)):
            if target.get(key) != val:
                target[key] = val
                changed = True
    elif best_legacy is not None:
        tok, uid, did = best_legacy
        if str(target.get("device_token") or "").strip():
            target["device_token"] = ""
            changed = True
        for key, val in (("token", tok), ("user_id", uid)):
            if target.get(key) != val:
                target[key] = val
                changed = True
        if did and not str(target.get("device_id") or "").strip():
            target["device_id"] = did
            changed = True

    return changed


def _merge_preference_fields(target: dict, source: dict) -> bool:
    """Fill factory-default slots in target from a richer legacy config."""
    changed = False
    for key, src_val in source.items():
        if key in _PAIRING_KEYS:
            continue
        if _value_at_default(key, src_val):
            continue
        if _value_at_default(key, target.get(key)):
            if target.get(key) != src_val:
                target[key] = src_val
                changed = True
    return changed


def _migrate_legacy_files(target_dir: Path) -> None:
    for name in _MIGRATE_FILES:
        if name == CONFIG_NAME:
            continue
        dest = target_dir / name
        if dest.is_file():
            continue
        for src_dir in _legacy_data_dirs():
            src = src_dir / name
            if not src.is_file():
                continue
            try:
                shutil.copy2(src, dest)
                break
            except OSError:
                continue


def _ensure_config_migrated() -> None:
    """Merge the best legacy config into AppData (pairing + prefs)."""
    global _MIGRATED_FOR_EXE
    if _CONFIG_PATH_OVERRIDE is not None or not getattr(sys, "frozen", False):
        return
    exe_key = str(_exe_dir()).lower()
    if _MIGRATED_FOR_EXE == exe_key:
        return
    _MIGRATED_FOR_EXE = exe_key

    data_dir = _appdata_dir()
    _migrate_legacy_files(data_dir)
    dest = data_dir / CONFIG_NAME

    candidates: list[tuple[int, int, float, dict, Path]] = []
    for src_dir in _legacy_data_dirs():
        path = src_dir / CONFIG_NAME
        raw = _read_config_dict(path)
        if raw is None:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        candidates.append(
            (_pairing_score(raw), _prefs_richness(raw), mtime, raw, path)
        )

    current = _read_config_dict(dest)
    if current is not None:
        try:
            mtime = dest.stat().st_mtime
        except OSError:
            mtime = 0.0
        candidates.append(
            (
                _pairing_score(current),
                _prefs_richness(current),
                mtime,
                current,
                dest,
            )
        )

    if not candidates:
        return

    exe_json = _exe_dir() / CONFIG_NAME
    exe_raw = _read_config_dict(exe_json)

    def _rank(item: tuple[int, int, float, dict, Path]) -> tuple[int, int, int, float]:
        _pscore, richness, mtime, _raw, path = item
        beside_exe = 1 if path.resolve() == exe_json.resolve() else 0
        return (beside_exe, _pscore, richness, mtime)

    best = max(candidates, key=_rank)

    if current is None:
        # Drop-in over a 1.4.1 folder: the json next to this exe wins.
        if exe_raw is not None and (
            _can_connect_dict(exe_raw) or _prefs_richness(exe_raw) > 0
        ):
            best_raw, best_path = exe_raw, exe_json
        else:
            _ps, _rich, _mtime, best_raw, best_path = best
        if best_path != dest:
            try:
                shutil.copy2(best_path, dest)
            except OSError:
                dest.write_text(
                    json.dumps(best_raw, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        return

    merged = dict(current)
    changed = False
    all_raws = [item[3] for item in candidates]
    if _merge_best_pairing(merged, all_raws):
        changed = True

    legacy_candidates = [item for item in candidates if item[4] != dest]
    for _pscore, richness, mtime, raw, _path in sorted(
        legacy_candidates, key=lambda item: (item[1], item[2]), reverse=True
    ):
        if _merge_preference_fields(merged, raw):
            changed = True

    if not changed:
        return
    try:
        dest.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def exe_dir() -> Path:
    """Folder that contains the frozen exe (or the package, in source runs)."""
    return _exe_dir()


def app_dir() -> Path:
    """Directory for config + local Bridge data files."""
    if _CONFIG_PATH_OVERRIDE is not None:
        return _CONFIG_PATH_OVERRIDE.parent
    if getattr(sys, "frozen", False):
        _ensure_config_migrated()
        return _appdata_dir()
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
    # Auto-update on launch (HTTP beside the WS host, default port 8766).
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
    window_state: str = "zoomed"
    # Soft beep when an in-client omni craft lands a new image.
    omni_beep: bool = False
    # Omni panel: load original/sample instead of the small preview (larger pane).
    omni_full_image: bool = False
    # Repeat last OmniCraft (green) action. Tk keysym; default space.
    omni_repeat_key: str = "space"
    # Last Omnicraft host geometry, e.g. "720x520+80+60". Empty = center default.
    omni_window_geometry: str = ""
    # Market lot inspector window geometry.
    market_lot_window_geometry: str = ""
    # Market lot inspector window state: "normal" or "zoomed".
    market_lot_window_state: str = "normal"
    # Market tab: justified gallery (like roster grid view).
    market_grid_view: bool = True
    # Market price filter (persisted; empty = no bound).
    market_min_price: str = ""
    market_max_price: str = ""
    # Tk state: "normal" or "zoomed" (Windows maximized).
    omni_window_state: str = "zoomed"
    # App chrome: "dark" (default) or "light" (classic bright look).
    ui_theme: str = "dark"
    # Extra UI scale on top of Windows DPI (0.90–1.50). 1.0 = follow display DPI.
    ui_scale: float = 1.0
    # Left-click opens in-client Omnicraft instead of the image viewer.
    left_click_omni: bool = False
    # Flavoured / Unflavoured tabs: left-click opens the flavour editor (wins over omni).
    left_click_flavour: bool = True
    # Roster: hide cards that already belong to any set.
    hide_in_any_set: bool = False
    # Keep viewed pictures on this device permanently (instant reopen).
    offline_image_cache: bool = False
    # Mouse-wheel scroll strength (0.25–6.0). Default 3.0 with smooth easing.
    scroll_speed: float = 3.0
    # Local HTTP hook for browser userscript (127.0.0.1 only).
    browser_hook_enabled: bool = True
    browser_hook_port: int = 8767
    # Soft looping chime 1 minute before today's voted tournament (off by default).
    tournament_alarm: bool = False

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


def _to_int(raw: object, default: int) -> int:
    try:
        return int(raw or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


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
        port=_to_int(raw.get("port"), BridgeConfig.port),
        token=str(raw.get("token") or ""),
        user_id=_to_int(raw.get("user_id"), 0),
        device_id=str(raw.get("device_id") or ""),
        device_token=str(raw.get("device_token") or ""),
        paused=bool(raw.get("paused", False)),
        pause_on_lock=bool(raw.get("pause_on_lock", True)),
        start_hidden=bool(raw.get("start_hidden", False)),
        open_browser=bool(raw.get("open_browser", True)),
        autostart=bool(raw.get("autostart", False)),
        check_updates=bool(raw.get("check_updates", True)),
        update_port=_to_int(raw.get("update_port"), BridgeConfig.update_port),
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
        window_state=_normalize_window_state(raw.get("window_state", "zoomed")),
        omni_beep=bool(raw.get("omni_beep", False)),
        omni_full_image=bool(raw.get("omni_full_image", False)),
        omni_repeat_key=_normalize_omni_repeat_key(raw.get("omni_repeat_key", "space")),
        omni_window_geometry=str(raw.get("omni_window_geometry") or ""),
        market_lot_window_geometry=str(raw.get("market_lot_window_geometry") or ""),
        market_lot_window_state=_normalize_window_state(
            raw.get("market_lot_window_state", "normal")
        ),
        market_grid_view=bool(raw.get("market_grid_view", True)),
        market_min_price=str(raw.get("market_min_price") or ""),
        market_max_price=str(raw.get("market_max_price") or ""),
        omni_window_state=_normalize_window_state(
            raw.get("omni_window_state", "zoomed")
        ),
        ui_theme=_normalize_ui_theme(raw.get("ui_theme", "dark")),
        ui_scale=_clamp_ui_scale(raw.get("ui_scale", 1.0)),
        left_click_omni=bool(raw.get("left_click_omni", False)),
        left_click_flavour=bool(raw.get("left_click_flavour", True)),
        hide_in_any_set=bool(raw.get("hide_in_any_set", False)),
        offline_image_cache=bool(raw.get("offline_image_cache", False)),
        scroll_speed=_clamp_scroll_speed(raw.get("scroll_speed", 3.0)),
        browser_hook_enabled=bool(raw.get("browser_hook_enabled", True)),
        browser_hook_port=_to_int(raw.get("browser_hook_port"), 8767),
        tournament_alarm=bool(raw.get("tournament_alarm", False)),
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


def _normalize_window_state(raw: object) -> str:
    return "zoomed" if str(raw or "").strip().lower() == "zoomed" else "normal"


def _clamp_ui_scale(raw: object) -> float:
    from link_bridge.dpi import clamp_ui_scale

    return clamp_ui_scale(raw)


def _normalize_omni_repeat_key(raw: object) -> str:
    text = str(raw or "space").strip().lower()
    if text in ("", "space", "spacebar", " "):
        return "space"
    if text.startswith("key-"):
        text = text[4:]
    return text or "space"


def save_config(cfg: BridgeConfig) -> Path:
    path = config_path()
    cfg.ensure_device_id()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(cfg), indent=2, ensure_ascii=False) + "\n"
    # Atomic write: a mid-write crash must never leave half a JSON behind
    # (next launch would silently reset to defaults).
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    return path


def new_pair_device_id() -> str:
    return str(uuid.uuid4())
