"""Source adapters: where raw items come from, before the engine sees them.

An adapter turns a configured source into `Item`s. Two ship: `rss` and `files`.
The protocol is the seam — a keyed third-party API adapter is one more module,
deliberately not built against an unchosen vendor.
"""

from __future__ import annotations

import calendar
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

import net_guard

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


# --- source specs and adapters -------------------------------------------------

# Extensions the files adapter treats as text. Anything else is ignored rather than
# decoded as garbage.
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".text"})


@dataclass(frozen=True)
class SourceSpec:
    """A source as declared in neonews.toml."""

    kind: str
    locator: str
    title: str | None


def _rss_items(locator: str) -> tuple[str | None, list[Item]]:
    return parse_feed(net_guard.fetch(locator))


def _file_items(locator: str) -> tuple[str | None, list[Item]]:
    """Read a drop directory. The dedup key is path + content hash, so an edited
    file is a new item and an unchanged one is never re-submitted."""
    directory = Path(locator)
    if not directory.is_dir():
        return None, []
    items = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        items.append(
            Item(
                dedup_key=f"{path.name}:{digest}",
                title=path.name,
                text=text,
                url=None,
                published_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
            )
        )
    return None, items


_ADAPTERS = {"rss": _rss_items, "files": _file_items}


def fetch_items(kind: str, locator: str) -> tuple[str | None, list[Item]]:
    """Dispatch to the adapter for `kind`. Returns (discovered_title, items)."""
    adapter = _ADAPTERS.get(kind)
    if adapter is None:
        raise ValueError(f"unknown source kind: {kind}")
    return adapter(locator)


def load_config(path: Path) -> tuple[list[SourceSpec], str]:
    """Read neonews.toml into (source specs, beat prompt). Config lives in git;
    runtime state lives in Postgres."""
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    specs = []
    for entry in data.get("sources", []):
        kind = entry.get("kind", "")
        if kind not in _ADAPTERS:
            raise ValueError(f"unknown source kind: {kind}")
        locator = entry.get("url") or entry.get("path") or ""
        if not locator:
            raise ValueError(f"source of kind {kind} has neither url nor path")
        specs.append(SourceSpec(kind=kind, locator=locator, title=entry.get("title")))
    return specs, str(data.get("editorial", {}).get("beat", ""))
