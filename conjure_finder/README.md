# Conjure Finder (PC tool)

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
- **Bulk wishlist** mode (toggle at the top): paste many posts of the same character/artist. Ranks cheap paths that hit *any* of them; shared rare tags help. Optional **Already have this character / author** skips the 50 conjure cost on reshape/Author paths. Low-res previews are clickable; **Save result…** / **Load saved…** keep ranked paths for later.
- Max 2 tags; pricing matches the bot (general 25 / premium 50).
- 1 free reroll ⇒ pool ≤ 2 is a single-conjure guarantee.
- Same-tag pity (bot): expected sessions/cost assume without-replacement until the pool reshuffles. Any-of groups score with K acceptable hits in the pool.
- Also considers `/beckon` / `/beckon_hell`: one category-0 general tag, cost 30, up to 10 peeks — often wins on sparsely populated tags (pool ≤ 10 is a beckon guarantee). Not used for Rule34 AI posts (no beckon slop command).
- Also considers roster paths: conjure artist → Author, or conjure character → reshape / reshape_m (solo vs not). Rule34 AI uses `/conjure_hell_slop`.
- Also considers targeted **@refine**: same Author/reshape crafts with one fixed −exclude (`greyscale`, `simple_background`, …) when the target lacks that tag. Caps live probes so search time stays bounded. Mega **ALL** refine is skipped (unreliable pool math + many extra counts).
- Searches cheapest-first; stops on the first guarantee per job.
- English UI; Copy command(s) copies every successful result.

## Requirements

Python 3 with tcl/tk (Windows installer options: PATH + tcl/tk). Needs `Pillow` for Bulk wishlist thumbnails (installed via `requirements.txt`).

## Personal vs share builds

| | Personal (this repo) | Friends (portable exe) |
|---|---|---|
| Keys | Your `.env` / `conjure_finder.env` | Empty — they use **Settings…** |
| Launch | `Conjure Finder.vbs` here | **`ConjureFinder-v*-windows.exe`** from a GitHub Release |
| Refresh share | `python scripts/build_conjure_finder_exe.py` then tag `vX.Y.Z` | Friends download the Release asset |

See the root `README.md` for the full release flow.
