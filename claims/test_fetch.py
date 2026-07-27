from datetime import UTC, datetime
from typing import Any

import config
import fetch
from fetch import canonicalize_url, fetch_page


def test_canonicalize_url_drops_fragment_and_tracking_params() -> None:
    assert canonicalize_url("https://Example.com/a/?utm_source=x&b=2#frag") == "https://example.com/a?b=2"


def test_canonicalize_url_sorts_the_query() -> None:
    assert canonicalize_url("https://example.com/a?z=1&a=2") == "https://example.com/a?a=2&z=1"


def test_fetch_page_returns_metadata_and_text(monkeypatch: Any) -> None:
    monkeypatch.setattr(fetch.net_guard, "fetch", lambda url, timeout=0: b"<html></html>")
    monkeypatch.setattr(
        fetch.trafilatura,
        "extract",
        lambda *a, **k: '{"title": "A headline", "author": "Ada", "date": "2026-07-01", "text": "Body text."}',
    )
    page = fetch_page("https://example.com/a")
    assert page.title == "A headline"
    assert page.author == "Ada"
    assert page.published_at == datetime(2026, 7, 1, tzinfo=UTC)
    assert page.text == "Body text."
    assert page.truncated is False


def test_fetch_page_truncates_and_flags(monkeypatch: Any) -> None:
    long_text = "x" * (config.FULL_TEXT_MAX_CHARS + 100)
    monkeypatch.setattr(fetch.net_guard, "fetch", lambda url, timeout=0: b"<html></html>")
    monkeypatch.setattr(fetch.trafilatura, "extract", lambda *a, **k: f'{{"text": "{long_text}"}}')
    page = fetch_page("https://example.com/a")
    assert len(page.text) == config.FULL_TEXT_MAX_CHARS
    assert page.truncated is True


def test_fetch_page_raises_when_there_is_no_readable_text(monkeypatch: Any) -> None:
    monkeypatch.setattr(fetch.net_guard, "fetch", lambda url, timeout=0: b"<html></html>")
    monkeypatch.setattr(fetch.trafilatura, "extract", lambda *a, **k: None)
    try:
        fetch_page("https://example.com/a")
    except fetch.NoReadableTextError:
        return
    raise AssertionError("expected NoReadableTextError")
