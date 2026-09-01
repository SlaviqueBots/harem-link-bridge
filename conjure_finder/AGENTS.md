# Conjure Finder — agent notes

**The standalone Conjure Finder app is deprecated.** All Conjure Finder work happens inside **Harem Link Bridge** (`link_bridge/`), on the **Conjure** tab (`ConjureFinderApp` embedded in `link_bridge/gui.py`).

Do **not** launch or ship fixes via `Conjure Finder.vbs`, `python -m conjure_finder`, or the frozen exe unless the owner explicitly asks for a legacy share build.

## Where to work

| What | Where |
|------|--------|
| UI (embedded) | `conjure_finder/gui.py` — `embedded=True` |
| Bridge tab wiring | `link_bridge/gui.py` → `_ensure_conjure_tab` |
| Engine / pricing | `conjure_finder/engine.py` |
| API clients | `bot/services/danbooru.py`, `bot/services/rule34.py` |
| Local dev | `python -m link_bridge --dev --config link_bridge/harem_link_bridge.json` |

## Test

1. Start Bridge dev (above).
2. Open the **Conjure** tab.
3. Paste Danbooru / Rule34 post URLs and run.

Shared engine code lives under `conjure_finder/`; Bridge imports it read-only.
