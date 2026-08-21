"""Persist Bulk wishlist search results beside the app."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from conjure_finder.bootstrap import ROOT
from conjure_finder.bulk import BulkCoveredPost, BulkPath, BulkResult
from conjure_finder.engine import ConjureOption

SAVES_DIR = ROOT / "conjure_finder_saves"
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._\- ]+")


@dataclass(frozen=True)
class SaveMeta:
    path: Path
    name: str
    saved_at: str
    wishlist_size: int
    path_count: int


def list_saves() -> list[SaveMeta]:
    if not SAVES_DIR.exists():
        return []
    out: list[SaveMeta] = []
    for path in sorted(SAVES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        out.append(
            SaveMeta(
                path=path,
                name=str(raw.get("name") or path.stem),
                saved_at=str(raw.get("saved_at") or ""),
                wishlist_size=int(raw.get("wishlist_size") or 0),
                path_count=len(raw.get("paths") or []),
            )
        )
    return out


def _safe_filename(name: str) -> str:
    cleaned = _SAFE_NAME.sub("", (name or "").strip())[:80].strip(" ._")
    return cleaned or "bulk_save"


def save_bulk_result(result: BulkResult, name: str = "") -> Path:
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    display = (name or "").strip() or f"Bulk {stamp}"
    fname = f"{_safe_filename(display)}_{stamp}.json"
    path = SAVES_DIR / fname
    payload = {
        "version": 1,
        "mode": "bulk",
        "name": display,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "source": result.source,
        "urls": list(result.urls),
        "own_author": bool(result.own_author),
        "own_character": bool(result.own_character),
        "wishlist_size": result.wishlist_size,
        "checked": result.checked,
        "elapsed_sec": result.elapsed_sec,
        "warnings": list(result.warnings),
        "paths": [_path_to_dict(bp) for bp in result.paths],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_bulk_result(path: Path) -> BulkResult:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Invalid save file.")
    paths: list[BulkPath] = []
    for row in raw.get("paths") or []:
        if not isinstance(row, dict):
            continue
        opt_raw = row.get("option") or {}
        if not isinstance(opt_raw, dict):
            continue
        opt = ConjureOption(
            tags=tuple(str(t) for t in (opt_raw.get("tags") or ())),
            cost=int(opt_raw.get("cost") or 0),
            pool_size=int(opt_raw.get("pool_size") or 0),
            guaranteed=bool(opt_raw.get("guaranteed")),
            command=str(opt_raw.get("command") or ""),
            hell_mode=opt_raw.get("hell_mode") or "",  # type: ignore[arg-type]
            expected_sessions=float(opt_raw.get("expected_sessions") or 0),
            expected_currency=float(opt_raw.get("expected_currency") or 0),
            note=str(opt_raw.get("note") or ""),
            path=str(opt_raw.get("path") or "conjure"),
        )
        covered_rows = row.get("covered") or []
        covered: list[BulkCoveredPost] = []
        for c in covered_rows:
            if not isinstance(c, dict):
                continue
            try:
                pid = int(c.get("post_id") or 0)
            except (TypeError, ValueError):
                continue
            if pid <= 0:
                continue
            covered.append(
                BulkCoveredPost(
                    post_id=pid,
                    preview_url=str(c.get("preview_url") or ""),
                    page_url=str(c.get("page_url") or ""),
                )
            )
        paths.append(BulkPath(option=opt, covered=tuple(covered)))
    return BulkResult(
        source=raw.get("source") or "danbooru",  # type: ignore[arg-type]
        paths=paths,
        warnings=list(raw.get("warnings") or []),
        elapsed_sec=float(raw.get("elapsed_sec") or 0),
        urls=list(raw.get("urls") or []),
        own_author=bool(raw.get("own_author")),
        own_character=bool(raw.get("own_character")),
        checked=int(raw.get("checked") or 0),
        wishlist_size=int(raw.get("wishlist_size") or 0),
    )


def _path_to_dict(bp: BulkPath) -> dict:
    opt = bp.option
    return {
        "option": {
            "tags": list(opt.tags),
            "cost": opt.cost,
            "pool_size": opt.pool_size,
            "guaranteed": opt.guaranteed,
            "command": opt.command,
            "hell_mode": opt.hell_mode,
            "expected_sessions": opt.expected_sessions,
            "expected_currency": opt.expected_currency,
            "note": opt.note,
            "path": opt.path,
        },
        "covered": [
            {
                "post_id": c.post_id,
                "preview_url": c.preview_url,
                "page_url": c.page_url,
            }
            for c in bp.covered
        ],
    }
