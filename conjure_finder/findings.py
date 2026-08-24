"""Recent Conjure Finder results — in-memory list with optional disk persistence."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from conjure_finder.bootstrap import ROOT

FINDINGS_NAME = "conjure_finder_findings.json"
FINDINGS_PATH = ROOT / FINDINGS_NAME
MAX_ENTRIES = 80


@dataclass
class FindingRecord:
    url: str
    page_url: str
    preview_url: str
    source: str
    post_id: int
    summary: str
    command: str
    saved_at: float = field(default_factory=time.time)

    @classmethod
    def from_result(cls, url: str, result: Any, *, summary: str) -> FindingRecord:
        cmd = ""
        best = getattr(result, "best", None)
        if best is not None:
            cmd = str(getattr(best, "command", "") or "")
        return cls(
            url=(url or "").strip(),
            page_url=str(getattr(result, "page_url", "") or url or "").strip(),
            preview_url=str(getattr(result, "preview_url", "") or "").strip(),
            source=str(getattr(result, "source", "") or ""),
            post_id=int(getattr(result, "post_id", 0) or 0),
            summary=summary,
            command=cmd,
        )


def _path() -> Path:
    return FINDINGS_PATH


def load_findings() -> list[FindingRecord]:
    path = _path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[FindingRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                FindingRecord(
                    url=str(item.get("url") or ""),
                    page_url=str(item.get("page_url") or ""),
                    preview_url=str(item.get("preview_url") or ""),
                    source=str(item.get("source") or ""),
                    post_id=int(item.get("post_id") or 0),
                    summary=str(item.get("summary") or ""),
                    command=str(item.get("command") or ""),
                    saved_at=float(item.get("saved_at") or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def save_findings(entries: list[FindingRecord]) -> None:
    path = _path()
    trimmed = entries[:MAX_ENTRIES]
    try:
        path.write_text(
            json.dumps([asdict(e) for e in trimmed], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def prepend_finding(entry: FindingRecord, existing: list[FindingRecord]) -> list[FindingRecord]:
    merged = [entry]
    seen = {(entry.url or "").lower()}
    for old in existing:
        key = (old.url or "").lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(old)
    merged = merged[:MAX_ENTRIES]
    save_findings(merged)
    return merged
