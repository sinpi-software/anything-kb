import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

os.environ.setdefault("NEONEWS_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from sqlalchemy import text

import config
import engine
import ingest
from db import get_postgres_session
from models import Item, Source


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as s:
            s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_HAS_POSTGRES = _postgres_available()
requires_postgres = pytest.mark.skipif(not _HAS_POSTGRES, reason="Postgres not reachable")


@pytest.fixture
def item() -> Generator[str, None, None]:
    with get_postgres_session() as s:
        source = Source(kind="rss", locator=f"https://example.com/{os.urandom(4).hex()}.xml")
        s.add(source)
        s.flush()
        row = Item(
            source_id=source.id,
            dedup_key=os.urandom(4).hex(),
            url="https://example.com/a",
            title="A headline",
            content="Feed body.",
            published_at=datetime(2026, 7, 22, tzinfo=UTC),
        )
        s.add(row)
        s.commit()
        yield str(row.id)


@requires_postgres
def test_prepare_stores_extracted_text(monkeypatch: pytest.MonkeyPatch, item: str) -> None:
    monkeypatch.setattr(ingest, "extract_text", lambda url: "Full article body.")
    with get_postgres_session() as s:
        row = s.get(Item, item)
        assert row is not None
        ingest.prepare(s, row)
        s.commit()
        refreshed = s.get(Item, item)
        assert refreshed is not None
        assert refreshed.full_text == "Full article body."


@requires_postgres
def test_prepare_stamps_extracted_at_even_when_extraction_yields_nothing(
    monkeypatch: pytest.MonkeyPatch, item: str
) -> None:
    """A dead link must be attempted once, not forever."""
    monkeypatch.setattr(ingest, "extract_text", lambda url: None)
    with get_postgres_session() as s:
        row = s.get(Item, item)
        assert row is not None
        ingest.prepare(s, row)
        s.commit()
        refreshed = s.get(Item, item)
        assert refreshed is not None
        assert refreshed.extracted_at is not None
        assert refreshed.full_text == "Feed body."  # fell back to the feed's own content


@requires_postgres
def test_prepare_falls_back_to_feed_content_when_there_is_no_url(
    monkeypatch: pytest.MonkeyPatch, item: str
) -> None:
    monkeypatch.setattr(ingest, "extract_text", lambda url: pytest.fail("should not fetch without a url"))
    with get_postgres_session() as s:
        row = s.get(Item, item)
        assert row is not None
        row.url = None
        ingest.prepare(s, row)
        s.commit()
        refreshed = s.get(Item, item)
        assert refreshed is not None
        assert refreshed.full_text == "Feed body."


@requires_postgres
def test_submit_stores_the_job_id_and_sends_metadata(monkeypatch: pytest.MonkeyPatch, item: str) -> None:
    seen: dict[str, Any] = {}

    def fake_post(text_: str, metadata: dict[str, Any]) -> str:
        seen.update(metadata=metadata, text=text_)
        return "job-99"

    monkeypatch.setattr(ingest.engine, "post_content", fake_post)
    with get_postgres_session() as s:
        row = s.get(Item, item)
        assert row is not None
        row.full_text = "Full article body."
        assert ingest.submit(s, row) is True
        s.commit()
        refreshed = s.get(Item, item)
        assert refreshed is not None
        assert refreshed.job_id == "job-99"
    assert seen["text"] == "Full article body."
    # The engine dates its Source nodes from published_at in job metadata.
    assert seen["metadata"]["published_at"].startswith("2026-07-22T00:00:00")
    assert seen["metadata"]["url"] == "https://example.com/a"
    assert seen["metadata"]["label"] == "A headline"


@requires_postgres
def test_submit_failure_bumps_attempts_and_records_the_error(monkeypatch: pytest.MonkeyPatch, item: str) -> None:
    monkeypatch.setattr(
        ingest.engine, "post_content", lambda t, m: (_ for _ in ()).throw(engine.EngineError("HTTP 503"))
    )
    with get_postgres_session() as s:
        row = s.get(Item, item)
        assert row is not None
        row.full_text = "Body."
        assert ingest.submit(s, row) is False
        s.commit()
        refreshed = s.get(Item, item)
        assert refreshed is not None
        assert refreshed.attempts == 1
        assert refreshed.job_id is None
        assert refreshed.error is not None
        assert "503" in refreshed.error


@requires_postgres
def test_ingest_flow_skips_items_over_the_attempt_cap(monkeypatch: pytest.MonkeyPatch, item: str) -> None:
    """The cap is the guard against re-driving a broken item forever — the failure
    mode that, in the prior codebase, drained the account.

    The table isn't reset between tests, so unrelated pending rows left by earlier
    tests may also be swept here. The fail-trap is scoped to this test's own item
    (by a label unique to its id) so those unrelated rows pass through harmlessly
    instead of tripping it; the DB-state assertions below are the real proof that
    *this* item was never touched.
    """
    monkeypatch.setattr(ingest, "extract_text", lambda url: "Body.")
    label = f"cap-test-{item}"

    def fake_post(text_: str, metadata: dict[str, Any]) -> str:
        if metadata.get("label") == label:
            pytest.fail("should not submit: item is over the attempt cap")
        return "job-unrelated"  # some other pending row; not this test's concern

    monkeypatch.setattr(ingest.engine, "post_content", fake_post)
    with get_postgres_session() as s:
        row = s.get(Item, item)
        assert row is not None
        row.title = label
        row.attempts = config.MAX_SUBMIT_ATTEMPTS
        s.commit()
    ingest.ingest_items()
    with get_postgres_session() as s:
        refreshed = s.get(Item, item)
        assert refreshed is not None
        assert refreshed.job_id is None
        assert refreshed.attempts == config.MAX_SUBMIT_ATTEMPTS  # untouched: never even attempted


@requires_postgres
def test_ingest_flow_leaves_already_submitted_items_alone(monkeypatch: pytest.MonkeyPatch, item: str) -> None:
    """Proves the `job_id IS NULL` half of the sweep: an item that already has a
    job_id is never re-submitted. See the cap test above for why the fail-trap is
    scoped to this test's own item rather than any call at all."""
    monkeypatch.setattr(ingest, "extract_text", lambda url: "Body.")
    label = f"submitted-test-{item}"

    def fake_post(text_: str, metadata: dict[str, Any]) -> str:
        if metadata.get("label") == label:
            pytest.fail("should not resubmit: item already has a job_id")
        return "job-unrelated"

    monkeypatch.setattr(ingest.engine, "post_content", fake_post)
    with get_postgres_session() as s:
        row = s.get(Item, item)
        assert row is not None
        row.title = label
        row.job_id = "job-existing"
        s.commit()
    ingest.ingest_items()
    with get_postgres_session() as s:
        refreshed = s.get(Item, item)
        assert refreshed is not None
        assert refreshed.job_id == "job-existing"  # unchanged


def test_extract_text_returns_none_on_a_blocked_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import net_guard

    monkeypatch.setattr(
        ingest.net_guard, "fetch", lambda url, **kw: (_ for _ in ()).throw(net_guard.BlockedURLError("nope"))
    )
    assert ingest.extract_text("http://169.254.169.254/") is None
