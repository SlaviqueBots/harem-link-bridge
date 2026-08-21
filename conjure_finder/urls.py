"""Parse Danbooru / Rule34 post URLs into (source, post_id)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse

Source = Literal["danbooru", "rule34"]

_DANBOORU_HOSTS = {
    "danbooru.donmai.us",
    "www.donmai.us",
    "donmai.us",
}
_R34_HOSTS = {
    "rule34.xxx",
    "www.rule34.xxx",
}

# Split same-line any-of groups on whitespace or explicit |.
_JOB_TOKEN_SPLIT = re.compile(r"[\s|]+")


@dataclass(frozen=True)
class ParsedPostUrl:
    source: Source
    post_id: int
    url: str


def split_url_jobs(raw: str) -> list[list[str]]:
    """Split pasted text into search jobs.

    - Each non-empty line is one job.
    - Multiple URL tokens on the same line (space or ``|``) form an any-of group.
    - One URL per line keeps today's independent-search behavior.
    """
    jobs: list[list[str]] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens: list[str] = []
        seen: set[str] = set()
        for token in _JOB_TOKEN_SPLIT.split(line):
            token = token.strip().strip("<>\"',")
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            tokens.append(token)
        if tokens:
            jobs.append(tokens)
    return jobs


def flatten_wishlist_urls(raw: str) -> list[str]:
    """All URL tokens from pasted text as one flat wishlist (order preserved, deduped)."""
    out: list[str] = []
    seen: set[str] = set()
    for job in split_url_jobs(raw):
        for url in job:
            key = url.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(url)
    return out


def parse_post_url(raw: str) -> ParsedPostUrl:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Paste a Danbooru or Rule34 post URL.")

    # Bare numeric id is ambiguous — require a full URL.
    if re.fullmatch(r"\d+", text):
        raise ValueError("Need a full post URL (danbooru.donmai.us or rule34.xxx), not just an id.")

    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text

    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()

    if host in _DANBOORU_HOSTS or host.endswith(".donmai.us"):
        m = re.search(r"/posts/(\d+)", parsed.path or "")
        if not m:
            raise ValueError("Danbooru URL must look like https://danbooru.donmai.us/posts/12345")
        return ParsedPostUrl(source="danbooru", post_id=int(m.group(1)), url=text)

    if host in _R34_HOSTS or host.endswith("rule34.xxx"):
        qs = parse_qs(parsed.query or "")
        ids = qs.get("id") or []
        if ids and str(ids[0]).isdigit():
            return ParsedPostUrl(source="rule34", post_id=int(ids[0]), url=text)
        m = re.search(r"(?:^|[?&])id=(\d+)", text)
        if m:
            return ParsedPostUrl(source="rule34", post_id=int(m.group(1)), url=text)
        raise ValueError("Rule34 URL must include id=… (post view page).")

    raise ValueError("Unsupported site. Use danbooru.donmai.us or rule34.xxx post links.")
