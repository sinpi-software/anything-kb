import os
from collections.abc import Generator
from datetime import UTC, datetime

import config

# Point at the test suite's own database (config.POSTGRES_TEST_URL_ENV / _DEFAULT), by
# assignment rather than setdefault: the `import config` just above already ran its
# repo-root .env loading, and .env.sample sets NEONEWS_POSTGRES_URL to the operator's
# live database. Plain assignment (not setdefault) unconditionally overwrites whatever
# dotenv set, so the test suite can never end up pointed at the live database — the
# semantics of `=` vs `setdefault` are what protect this, not import order.
os.environ["NEONEWS_POSTGRES_URL"] = os.environ.get(config.POSTGRES_TEST_URL_ENV, config.POSTGRES_TEST_URL_DEFAULT)

import pytest
from sqlalchemy import delete, select, text

import poll
from db import get_postgres_session
from models import Item, Source
from sources import Item as SourceItem
from sources import SourceSpec


def _require_test_postgres() -> None:
    """Every test in this module needs neonews_test. A missing/unmigrated database
    must FAIL the suite, not silently skip it — a skip here means the one-time setup
    documented in README.md was never done, not that the environment legitimately
    lacks Postgres. Reported as a clean pass, that's exactly the failure mode this
    project has fought all the way through."""
    try:
        with get_postgres_session() as s:
            s.execute(text("SELECT 1"))
    except Exception as exc:
        raise RuntimeError(
            f"neonews_test is not reachable at NEONEWS_POSTGRES_URL={os.environ.get('NEONEWS_POSTGRES_URL')!r}. "
            "Create and migrate it once:\n"
            "  createdb -U ingestion -h localhost neonews_test\n"
            "  NEONEWS_TEST_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/neonews_test "
            "uv run alembic upgrade head"
        ) from exc


_require_test_postgres()


def _spec(suffix: str) -> SourceSpec:
    return SourceSpec(kind="rss", locator=f"https://example.com/{suffix}.xml", title="Example")


def _item(key: str) -> SourceItem:
    return SourceItem(
        dedup_key=key, title="T", text="body", url="https://example.com/a", published_at=datetime.now(UTC)
    )


@pytest.fixture
def source_ids() -> Generator[list[str], None, None]:
    """Tests append every source_id they create here. Teardown deletes exactly the
    Items and Sources under those ids — the same source_id-scoped pattern as
    test_ingest.py's `item` fixture and test_jobs.py's `make_item` fixture. Without
    this, every run of this file leaves `neonews_sources`/`neonews_items` rows behind
    with `job_id IS NULL, attempts = 0, content = "body"` — eligible for a REAL
    ingest.py run, which would spend OpenRouter tokens judging test garbage and write
    junk Source nodes into the live graph."""
    ids: list[str] = []
    yield ids
    with get_postgres_session() as s:
        for source_id in ids:
            s.execute(delete(Item).where(Item.source_id == source_id))
            s.execute(delete(Source).where(Source.id == source_id))
        s.commit()


def test_upsert_sources_is_idempotent(source_ids: list[str]) -> None:
    spec = _spec(os.urandom(4).hex())
    with get_postgres_session() as s:
        first = poll.upsert_sources(s, [spec])
        s.commit()
        second = poll.upsert_sources(s, [spec])
        s.commit()
    source_ids.extend(first)
    assert first == second


def test_store_items_returns_only_newly_inserted_rows(source_ids: list[str]) -> None:
    with get_postgres_session() as s:
        source_id = poll.upsert_sources(s, [_spec(os.urandom(4).hex())])[0]
        s.commit()
        source_ids.append(source_id)
        first = poll.store_items(s, source_id, [_item("k1"), _item("k2")])
        s.commit()
        second = poll.store_items(s, source_id, [_item("k1"), _item("k2"), _item("k3")])
        s.commit()
    assert len(first) == 2
    assert len(second) == 1


def test_poll_source_records_success_and_resets_failure_count(
    monkeypatch: pytest.MonkeyPatch, source_ids: list[str]
) -> None:
    monkeypatch.setattr(poll, "fetch_items", lambda kind, locator: ("Discovered", [_item("a"), _item("b")]))
    with get_postgres_session() as s:
        source_id = poll.upsert_sources(s, [_spec(os.urandom(4).hex())])[0]
        source_ids.append(source_id)
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


def test_poll_source_isolates_a_failing_source(monkeypatch: pytest.MonkeyPatch, source_ids: list[str]) -> None:
    """One bad feed bumps its failure count and returns 0 — it must not raise."""

    def boom(kind: str, locator: str) -> tuple[str | None, list[SourceItem]]:
        raise RuntimeError("feed is down")

    monkeypatch.setattr(poll, "fetch_items", boom)
    with get_postgres_session() as s:
        source_id = poll.upsert_sources(s, [_spec(os.urandom(4).hex())])[0]
        source_ids.append(source_id)
        source = s.get(Source, source_id)
        assert source is not None
        s.commit()
        assert poll.poll_source(s, source) == 0
        s.commit()
        refreshed = s.get(Source, source_id)
        assert refreshed is not None
        assert refreshed.failure_count == 1


def test_poll_source_skips_items_with_no_text(monkeypatch: pytest.MonkeyPatch, source_ids: list[str]) -> None:
    """An entry with no body is nothing to submit; storing it would permanently
    consume its dedup key and mask the real item if the feed later fills it in."""
    empty = SourceItem(dedup_key="empty", title="T", text=None, url=None, published_at=None)
    monkeypatch.setattr(poll, "fetch_items", lambda kind, locator: (None, [empty]))
    with get_postgres_session() as s:
        source_id = poll.upsert_sources(s, [_spec(os.urandom(4).hex())])[0]
        source_ids.append(source_id)
        source = s.get(Source, source_id)
        assert source is not None
        s.commit()
        assert poll.poll_source(s, source) == 0


def test_deactivate_removed_sources_turns_off_rows_missing_from_config(source_ids: list[str]) -> None:
    """Config is authoritative: an operator who deletes a feed from neonews.toml must
    see it stop being polled, not keep running forever because nothing ever clears
    `active`.

    `deactivate_removed_sources` iterates every `active IS TRUE` row in the table, not
    just this test's own — so the spec list passed in must retain every other active
    source's (kind, locator), or this call flips `active = False` on rows this test
    does not own (including, on the shared live database, real operator sources).
    Fetch every currently-active row first and keep all of them except this test's
    own, which is the one row under test here.
    """
    spec = _spec(os.urandom(4).hex())
    with get_postgres_session() as s:
        source_id = poll.upsert_sources(s, [spec])[0]
        source_ids.append(source_id)
        s.commit()
        keep_specs = [
            SourceSpec(kind=row.kind, locator=row.locator, title=row.title)
            for row in s.scalars(select(Source).where(Source.active.is_(True)))
            if str(row.id) != source_id
        ]
        deactivated = poll.deactivate_removed_sources(s, keep_specs)  # spec no longer in config
        s.commit()
        refreshed = s.get(Source, source_id)
    assert refreshed is not None
    assert refreshed.active is False
    assert deactivated >= 1


def test_deactivate_removed_sources_leaves_active_config_sources_alone(source_ids: list[str]) -> None:
    spec = _spec(os.urandom(4).hex())
    with get_postgres_session() as s:
        source_id = poll.upsert_sources(s, [spec])[0]
        source_ids.append(source_id)
        s.commit()
        poll.deactivate_removed_sources(s, [spec])  # still in config
        s.commit()
        refreshed = s.get(Source, source_id)
    assert refreshed is not None
    assert refreshed.active is True
