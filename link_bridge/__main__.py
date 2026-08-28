"""Entry: ``python -m link_bridge`` (GUI) or ``python -m link_bridge --cli``.

Local iteration:
  python -m link_bridge --dev --config path\\to\\harem_link_bridge.json
"""

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
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Local DEV run: skip sync_config + silent auto-update (singleton still enforced)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Use this harem_link_bridge.json (instead of next-to-exe / package)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _ensure_path()
    _load_dotenv_quiet()
    try:
        from link_bridge.ssl_certs import ensure_ssl_certs

        ensure_ssl_certs()
    except Exception:
        logging.getLogger(__name__).debug("ssl_certs setup skipped", exc_info=True)

    if args.config is not None:
        from link_bridge.config import set_config_path

        set_config_path(args.config)

    # Never rewrite adopter config on frozen launches (owner bootstrap only).
    # DEV runs also skip — keep the copied live settings intact.
    if not getattr(sys, "frozen", False) and not args.dev:
        try:
            from link_bridge.sync_config import sync_config

            sync_config()
        except Exception:
            logging.getLogger(__name__).debug("sync_config skipped", exc_info=True)

    if args.cli:
        return _run_cli()

    if sys.platform == "win32":
        from link_bridge.singleton import acquire_singleton

        if not acquire_singleton(dev=bool(args.dev)):
            logging.getLogger(__name__).error(
                "Harem Link Bridge is already running — exit the tray copy first."
            )
            try:
                import ctypes

                msg = (
                    "Harem Link Bridge DEV is already running.\n\n"
                    "Use scripts\\relaunch_bridge_dev.py (or the VBS) to restart it."
                    if args.dev
                    else "Harem Link Bridge is already running (check the tray).\n\n"
                    "Exit the existing copy before starting another."
                )
                ctypes.windll.user32.MessageBoxW(
                    0,
                    msg,
                    "Harem Link Bridge",
                    0x00000030,
                )
            except Exception:
                pass
            return 1

    from link_bridge.config import load_config
    from link_bridge.gui import LinkBridgeApp

    cfg = load_config()
    if args.dev:
        from link_bridge.dpi import enable_dpi_awareness

        enable_dpi_awareness()
        cfg.check_updates = False
        cfg.start_hidden = False
        app = LinkBridgeApp(cfg, dev=True)
        app.title("Harem Link Bridge  DEV  (local source)")
        app.mainloop()
        return 0

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
