import os
from datetime import UTC, datetime

os.environ.setdefault("NEONEWS_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from sqlalchemy import text

import poll
from db import get_postgres_session
from models import Source
from sources import Item as SourceItem
from sources import SourceSpec


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as s:
            s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


def _spec(suffix: str) -> SourceSpec:
    return SourceSpec(kind="rss", locator=f"https://example.com/{suffix}.xml", title="Example")


def _item(key: str) -> SourceItem:
    return SourceItem(
        dedup_key=key, title="T", text="body", url="https://example.com/a", published_at=datetime.now(UTC)
    )


@requires_postgres
def test_upsert_sources_is_idempotent() -> None:
    spec = _spec(os.urandom(4).hex())
    with get_postgres_session() as s:
        first = poll.upsert_sources(s, [spec])
        s.commit()
        second = poll.upsert_sources(s, [spec])
        s.commit()
    assert first == second


@requires_postgres
def test_store_items_returns_only_newly_inserted_rows() -> None:
    with get_postgres_session() as s:
        source_id = poll.upsert_sources(s, [_spec(os.urandom(4).hex())])[0]
        s.commit()
        first = poll.store_items(s, source_id, [_item("k1"), _item("k2")])
        s.commit()
        second = poll.store_items(s, source_id, [_item("k1"), _item("k2"), _item("k3")])
        s.commit()
    assert len(first) == 2
    assert len(second) == 1


@requires_postgres
def test_poll_source_records_success_and_resets_failure_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(poll, "fetch_items", lambda kind, locator: ("Discovered", [_item("a"), _item("b")]))
    with get_postgres_session() as s:
        source_id = poll.upsert_sources(s, [_spec(os.urandom(4).hex())])[0]
        source = s.get(Source, source_id)
        assert source is not None
        source.failure_count = 4
        s.commit()
        assert poll.poll_source(s, source) == 2
        s.commit()
        refreshed = s.get(Source, source_id)
        assert refreshed is not None
        assert refreshed.failure_count == 0
        assert refreshed.last_polled_at is not None


@requires_postgres
def test_poll_source_isolates_a_failing_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad feed bumps its failure count and returns 0 — it must not raise."""

    def boom(kind: str, locator: str) -> tuple[str | None, list[SourceItem]]:
        raise RuntimeError("feed is down")

    monkeypatch.setattr(poll, "fetch_items", boom)
    with get_postgres_session() as s:
        source_id = poll.upsert_sources(s, [_spec(os.urandom(4).hex())])[0]
        source = s.get(Source, source_id)
        assert source is not None
        s.commit()
        assert poll.poll_source(s, source) == 0
        s.commit()
        refreshed = s.get(Source, source_id)
        assert refreshed is not None
        assert refreshed.failure_count == 1


@requires_postgres
def test_poll_source_skips_items_with_no_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """An entry with no body is nothing to submit; storing it would permanently
    consume its dedup key and mask the real item if the feed later fills it in."""
    empty = SourceItem(dedup_key="empty", title="T", text=None, url=None, published_at=None)
    monkeypatch.setattr(poll, "fetch_items", lambda kind, locator: (None, [empty]))
    with get_postgres_session() as s:
        source_id = poll.upsert_sources(s, [_spec(os.urandom(4).hex())])[0]
        source = s.get(Source, source_id)
        assert source is not None
        s.commit()
        assert poll.poll_source(s, source) == 0
