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

spec_dir = os.path.dirname(os.path.abspath(SPEC))
repo_root = os.path.dirname(spec_dir)

a = Analysis(
    [os.path.join(spec_dir, "__main__.py")],
    pathex=[repo_root],
    binaries=[],
    datas=[],
    hiddenimports=[
        "link_bridge",
        "link_bridge.config",
        "link_bridge.gui",
        "link_bridge.tray",
        "link_bridge.ws_client",
        "link_bridge.updater",
        "link_bridge.autostart",
        "link_bridge.browser_open",
        "websockets",
        "websockets.asyncio",
        "websockets.asyncio.client",
        "pystray",
        "pystray._win32",
        "PIL",
    ],
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
