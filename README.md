# Harem Link Bridge

Opens the bot’s Danbooru/Rule34 **link buttons** in your desktop browser (no Telegram confirm dialog).

**Source (this repo):** https://github.com/SlaviqueBots/harem-link-bridge  

**Releases (`.exe`):** https://github.com/SlaviqueBots/harem-link-bridge/releases

Client-only. No bot tokens or server secrets live here. Compare builds to these files.

## First users — install

1. Get `HaremLinkBridge.exe` from the owner (one file).
2. Put it in any folder (e.g. Desktop) and double-click.
3. In the app follow **Setup**: **Pair with Telegram** → confirm in Telegram.
   - Alternate Method: DM the bot `/bridge`, then **Enter code…** in the app.
4. Wait until status says **Connected**. Closing the window keeps it in the tray.
5. Optional: tick **Start with Windows** (and **Start in tray**).
6. Optional: **Pause when PC locked (Win+L)** — auto-pauses while the
   session is locked, resumes when you unlock (manual Pause still wins).

Links open in your browser and bring it to the front.

## Browser → Bridge (Firefox / Violentmonkey)

While browsing Danbooru or Rule34, you can send the open post to Link Bridge:

1. Keep **Harem Link Bridge** running (tray is fine). It listens on `http://127.0.0.1:8767/send`.
2. Install the userscript in [Violentmonkey](https://violentmonkey.github.io/) (Firefox) or
   [Tampermonkey](https://www.tampermonkey.net/) / Violentmonkey (Chrome/Edge).
   After an auto-update, a fresh copy is saved as **`harem_bridge_send.user.js`**
   next to `HaremLinkBridge.exe` — import that file if your manager did not update.
   **Chromium must use the userscript manager** — plain fetch cannot reach `http://127.0.0.1` from HTTPS booru tabs.
3. On a post page, use the compact button stack (top-right):

   ```
   checkres  conjure
   [      both      ]
   ```

   - **checkres** — max-res diagnostic to bot DMs only
   - **conjure** — Conjure Finder only (+ result DM from browser)
   - **both** — checkres and Conjure Finder

Results accumulate in the Conjure tab under **Recent findings** (click a
thumb for details + copy command).

Left **double-click** the tray icon to show the window (or right-click → Show).

In the app, **Source code** opens this repository.

## Updates

The app checks for a newer build on startup and can install it. After install it
closes and opens the folder — start `HaremLinkBridge.exe` yourself (auto-relaunch
was unreliable on Windows). Your `harem_link_bridge.json` next to the exe is
never overwritten.

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
