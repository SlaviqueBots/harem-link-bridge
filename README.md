# Harem Link Bridge

Opens the bot’s Danbooru/Rule34 **link buttons** in your desktop browser (no Telegram confirm dialog).

**Source (this repo):** https://github.com/SlaviqueBots/harem-link-bridge  

Client-only. No bot tokens or server secrets live here. Compare builds to these files.

## First users — install

1. Get `HaremLinkBridge.exe` from the owner (one file).
2. Put it in any folder (e.g. Desktop) and double-click.
3. In the app follow **Setup**: **Pair with Telegram** → confirm in Telegram.
   - Alternate Method: DM the bot `/bridge`, then **Enter code…** in the app.
4. Wait until status says **Connected**. Closing the window keeps it in the tray.
5. Optional: tick **Start with Windows** (and **Start in tray**).

Auction DMs stay off unless you tick **Enable auction DMs**.

Left **double-click** the tray icon to show the window (or right-click → Show).

In the app, **Source code** opens this repository.

## Updates

The app checks for a newer build on startup and can self-update. Your
`harem_link_bridge.json` next to the exe is never overwritten.

## Build from source

```bat
pip install -r requirements.txt
pyinstaller --noconfirm harem_link_bridge.spec
```

## What this is / isn’t

| Included | Not included |
|----------|----------------|
| Desktop companion UI + WebSocket client | Telegram bot server |
| Pairing / autostart / auto-update client | Database, economy, tokens |
| Example config (no secrets) | Your personal `harem_link_bridge.json` |
