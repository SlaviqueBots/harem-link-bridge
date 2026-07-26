"""Windows Run-key autostart for the frozen companion."""

from __future__ import annotations

import sys
from pathlib import Path

APP_RUN_NAME = "HaremLinkBridge"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    """Command line written into the Run key."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    # Dev fallback: start the package from this repo.
    root = Path(__file__).resolve().parents[1]
    py = sys.executable
    return f'"{py}" -m link_bridge'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_RUN_NAME)
        return bool(str(value or "").strip())
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Autostart is only supported on Windows.")
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, APP_RUN_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_RUN_NAME)
            except FileNotFoundError:
                pass
