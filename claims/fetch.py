"""Turning a submitted link into readable text and metadata.

The single outbound fetch path is net_guard, so an operator-supplied URL can never
reach an internal address. Evidence pages are never fetched — OpenRouter's web plugin
does that server-side — so this is the app's only SSRF surface.

`canonicalize_url` is copied from neonews/sources.py: claims is standalone.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import trafilatura as trafilatura  # re-exported: tests patch fetch.trafilatura.extract
from pydantic import BaseModel

import config
import net_guard as net_guard  # re-exported: tests patch fetch.net_guard.fetch

_TRACKING_PARAMS = ("fbclid", "gclid", "mc_cid", "mc_eid")

logger = logging.getLogger("claims.fetch")


class NoReadableTextError(ValueError):
    """The page loaded but yielded no readable text — a JS-rendered app, a paywall,
    an image. Deterministic, so the caller dead-letters rather than retrying."""


class FetchedPage(BaseModel):
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    text: str
    truncated: bool = False


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


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def fetch_page(url: str) -> FetchedPage:
    """Fetch through the guard and extract readable text plus metadata.

    Raises BlockedURLError or any network error from net_guard (transient — the caller
    retries), or NoReadableTextError (deterministic — the caller dead-letters).
    """
    html = net_guard.fetch(url, timeout=config.FETCH_TIMEOUT_SECONDS)
    extracted = trafilatura.extract(
        html.decode("utf-8", errors="replace"), output_format="json", with_metadata=True
    )
    if not extracted:
        raise NoReadableTextError(f"no readable text at {url}")
    payload = json.loads(extracted)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise NoReadableTextError(f"no readable text at {url}")
    truncated = len(text) > config.FULL_TEXT_MAX_CHARS
    if truncated:
        logger.warning(
            "fetch: %s yielded %d chars, truncated to FULL_TEXT_MAX_CHARS=%d; claims made only in the "
            "discarded tail will never be extracted",
            url,
            len(text),
            config.FULL_TEXT_MAX_CHARS,
        )
    return FetchedPage(
        title=payload.get("title") or None,
        author=payload.get("author") or None,
        published_at=_parse_date(payload.get("date")),
        text=text[: config.FULL_TEXT_MAX_CHARS],
        truncated=truncated,
    )
