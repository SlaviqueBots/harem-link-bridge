# Conjure Finder

Finds the cheapest `/conjure` path for a Danbooru or Rule34 post: compares direct summon cost against roster paths (conjure artist → Author, conjure character → reshape), cheapest first.

Ships as the **Conjure** tab inside Harem Link Bridge; the code here is the shared engine + a standalone Tkinter GUI.

## Run

Standalone (needs Python 3 with Tkinter):

```bash
python -m conjure_finder
```

Paste post URLs (one per line = separate jobs; space-separated on one line = any-of group). Copy the resulting command(s) into the bot chat.

## API keys

Danbooru username + API key and Rule34 key + user id go in **Settings…** inside the app (stored locally, never in this repo).

## Notes

- Pricing mirrors the bot: regular tags 25, character/title/author tags 50.
- One free reroll per summon; cheapest-first search stops at the first guarantee.
