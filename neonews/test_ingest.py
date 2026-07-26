import logging
import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import config

# Point at the test suite's own database (config.POSTGRES_TEST_URL_ENV / _DEFAULT), by
# assignment rather than setdefault: the `import config` just above already ran its
# repo-root .env loading, and .env.sample sets NEONEWS_POSTGRES_URL to the operator's
# live database. Plain assignment (not setdefault) unconditionally overwrites whatever
# dotenv set, so the test suite can never end up pointed at the live database — the
# semantics of `=` vs `setdefault` are what protect this, not import order.
os.environ["NEONEWS_POSTGRES_URL"] = os.environ.get(config.POSTGRES_TEST_URL_ENV, config.POSTGRES_TEST_URL_DEFAULT)

import pytest
from sqlalchemy import delete, func, select, text

import engine
import ingest
from db import get_postgres_session
from models import Item, Source


def _require_test_postgres() -> None:
    """The Postgres-backed tests in this module need neonews_test — the two
    extract_text tests below don't touch the database and are unaffected either way.
    A missing/unmigrated database must FAIL the suite, not silently skip it — a skip
    here means the one-time setup documented in README.md was never done, not that
    the environment legitimately lacks Postgres. Reported as a clean pass, that's
    exactly the failure mode this project has fought all the way through."""
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
def item() -> Generator[str, None, None]:
    """Each invocation gets its own throwaway Source (a random locator, so it can
    never collide with another test's). Teardown deletes every Item under that
    source_id, then the source itself — which is exactly "the rows this test
    created": nothing else can share that source_id, including a sibling item a
    test adds under it (see the two flow tests below). Without this, `test_ingest.py`
    would leave a permanently-pending row (job_id IS NULL, attempts < cap) behind on
    every run, forever growing the backlog the `ingest_items()` sweep has to wade
    through."""
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
        item_id = str(row.id)
        source_id = str(source.id)
    yield item_id
    with get_postgres_session() as s:
        s.execute(delete(Item).where(Item.source_id == source_id))
        s.execute(delete(Source).where(Source.id == source_id))
        s.commit()


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
    # The engine dates its Source nodes from published_at in job metadata, and reads
    # the label from the `source` key (worker.py: `meta.get("source", "")`) — not
    # `label`, which the engine never looks at.
    assert seen["metadata"]["published_at"].startswith("2026-07-22T00:00:00")
    assert seen["metadata"]["url"] == "https://example.com/a"
    assert seen["metadata"]["source"] == "A headline"


def test_submit_failure_bumps_attempts_and_records_the_error(
    monkeypatch: pytest.MonkeyPatch, item: str, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        ingest.engine, "post_content", lambda t, m: (_ for _ in ()).throw(engine.EngineError("HTTP 503"))
    )
    with get_postgres_session() as s:
        row = s.get(Item, item)
        assert row is not None
        row.full_text = "Body."
        with caplog.at_level(logging.WARNING):
            assert ingest.submit(s, row) is False
        s.commit()
        refreshed = s.get(Item, item)
        assert refreshed is not None
        assert refreshed.attempts == 1
        assert refreshed.job_id is None
        assert refreshed.error is not None
        assert "503" in refreshed.error
    # A failed submission must stay visible in the logs, not just in the DB row.
    assert any("503" in record.message for record in caplog.records)


def test_ingest_flow_skips_items_over_the_attempt_cap(monkeypatch: pytest.MonkeyPatch, item: str) -> None:
    """The cap is the guard against re-driving a broken item forever — the failure
    mode that, in the prior codebase, drained the account.

    The table isn't reset between tests, so unrelated pending rows left by earlier
    tests may also be swept here. The fail-trap is scoped to this test's own item
    (by a label unique to its id) so those unrelated rows pass through harmlessly
    instead of tripping it; the DB-state assertions below are the real proof that
    *this* item was never touched.

    A sibling item — same batch, but eligible — is a positive control: it proves
    the sweep actually reached this batch at all. `INGEST_BATCH_SIZE` rows are swept
    oldest-first, and without this control, a large enough backlog of permanently-
    pending rows (from other tests, or other runs of this file) could push both this
    item and its sibling out of the batch entirely — at which point the assertions
    below would hold vacuously, whether or not the cap is actually enforced.
    """
    monkeypatch.setattr(ingest, "extract_text", lambda url: "Body.")
    label = f"cap-test-{item}"
    sibling_label = f"cap-test-sibling-{item}"

    def fake_post(text_: str, metadata: dict[str, Any]) -> str:
        if metadata.get("source") == label:
            pytest.fail("should not submit: item is over the attempt cap")
        if metadata.get("source") == sibling_label:
            return "job-sibling"  # the positive control: this row IS owned by this test
        # Any other row is a genuine pending item this test does not own. Stamping it
        # with a fabricated job_id here would orphan it permanently (job_id IS NOT NULL
        # skips it from `ingest`, and check-jobs 404s polling a non-UUID forever) — so
        # fail loudly instead of quietly resolving someone else's row.
        pytest.fail(f"fake_post called for a row this test does not own: {metadata}")

    monkeypatch.setattr(ingest.engine, "post_content", fake_post)
    with get_postgres_session() as s:
        row = s.get(Item, item)
        assert row is not None
        row.title = label
        row.attempts = config.MAX_SUBMIT_ATTEMPTS
        # Shares this test's dedicated source_id, so the `item` fixture's teardown
        # deletes it too — no separate cleanup needed here.
        sibling = Item(
            source_id=row.source_id,
            dedup_key=os.urandom(4).hex(),
            url="https://example.com/sibling",
            title=f"cap-test-sibling-{item}",
            content="Sibling body.",
        )
        s.add(sibling)
        s.commit()
        sibling_id = str(sibling.id)

    ingest.ingest_items()

    with get_postgres_session() as s:
        refreshed = s.get(Item, item)
        assert refreshed is not None
        assert refreshed.job_id is None
        assert refreshed.attempts == config.MAX_SUBMIT_ATTEMPTS  # untouched: never even attempted

        sibling_refreshed = s.get(Item, sibling_id)
        assert sibling_refreshed is not None
        assert sibling_refreshed.job_id is not None  # positive control: the sweep did reach this batch


