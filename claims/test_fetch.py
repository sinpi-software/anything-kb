from datetime import UTC, datetime
from typing import Any

import config
import fetch
from fetch import canonicalize_url, fetch_page


def test_canonicalize_url_drops_fragment_and_tracking_params() -> None:
    assert canonicalize_url("https://Example.com/a/?utm_source=x&b=2#frag") == "https://example.com/a?b=2"


def test_canonicalize_url_sorts_the_query() -> None:
    assert canonicalize_url("https://example.com/a?z=1&a=2") == "https://example.com/a?a=2&z=1"


def test_canonicalize_url_degrades_on_unparseable_input() -> None:
    """Task 6 feeds this function LLM-supplied citation URLs, which are untrusted
    third-party strings — a malformed one must not raise out of the grounding filter."""
    assert canonicalize_url("http://[::1") == "http://[::1"


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


def test_fetch_page_truncates_and_flags(monkeypatch: Any, caplog: Any) -> None:
    long_text = "x" * (config.FULL_TEXT_MAX_CHARS + 100)
    monkeypatch.setattr(fetch.net_guard, "fetch", lambda url, timeout=0: b"<html></html>")
    monkeypatch.setattr(fetch.trafilatura, "extract", lambda *a, **k: f'{{"text": "{long_text}"}}')
    with caplog.at_level("WARNING", logger="claims.fetch"):
        page = fetch_page("https://example.com/a")
    assert len(page.text) == config.FULL_TEXT_MAX_CHARS
    assert page.truncated is True
    assert any("truncated" in record.message for record in caplog.records)


def test_fetch_page_runs_real_trafilatura(monkeypatch: Any) -> None:
    """The other fetch_page tests all patch trafilatura.extract, so they never check the
    call signature (output_format="json", with_metadata=True) or the assumed JSON keys
    against the installed library. This one lets the real extractor run."""
    html = b"""
    <html>
      <head><title>A real headline</title></head>
      <body>
        <article>
          <h1>A real headline</h1>
          <p>This is the first paragraph of the article body, long enough for trafilatura
          to recognize it as the main content rather than boilerplate.</p>
          <p>This is a second paragraph, continuing the article with more real prose so the
          extractor has enough text to work with.</p>
        </article>
      </body>
    </html>
    """
    monkeypatch.setattr(fetch.net_guard, "fetch", lambda url, timeout=0: html)
    page = fetch_page("https://example.com/a")
    assert "first paragraph of the article body" in page.text


def test_fetch_page_raises_when_there_is_no_readable_text(monkeypatch: Any) -> None:
    monkeypatch.setattr(fetch.net_guard, "fetch", lambda url, timeout=0: b"<html></html>")
    monkeypatch.setattr(fetch.trafilatura, "extract", lambda *a, **k: None)
    try:
        fetch_page("https://example.com/a")
    except fetch.NoReadableTextError:
        return
    raise AssertionError("expected NoReadableTextError")
