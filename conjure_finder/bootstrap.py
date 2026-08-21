"""Load env before any bot imports (CFG reads env at import time)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = _app_root()


def set_app_root(path: Path | str) -> None:
    """Point env / saves / cache at a folder (Bridge embed uses config dir)."""
    global ROOT
    ROOT = Path(path).resolve()
    try:
        import conjure_finder.settings as settings_mod

        settings_mod.SETTINGS_PATH = ROOT / "conjure_finder.env"
    except Exception:
        pass
    try:
        import conjure_finder.saves as saves_mod

        saves_mod.SAVES_DIR = ROOT / "conjure_finder_saves"
    except Exception:
        pass
    try:
        import conjure_finder.previews as previews_mod

        previews_mod.CACHE_DIR = ROOT / "conjure_finder_preview_cache"
    except Exception:
        pass


def ensure_path() -> None:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and meipass not in sys.path:
            sys.path.insert(0, str(meipass))
        return
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _sibling_conjure_env() -> Path | None:
    """Optional DEV fallback: ConjureFinder/conjure_finder.env next to this repo."""
    for candidate in (
        ROOT / "conjure_finder.env",
        ROOT.parent / "ConjureFinder" / "conjure_finder.env",
        ROOT.parent.parent / "ConjureFinder" / "conjure_finder.env",
    ):
        if candidate.is_file() and candidate.parent != ROOT:
            return candidate
    return None


def load_env() -> None:
    """Load optional key files. Later files win.

    Order:
      1. project ``.env`` (optional)
      2. sibling ConjureFinder ``conjure_finder.env`` (DEV convenience only)
      3. ``conjure_finder.env`` in ROOT — preferred; also written by Settings…
    """
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    local = ROOT / "conjure_finder.env"
    if not local.exists():
        sibling = _sibling_conjure_env()
        if sibling is not None:
            load_dotenv(sibling, override=False)
    if local.exists():
        load_dotenv(local, override=True)
    # BOT_TOKEN is required by Config.load(); finder never talks to Telegram.
    os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN") or "conjure-finder-unused")


def apply_env() -> None:
    """Ensure import path + env files (handy for one-off scripts)."""
    ensure_path()
    load_env()
