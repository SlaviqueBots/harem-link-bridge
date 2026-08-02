"""Entry: ``python -m link_bridge`` (GUI) or ``python -m link_bridge --cli``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def _ensure_path() -> None:
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_dotenv_quiet() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    local = root / "scripts" / "koara_secrets.local.env"
    if local.exists():
        load_dotenv(local, override=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harem Link Bridge PC companion")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Headless mode (no GUI) — useful for smoke tests",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _ensure_path()
    _load_dotenv_quiet()
    # Never rewrite adopter config on frozen launches (owner bootstrap only).
    if not getattr(sys, "frozen", False):
        try:
            from link_bridge.sync_config import sync_config

            sync_config()
        except Exception:
            logging.getLogger(__name__).debug("sync_config skipped", exc_info=True)

    if args.cli:
        return _run_cli()

    if sys.platform == "win32":
        from link_bridge.singleton import acquire_singleton

        if not acquire_singleton():
            logging.getLogger(__name__).error(
                "Harem Link Bridge is already running — exit the tray copy first."
            )
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    0,
                    "Harem Link Bridge is already running (check the tray).\n\n"
                    "Exit the existing copy before starting another.",
                    "Harem Link Bridge",
                    0x00000030,
                )
            except Exception:
                pass
            return 1

    from link_bridge.gui import main as gui_main

    gui_main()
    return 0


def _run_cli() -> int:
    from link_bridge.config import load_config
    from link_bridge.ws_client import BridgeClient

    cfg = load_config()
    if not cfg.token or int(cfg.user_id) <= 0:
        print(
            "Missing token/user_id — run from the bot folder after bootstrap, "
            "or fill harem_link_bridge.json.",
            file=sys.stderr,
        )
        return 2

    def on_status(msg: str) -> None:
        print(msg, flush=True)

    def on_open(url: str) -> None:
        print(f"OPEN {url}", flush=True)
        if cfg.open_browser:
            from link_bridge.browser_open import open_url

            open_url(url)

    client = BridgeClient(cfg, on_status=on_status, on_open_url=on_open)
    try:
        asyncio.run(client.run_forever())
    except KeyboardInterrupt:
        client.request_stop()
        print("Stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
