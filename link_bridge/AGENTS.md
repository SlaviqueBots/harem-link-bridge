# Agent instructions — Harem Link Bridge (`link_bridge/`)

Desktop client for the harem bot. **Bot API and handlers live on Koara** — Bridge only talks to them
over WebSocket (`bot/services/pc_bridge*.py`). Parent rules: `../AGENTS.md`.

---

## Foremost rule — do not ship stale bot code from the PC

Bridge features break when **the PC `bot/` tree is stale** but agents deploy it anyway, or when Bridge
expects an API that only exists locally.

| You edit | Koara deploy? | Bot restart? |
|----------|---------------|--------------|
| `link_bridge/` only (GUI, roster, omni tab, settings) | No | No |
| `bot/services/pc_bridge*.py`, handlers, callbacks | **Yes** — hot-upload only touched files | Usually yes (`--restart`) |
| Both Bridge UI + bot WS op in same task | Yes — include every server file you changed | Yes |

### Before any session that touches `bot/` or Bridge APIs

From repo root on PC:

```bash
python scripts/agent_bootstrap_pc.py
```

Pulls live Koara `bot/`, `tests/`, `scripts/` onto the PC. **Run even for “Bridge-only” bugs** if you
will read server handlers to debug (refine, omni_tap, register_cup, tournament_time, etc.).

### After server-side Bridge API changes

```bash
python scripts/hot_upload_koara.py --restart bot/services/pc_bridge.py bot/services/pc_bridge_omni.py …
```

Never `deploy_koara.py` from PC unless the owner asked to replace the whole tree.

### Bridge client relaunch (PC only)

- **Never** `python` / visible console — use `pythonw` or `Harem Link Bridge.vbs`.
- Dev: `pythonw -m link_bridge --dev --config link_bridge/harem_link_bridge.dev.json`

---

## Bot and Bridge parity — do not break the sibling surface

The owner uses **Telegram inline/callbacks** and **this desktop client** interchangeably. A fix in
`link_bridge/omni.py` that never updates `bot/handlers/inline.py` (or the reverse) looks like “random
breakage” on the other device.

| You change | Also verify / update |
|------------|----------------------|
| Bridge Omni / Refine / cup / craft button | Matching WS op in `pc_bridge*.py` **and** bot inline + `chosen_inline` + `callbacks` if `@bot` exposes the same mode |
| New `@bot` inline mode or callback keyboard | Bridge tab or WS op if the feature exists in Omni; shared service in `bot/services/*` |
| `register_cup`, tournament, refine, variant, mirror, … | **Both** `pc_bridge` handler and Telegram path (see parent `AGENTS.md` § Bot and Bridge parity) |

**Before finishing:** exercise the flow in Telegram (`@bot …` or button) **and** in Bridge when both
exist. Hot-upload every server file you touched; relaunch Bridge with `pythonw` after client edits.

---

## Module map (open only what you need)

| Area | Path |
|------|------|
| Entry / tray | `__main__.py`, `gui.py`, `tray.py` |
| WebSocket client | `ws_client.py`, `config.py` |
| Roster / thumbs | `roster.py`, `thumb_grid.py`, `thumb_menu.py` |
| Omni / Refine UI | `omni.py` (tabs, crafts, refine picker) |
| Sets / tamed / market | `sets.py`, `tamed.py`, `primed.py`, `market.py`, `market_lot.py` |
| Help / hidden state | `help_dialog.py`, `market_hidden.py`, `primed_hidden.py` |
| Server protocol | `../bot/services/pc_bridge.py`, `pc_bridge_omni.py`, `pc_bridge_dm_craft.py` |

Conjure Finder is **`../conjure_finder/`** — Bridge module only, not a separate product.

---

## Common failure modes (stale code)

1. **Mode in `inline_parse.py` but no `inline.py` / `chosen_inline.py` handler** — e.g. `refine` showed
   roster tiles with no picker. Fix the handler; do not re-add old confirm buttons from grep.
