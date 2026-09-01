# Conjure Finder (PC tool)

> **Deprecated as a standalone app.** Use the **Conjure** tab in **Harem Link Bridge** (`python -m link_bridge --dev`). See `conjure_finder/AGENTS.md`.


Standalone English GUI — cheapest `/conjure` path for a Danbooru or Rule34 post.

**Does not modify the bot.**

## One-click launch (PC)

In the project folder (next to `bot/`), double-click:

**`Conjure Finder.vbs`** ← use this on Windows

Fallbacks: `Conjure Finder.bat` (Windows) or `Conjure Finder.sh` (Linux/macOS).

First click installs deps and pulls API keys if needed. Optional: Desktop shortcut to the `.vbs`.

## API keys / Settings

Open **Settings…** in the app to enter:

- Danbooru username + API key
- Rule34 API key + user id

Saved to **`conjure_finder.env`** next to `bot/` (gitignored). That file overrides any shared project `.env` so a distributed build never needs your server keys.

During development, keys already in `.env` still work until you save Settings.

## Behavior

- Paste one or many URLs (**one per line** = separate searches). Danbooru and Rule34 queues run **in parallel**.
- **Same line** (space or `|`) = **any-of** group: success = get any of those posts (variants / same author sets).
- Max 2 tags; pricing matches the bot (general 25 / premium 50).
- 1 free reroll ⇒ pool ≤ 2 is a single-conjure guarantee.
- Same-tag pity (bot): expected sessions/cost assume without-replacement until the pool reshuffles. Any-of groups score with K acceptable hits in the pool.
- Also considers roster paths: conjure artist → Author, or conjure character → reshape / reshape_m (solo vs not). Rule34 AI uses `/conjure_hell_slop`.
- Searches cheapest-first; stops on the first guarantee per job.
- English UI; Copy command(s) copies every successful result.

## Requirements

Python 3 with tcl/tk (Windows installer options: PATH + tcl/tk).

## Personal vs share builds

| | Personal (this repo) | Friends (portable exe) |
|---|---|---|
| Keys | Your `.env` / `conjure_finder.env` | Empty — they use **Settings…** |
| Launch | `Conjure Finder.vbs` here | **`Conjure Finder.exe`** (no Python) |
| Refresh share | `python scripts/build_conjure_finder_exe.py` | Writes `Projects/Conjure Finder.exe` |

Source-only share (needs Python): `python scripts/export_conjure_finder_share.py --zip`
