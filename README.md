# Harem Link Bridge

Opens the bot's Danbooru/Rule34 **link buttons** in your desktop browser (no Telegram confirm dialog).

**This repository is the public source for the Windows companion.**  
Client-only. No bot tokens or server secrets. Compare the `.exe` behavior to these files.

Repo: https://github.com/SlaviqueBots/harem-link-bridge

## Install (users)

1. Get `HaremLinkBridge.exe` from the owner.
2. Double-click → **Pair with Telegram** (or Alternate Method: DM `/bridge` then **Enter code…**).
3. Wait for **Connected**. Close = tray. Optional: **Start with Windows**.

Auction DMs off by default (tick **Enable auction DMs** to turn on).

## Build from source

```bat
pip install -r link_bridge/requirements.txt
pyinstaller --noconfirm link_bridge/harem_link_bridge.spec
```

Output: `dist/HaremLinkBridge.exe`

## Layout

```
link_bridge/     Python package (GUI, WS client, updater, autostart)
LICENSE          MIT
```
