"""Source adapters: where raw items come from, before the engine sees them.

An adapter turns a configured source into `Item`s. Two ship: `rss` and `files`.
The protocol is the seam — a keyed third-party API adapter is one more module,
deliberately not built against an unchosen vendor.
"""

from __future__ import annotations

import calendar
import hashlib as hashlib  # unused until Task 4 (file adapter dedup keys)
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path as Path  # unused until Task 4 (file adapter)
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

import config as config  # unused until Task 4 (config-driven dispatch)
import net_guard as net_guard  # unused until Task 4 (fetch call)

_TRACKING_PARAMS = ("fbclid", "gclid", "mc_cid", "mc_eid")


@dataclass(frozen=True)
class Item:
    """One gathered item, before it is stored or submitted."""

    dedup_key: str
    title: str | None
    text: str | None
    url: str | None
    published_at: datetime | None


def canonicalize_url(url: str) -> str:
    """Normalize a URL for dedup: drop the fragment, sort the query, strip tracking params."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    query.sort()
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def _clean_title(title: str | None) -> str | None:
    """Strip HTML and entities. Some feeds put <a> markup in <title>."""
    if not title:
        return None
    return html.unescape(re.sub(r"<[^>]+>", "", title)).strip() or None


def _looks_like_url(value: str) -> bool:
    parts = urlsplit(value.strip())
    return parts.scheme in ("http", "https") and "<" not in value and "%3c" not in value.lower()


def _entry_url(entry: feedparser.FeedParserDict) -> str | None:
    """The link if it's clean, else the guid if it's a URL. Some feeds embed <a>
    markup in <link>, which feedparser mangles; the guid is then the reliable link."""
    for candidate in (entry.get("link"), entry.get("id") or entry.get("guid")):
        if candidate and _looks_like_url(candidate):
            return str(candidate.strip())
    return None


def _dedup_key(entry: feedparser.FeedParserDict) -> str | None:
    guid = entry.get("id") or entry.get("guid")
    if guid:
        return str(guid)
    link = entry.get("link")
    return canonicalize_url(link) if link else None


def _published_at(entry: feedparser.FeedParserDict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC) if parsed else None


def _entry_text(entry: feedparser.FeedParserDict) -> str | None:
    # Prefer the richest body: content:encoded, then the summary.
    content = entry.get("content")
    if content:
        value = content[0].get("value")
        return str(value) if value else None
    summary = entry.get("summary")
    return str(summary) if summary else None


def parse_feed(raw: bytes) -> tuple[str | None, list[Item]]:
    """Parse feed bytes into (feed_title, items). Raises ValueError if nothing parses."""
    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"failed to parse feed: {parsed.get('bozo_exception')}")
    items = []
    for entry in parsed.entries:
        key = _dedup_key(entry)
        if key is None:
            continue
        items.append(
            Item(
                dedup_key=key,
                title=_clean_title(entry.get("title")),
                text=_entry_text(entry),
                url=_entry_url(entry),
                published_at=_published_at(entry),
            )
        )
    return parsed.feed.get("title"), items
