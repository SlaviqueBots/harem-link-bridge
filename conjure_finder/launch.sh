#!/usr/bin/env bash
# Launch Conjure Finder GUI (Linux / macOS / Git Bash).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/venv/bin/python" ]]; then
  exec "$ROOT/venv/bin/python" -m conjure_finder
fi
exec python3 -m conjure_finder
