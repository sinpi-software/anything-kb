import os
from collections.abc import Callable, Generator

import config

# Point at the test suite's own database (config.POSTGRES_TEST_URL_ENV / _DEFAULT), by
# assignment rather than setdefault: the `import config` just above already ran its
# repo-root .env loading, and .env.sample sets NEONEWS_POSTGRES_URL to the operator's
# live database. Plain assignment (not setdefault) unconditionally overwrites whatever
# dotenv set, so the test suite can never end up pointed at the live database — the
# semantics of `=` vs `setdefault` are what protect this, not import order.
os.environ["NEONEWS_POSTGRES_URL"] = os.environ.get(config.POSTGRES_TEST_URL_ENV, config.POSTGRES_TEST_URL_DEFAULT)

import pytest
from sqlalchemy import delete, text

import engine
import jobs
from db import get_postgres_session
from models import Item, Source


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


def test_records_a_terminal_status(monkeypatch: pytest.MonkeyPatch, make_item: Callable[..., tuple[str, str]]) -> None:
    item_id, job_id = make_item("pending")

    def fake_status(job_id_arg: str) -> dict[str, str]:
        if job_id_arg == job_id:
            return {"status": "done"}
        # Inert for every other row the table-wide sweep visits: `jobs.py`'s
        # `if status:` guard treats an empty status as "no verdict yet" and
        # leaves the row untouched, so unrelated leftover rows are never
        # rewritten with a fabricated terminal verdict.
        return {"status": ""}

    monkeypatch.setattr(engine, "job_status", fake_status)
    jobs.check_jobs()
    with get_postgres_session() as s:
        row = s.get(Item, item_id)
        assert row is not None
        assert row.job_status == "done"


def test_skipped_is_terminal_and_normal(
    monkeypatch: pytest.MonkeyPatch, make_item: Callable[..., tuple[str, str]]
) -> None:
    """`skipped` means the engine judged the item irrelevant. That is a verdict,
    not a failure — recorded, never retried."""
    item_id, job_id = make_item("pending")

    def fake_status(job_id_arg: str) -> dict[str, str]:
        if job_id_arg == job_id:
            return {"status": "skipped", "relevance_reason": "off-topic"}
        return {"status": ""}  # inert for rows this test does not own

    monkeypatch.setattr(engine, "job_status", fake_status)
    jobs.check_jobs()
    with get_postgres_session() as s:
        row = s.get(Item, item_id)
        assert row is not None
        assert row.job_status == "skipped"
        assert row.error is None


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
        # Inert, not "pending": an empty status hits `jobs.py`'s `if status:` guard
        # and leaves the row exactly as it was, rather than overwriting whatever
        # unrelated leftover rows' actual status happens to be with a literal string.
        return {"status": ""}

    monkeypatch.setattr(engine, "job_status", fake_status)
    jobs.check_jobs()
    with get_postgres_session() as s:
        row = s.get(Item, item_id)
        assert row is not None
        assert row.job_status == "done"  # unchanged


def test_check_jobs_respects_the_batch_size(
    monkeypatch: pytest.MonkeyPatch, make_item: Callable[..., tuple[str, str]]
) -> None:
    """Unbounded, this sweep issues one 30s-timeout GET per outstanding row: with the
    engine down for a while, thousands of pending rows can occupy every serve() worker
    slot with stalled check-jobs runs. Shrink the batch to 0 and confirm nothing is
    checked, however many genuinely outstanding rows exist in the table."""
    make_item("pending")
    monkeypatch.setattr(jobs.config, "JOBS_BATCH_SIZE", 0)
    monkeypatch.setattr(engine, "job_status", lambda job_id: pytest.fail("should not be called: batch size is 0"))
    result = jobs.check_jobs()
    assert result["checked"] == 0


def test_a_failing_status_call_does_not_sink_the_run(
    monkeypatch: pytest.MonkeyPatch, make_item: Callable[..., tuple[str, str]]
) -> None:
    item_id, _job_id = make_item("pending")
    monkeypatch.setattr(engine, "job_status", lambda job_id: (_ for _ in ()).throw(engine.EngineError("HTTP 500")))
    jobs.check_jobs()  # must not raise
    with get_postgres_session() as s:
        row = s.get(Item, item_id)
        assert row is not None
        assert row.job_status == "pending"
