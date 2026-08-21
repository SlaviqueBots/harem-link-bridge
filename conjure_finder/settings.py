"""Load / save Conjure Finder settings (API keys) for portable Windows use."""

from __future__ import annotations

import os
import re
from pathlib import Path

from conjure_finder.bootstrap import ROOT

SETTINGS_PATH = ROOT / "conjure_finder.env"

# Keys the finder needs. BOT_TOKEN is a dummy placeholder for Config.load().
SETTING_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("DANBOORU_USER", "Danbooru username", ""),
    ("DANBOORU_API_KEY", "Danbooru API key", ""),
    ("RULE34_API_KEY", "Rule34 API key", ""),
    ("RULE34_USER_ID", "Rule34 user id", ""),
)

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def read_settings() -> dict[str, str]:
    """Current effective values (env after bootstrap load order)."""
    out: dict[str, str] = {}
    for key, _label, _hint in SETTING_FIELDS:
        out[key] = os.environ.get(key, "") or ""
    return out


def settings_status() -> dict[str, bool]:
    vals = read_settings()
    return {
        "danbooru": bool(vals.get("DANBOORU_USER") and vals.get("DANBOORU_API_KEY")),
        "rule34": bool(vals.get("RULE34_API_KEY") and vals.get("RULE34_USER_ID")),
        "file": SETTINGS_PATH.exists(),
    }


def save_settings(values: dict[str, str]) -> Path:
    """Write ``conjure_finder.env`` and apply into the running process + CFG."""
    lines = [
        "# Conjure Finder local settings — not for sharing / not committed to git.",
        "# Overrides project .env for this PC tool only.",
        "",
    ]
    cleaned: dict[str, str] = {}
    known = {key for key, _label, _hint in SETTING_FIELDS}
    for key, _label, _hint in SETTING_FIELDS:
        raw = (values.get(key) or "").strip()
        cleaned[key] = raw
        # Quote if needed
        if re.search(r'[\s#"\\]', raw):
            esc = raw.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}="{esc}"')
        else:
            lines.append(f"{key}={raw}")

    # Keep optional update overrides (and any other custom keys) across Settings saves.
    extras = _read_extra_env_lines(known)
    if extras:
        lines.append("")
        lines.append("# Auto-update (optional; defaults work without these)")
        lines.extend(extras)
    lines.append("")
    SETTINGS_PATH.write_text("\n".join(lines), encoding="utf-8")

    for key, val in cleaned.items():
        if val:
            os.environ[key] = val
        elif key in os.environ:
            # Keep previously loaded shared .env if user cleared the field? 
            # Prefer explicit clear of override — leave os.environ as written.
            os.environ[key] = val

    _reload_cfg()
    return SETTINGS_PATH


def _read_extra_env_lines(known_keys: set[str]) -> list[str]:
    """Preserve non-API-key assignments from an existing conjure_finder.env."""
    if not SETTINGS_PATH.exists():
        return []
    out: list[str] = []
    for line in SETTINGS_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in known_keys:
            continue
        if not _KEY_RE.match(key):
            continue
        out.append(stripped)
    return out


def _reload_cfg() -> None:
    """Rebuild bot Config singleton so clients pick up new keys."""
    try:
        import bot.core.config as cfg_mod

        cfg_mod.CFG = cfg_mod.Config.load()
    except Exception:
        pass


def auth_snapshot() -> dict[str, bool]:
    """Whether live CFG currently has usable site credentials."""
    try:
        import bot.core.config as cfg_mod

        cfg = cfg_mod.CFG
        return {
            "danbooru": bool(
                (cfg.danbooru_user or "").strip() and (cfg.danbooru_api_key or "").strip()
            ),
            "rule34": bool(cfg.rule34_api_key and cfg.rule34_user_id),
        }
    except Exception:
        return {"danbooru": False, "rule34": False}


def apply_settings_file() -> None:
    """Load conjure_finder.env with override if present."""
    from dotenv import load_dotenv

    if SETTINGS_PATH.exists():
        load_dotenv(SETTINGS_PATH, override=True)
        _reload_cfg()
