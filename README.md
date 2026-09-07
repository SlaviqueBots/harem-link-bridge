# Harem Link Bridge

Windows desktop companion for a Telegram harem collection bot: roster grid, Omni crafts, market, sets, and Conjure Finder — without switching to Telegram.

**Releases (`.exe`):** https://github.com/SlaviqueBots/harem-link-bridge/releases

Client only. No bot tokens or server data live here.

## Install

1. Download `HaremLinkBridge.exe` from Releases, put it anywhere, double-click.
2. Pair with Telegram: in the app follow **Setup** (or DM the bot `/bridge`, then **Enter code…** in the app).
3. Wait for **Connected**. Closing the window keeps it in the tray; double-click the tray icon to reopen.

Optional: **Start with Windows**, **Start in tray**, **Pause when PC locked**.

## Browser hook (Danbooru / Rule34)

Send the open post to the Conjure tab while browsing:

1. Keep the app running (tray is fine).
2. Install `link_bridge/userscript/harem_bridge_send.user.js` in Violentmonkey/Tampermonkey.
3. Use the button stack on a post page: **checkres** (max-res check), **conjure** (cheapest summon path), **both**.

## Updates

The app checks for new builds on startup and installs them. Your `harem_link_bridge.json` is never overwritten.

## Build from source

```bat
pip install -r link_bridge/requirements.txt
pyinstaller --noconfirm link_bridge/harem_link_bridge.spec
```

## Scope

| Included | Not included |
|----------|----------------|
| Desktop UI + WebSocket client | Telegram bot server |
| Pairing, autostart, auto-update | Database, economy, tokens |
| Example config (no secrets) | Your personal `harem_link_bridge.json` |