def test_ingest_flow_leaves_already_submitted_items_alone(monkeypatch: pytest.MonkeyPatch, item: str) -> None:
    """Proves the `job_id IS NULL` half of the sweep: an item that already has a
    job_id is never re-submitted. See the cap test above for why the fail-trap is
    scoped to this test's own item rather than any call at all, and for why the
    sibling positive control is needed to rule out batch-size starvation."""
    monkeypatch.setattr(ingest, "extract_text", lambda url: "Body.")
    label = f"submitted-test-{item}"
    sibling_label = f"submitted-test-sibling-{item}"

    def fake_post(text_: str, metadata: dict[str, Any]) -> str:
        if metadata.get("source") == label:
            pytest.fail("should not resubmit: item already has a job_id")
        if metadata.get("source") == sibling_label:
            return "job-sibling"  # the positive control: this row IS owned by this test
        pytest.fail(f"fake_post called for a row this test does not own: {metadata}")

    monkeypatch.setattr(ingest.engine, "post_content", fake_post)
    with get_postgres_session() as s:
        row = s.get(Item, item)
        assert row is not None
        row.title = label
        row.job_id = "job-existing"
        sibling = Item(
            source_id=row.source_id,
            dedup_key=os.urandom(4).hex(),
            url="https://example.com/sibling",
            title=f"submitted-test-sibling-{item}",
            content="Sibling body.",
        )
        s.add(sibling)
        s.commit()
        sibling_id = str(sibling.id)

    ingest.ingest_items()

    with get_postgres_session() as s:
        refreshed = s.get(Item, item)
        assert refreshed is not None
        assert refreshed.job_id == "job-existing"  # unchanged

        sibling_refreshed = s.get(Item, sibling_id)
        assert sibling_refreshed is not None
        assert sibling_refreshed.job_id is not None  # positive control: the sweep did reach this batch


def test_extract_text_returns_none_on_a_blocked_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import net_guard

    monkeypatch.setattr(
        ingest.net_guard, "fetch", lambda url, **kw: (_ for _ in ()).throw(net_guard.BlockedURLError("nope"))
    )
    assert ingest.extract_text("http://169.254.169.254/") is None


def test_extract_text_logs_the_failure(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """An egress change that breaks every fetch must stay visible in the logs, not
    silently fall back to feed content with no trace."""
    monkeypatch.setattr(
        ingest.net_guard, "fetch", lambda url, **kw: (_ for _ in ()).throw(RuntimeError("fetch exploded"))
    )
    with caplog.at_level(logging.WARNING):
        assert ingest.extract_text("https://example.com/x") is None
    assert any("fetch exploded" in record.message for record in caplog.records)


def test_ingest_flow_counts_dead_lettered_items(monkeypatch: pytest.MonkeyPatch, item: str) -> None:
    """Dead-lettered items fall out of the sweep's WHERE clause entirely, so without a
    separate count a pipeline with hundreds of them reports identically to an idle,
    healthy one — indistinguishable from healthy. The count must be surfaced.

    This test's own item is pushed over the attempt cap below, so it is dead-lettered
    and never eligible for the batch — this test owns no row `ingest_items()` should
    touch. Both `extract_text` and `engine.post_content` are therefore trapped
    unconditionally, exactly as the fail-trap in the two adjacent flow tests: any call
    at all means a row this test does not own is being processed, which would mean a
    real `net_guard.fetch` and a real engine POST — spending tokens and writing junk
    into the live graph — instead of failing loudly.
    """

    def fake_extract_text(url: str) -> str | None:
        pytest.fail(f"extract_text should not be called: this test owns no eligible item (url={url!r})")

    def fake_post(text_: str, metadata: dict[str, Any]) -> str:
        pytest.fail(f"post_content should not be called: this test owns no eligible item (metadata={metadata!r})")

    monkeypatch.setattr(ingest, "extract_text", fake_extract_text)
    monkeypatch.setattr(ingest.engine, "post_content", fake_post)

    with get_postgres_session() as s:
        before = s.scalar(
            select(func.count())
            .select_from(Item)
            .where(Item.job_id.is_(None), Item.attempts >= config.MAX_SUBMIT_ATTEMPTS)
        ) or 0
        row = s.get(Item, item)
        assert row is not None
        row.attempts = config.MAX_SUBMIT_ATTEMPTS
        s.commit()

    result = ingest.ingest_items()

    assert result["dead_lettered"] >= before + 1
