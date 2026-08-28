# -*- mode: python ; coding: utf-8 -*-
# Build on Windows:
#   cd <repo root>
#   pip install -r link_bridge/requirements.txt
#   pyinstaller --noconfirm link_bridge/harem_link_bridge.spec
#
# Output: dist/HaremLinkBridge.exe
# Ship the exe. Config is auto-created beside it as harem_link_bridge.json.
# Later updates: clients self-update from Koara :8766 — see scripts/publish_link_bridge.py

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

spec_dir = os.path.dirname(os.path.abspath(SPEC))
repo_root = os.path.dirname(spec_dir)

# Bundled ffmpeg binary (imageio-ffmpeg) — no system install for adopters.
_ffmpeg_datas = collect_data_files("imageio_ffmpeg")
# CA roots for httpsx / Danbooru / Rule34 (avoids bare [Errno 2] on HTTPS).
_certifi_datas = collect_data_files("certifi")

_hidden = [
    "link_bridge",
    "link_bridge.config",
    "link_bridge.gui",
    "link_bridge.tray",
    "link_bridge.ws_client",
    "link_bridge.updater",
    "link_bridge.tournament_alarm",
    "link_bridge.autostart",
    "link_bridge.browser_open",
    "link_bridge.session_lock",
    "link_bridge.singleton",
    "link_bridge.roster",
    "link_bridge.sets",
    "link_bridge.tamed",
    "link_bridge.thumb_menu",
    "link_bridge.set_names",
    "link_bridge.text_edit_dialog",
    "link_bridge.gallery",
    "link_bridge.member_browse",
    "link_bridge.open_image",
    "link_bridge.themes_admin",
    "link_bridge.focus_telegram",
    "link_bridge.theme",
    "link_bridge.market",
    "link_bridge.omni",
    "link_bridge.pig_snout",
    "link_bridge.video_still",
    "imageio_ffmpeg",
    "websockets",
    "websockets.asyncio",
    "websockets.asyncio.client",
    "pystray",
    "pystray._win32",
    "PIL",
    "httpx",
    "httpx._client",
    "certifi",
    "dotenv",
    "dotenv.main",
    "link_bridge.ssl_certs",
    # Conjure Finder tab (embedded) + bot helpers it imports.
    "bot.core.config",
    "bot.core.rate_limit",
    "bot.services.conjure_pricing",
    "bot.services.danbooru",
    "bot.services.rule34",
    "bot.utils.artist_tags",
    "bot.utils.booru_tags",
    "bot.utils.r34_tags",
    "bot.utils.media_urls",
    "bot.utils.media_validate",
    "bot.utils.media_policy",
    "bot.utils.formatting",
    "bot.utils.currency",
    "bot.utils.post_tags",
]
_hidden += collect_submodules("conjure_finder")

a = Analysis(
    [os.path.join(spec_dir, "__main__.py")],
    pathex=[repo_root],
    binaries=[],
    datas=_ffmpeg_datas + _certifi_datas,
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="HaremLinkBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