2. **WS op implemented in Bridge but not in `pc_bridge.py`** — client gets `*_err` or silent no-op.
3. **Callback prefix exists in `callbacks.py` but nothing attaches that keyboard on new posts** —
   leftover handlers are for old messages only.
4. **Full PC deploy overwrites Koara** with files this chat never edited — restores weeks-old UX.
5. **Bot-only or Bridge-only fix** — inline handler added but WS op missing (or omni tab wired but
   `@bot` mode still shows a dumb roster grid). See parent **`AGENTS.md` § Bot and Bridge parity**.

When adding a craft or mode: implement **client** (`link_bridge/omni.py` or inline grid), **WS/server**
(`pc_bridge_omni.run_omni_tap`, `pc_bridge_dm_craft`, etc.), **and** Telegram inline +
`chosen_inline` + `callbacks` when `@bot` lists that mode — then hot-upload all server files.

---

## Dark theme skinning (required for new Bridge UI)

Default is **dark**. Setup can switch to light (`ui_theme` in config). Every new panel,
dialog, or overlay must respect the active palette — never leave default Windows gray on
`tk.Frame` / `tk.Label` / `Text` / `Canvas`.

| Surface | How |
|---------|-----|
| Palette | `surface_for(widget)` or `palette(mode)` from `link_bridge/theme.py` |
| **ttk** widgets | Rely on `apply_app_theme()` — `TFrame`, `TLabel`, `Toolbutton`, `TCheckbutton`, etc. |
| **tk** widgets | Set `bg` / `fg` / `insertbackground` / `selectbackground` from palette (`bg`, `fg`, `log_bg`, `select`) |
| Modal dialogs | Copy `text_edit_dialog.py` or `help_dialog.py` — `win.configure(bg=…)`, muted subtitles use `pal["muted"]`; scrollbars use `ttk.Scrollbar(…, style="Vertical.TScrollbar")`, not `ScrolledText` |
| Gallery / scroll areas | `surface_for` → `canvas` for `Canvas` bg, `bg` / `bg2` for card chrome |
| Active toggle | `Accent.TCheckbutton` when a mode is on (hide mode in Market / Primed) |
| Omni craft progress | Thin `tk.Frame` overlay using `accent` only — no `ttk.Progressbar` trough (avoids reserved black band) |

### New features (skin + persistence checklist)

| Feature | Client | Server | Persisted locally |
|---------|--------|--------|-----------------|
| **Taming** tab (top) | `roster.py` — sub-tabs **Tamed** \| **Primed** | `kind=primed` in `pc_bridge.py` | — |
| **Primed** grid | `primed.py`, origin preview thumbs | WS roster page | `primed_hidden.json` (hide LMB) |
| **Market** hide / show hidden | `market.py` — hide mode (LMB), show hidden = hidden only | — | `market_hidden.json` |
| **Market** min/max price | `market.py` → `gui._save_market_prices` | — | `market_min_price`, `market_max_price` in active config JSON |
| **Help** dialog | `help_dialog.py` — dark `Text` + themed `Vertical.TScrollbar` | — | — |
| **Flavoured** LMB | `roster.py` — `LMB → Flavour` toggle (flavoured/unflavoured only; default on) | — | `left_click_flavour` in config |
| **Setup → Help** | `gui.py` `_show_help` | — | — |

**Config paths:** production `harem_link_bridge.json`; dev `--config link_bridge/harem_link_bridge.dev.json`
stores filters separately — do not overwrite dev config when testing prod values.

**Adding a field to `BridgeConfig`:** wire it in **both** `load_config()` and `save_config()` (via
`asdict`). Missing `load_config` keys silently reset on every relaunch (e.g. market prices).

---

## Tests (when touching Bridge ↔ bot contract)

```bash
pytest tests/test_pc_bridge.py tests/test_pc_bridge_omni.py tests/test_commands_and_callbacks_registration.py tests/test_behavior_locks.py -q
```

Registration tests = wiring only. `test_behavior_locks.py` = dropped UX must stay dropped.
