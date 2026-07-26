import os
from collections.abc import Callable, Generator

os.environ.setdefault("NEONEWS_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from sqlalchemy import delete, text

import engine
import jobs
from db import get_postgres_session
from models import Item, Source


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as s:
            s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.fixture
def make_item() -> Generator[Callable[..., tuple[str, str]], None, None]:
    """Factory for a throwaway Item under its own random-locator Source (see
    test_ingest.py's `item` fixture for why: a random locator can never collide
    with another test's, and teardown scoped to source_id deletes exactly the rows
    this test created).

    `check_jobs()` sweeps every item in the table with a non-terminal job_id, so
    leftover rows from other test runs *will* be visited alongside whatever this
    fixture creates — tests must tolerate that rather than assume an empty table.
    Returns (item_id, job_id) so a test can scope its `engine.job_status` mock to
    the row it actually owns."""
    source_ids: list[str] = []

    def _make(status: str | None, job_id: str | None = None) -> tuple[str, str]:
        job_id = job_id or f"job-{os.urandom(4).hex()}"
        with get_postgres_session() as s:
            source = Source(kind="rss", locator=f"https://example.com/{os.urandom(4).hex()}.xml")
            s.add(source)
            s.flush()
            row = Item(
                source_id=source.id,
                dedup_key=os.urandom(4).hex(),
                job_id=job_id,
                job_status=status,
                content="body",
            )
            s.add(row)
            s.commit()
            source_ids.append(str(source.id))
            return str(row.id), job_id

    yield _make
    with get_postgres_session() as s:
        for source_id in source_ids:
            s.execute(delete(Item).where(Item.source_id == source_id))
            s.execute(delete(Source).where(Source.id == source_id))
        s.commit()


@requires_postgres
def test_records_a_terminal_status(monkeypatch: pytest.MonkeyPatch, make_item: Callable[..., tuple[str, str]]) -> None:
    item_id, _job_id = make_item("pending")
    monkeypatch.setattr(engine, "job_status", lambda job_id: {"status": "done"})
    jobs.check_jobs()
    with get_postgres_session() as s:
        row = s.get(Item, item_id)
        assert row is not None
        assert row.job_status == "done"


@requires_postgres
def test_skipped_is_terminal_and_normal(
    monkeypatch: pytest.MonkeyPatch, make_item: Callable[..., tuple[str, str]]
) -> None:
    """`skipped` means the engine judged the item irrelevant. That is a verdict,
    not a failure — recorded, never retried."""
    item_id, _job_id = make_item("pending")
    monkeypatch.setattr(
        engine, "job_status", lambda job_id: {"status": "skipped", "relevance_reason": "off-topic"}
    )
    jobs.check_jobs()
    with get_postgres_session() as s:
        row = s.get(Item, item_id)
        assert row is not None
        assert row.job_status == "skipped"
        assert row.error is None


@requires_postgres
def test_does_not_repoll_a_terminal_item(
    monkeypatch: pytest.MonkeyPatch, make_item: Callable[..., tuple[str, str]]
) -> None:
    """The table isn't reset between tests, so unrelated non-terminal rows left by
    other runs may also be swept here (confirmed present against the live DB used
    for this task). The fail-trap below is scoped to this test's own job_id so
    those unrelated rows pass through harmlessly instead of tripping it; the
    unchanged job_status assertion is the real proof this item was never re-polled."""
    item_id, job_id = make_item("done")

    def fake_status(job_id_arg: str) -> dict[str, str]:
        if job_id_arg == job_id:
            pytest.fail("should not re-poll a terminal item")
        return {"status": "pending"}  # leave unrelated leftover rows as they were

    monkeypatch.setattr(engine, "job_status", fake_status)
    jobs.check_jobs()
    with get_postgres_session() as s:
        row = s.get(Item, item_id)
        assert row is not None
        assert row.job_status == "done"  # unchanged


@requires_postgres
def test_a_failing_status_call_does_not_sink_the_run(
    monkeypatch: pytest.MonkeyPatch, make_item: Callable[..., tuple[str, str]]
) -> None:
    item_id, _job_id = make_item("pending")
    monkeypatch.setattr(
        engine, "job_status", lambda job_id: (_ for _ in ()).throw(engine.EngineError("HTTP 500"))
    )
    jobs.check_jobs()  # must not raise
    with get_postgres_session() as s:
        row = s.get(Item, item_id)
        assert row is not None
        assert row.job_status == "pending"
