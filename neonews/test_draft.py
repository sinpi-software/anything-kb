import logging
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from sqlalchemy import text

import draft
from cluster import Cluster
from db import get_postgres_session
from models import Issue, JobState
from write import Story


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


def _source(sid: str, entity_ids: list[str]) -> dict[str, Any]:
    return {
        "id": sid,
        "label": f"Source {sid}",
        "publishedAt": "2026-07-22T00:00:00",
        "ingestedAt": "2026-07-22T00:00:00",
        "entities": [{"id": e, "name": e, "type": "Thing", "summary": "s", "article": "a"} for e in entity_ids],
    }


@pytest.fixture(autouse=True)
def _isolated_watermark() -> Generator[None, None, None]:
    """Each test needs a clean starting watermark. The suite runs against its own
    database (NEONEWS_TEST_POSTGRES_URL), so there's no real operator watermark to
    destroy here — but save-and-restore rather than an unconditional delete costs
    nothing and keeps this fixture correct even if it's ever pointed at a database
    that isn't purely throwaway."""
    with get_postgres_session() as s:
        existing = s.get(JobState, config.DRAFT_WATERMARK_KEY)
        saved_ran_at = existing.ran_at if existing is not None else None
        if existing is not None:
            s.delete(existing)
            s.commit()
    yield
    with get_postgres_session() as s:
        current = s.get(JobState, config.DRAFT_WATERMARK_KEY)
        if saved_ran_at is None:
            if current is not None:
                s.delete(current)
        elif current is None:
            s.add(JobState(key=config.DRAFT_WATERMARK_KEY, ran_at=saved_ran_at))
        else:
            current.ran_at = saved_ran_at
        s.commit()


@pytest.fixture
def output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(draft.config, "OUTPUT_DIR", str(tmp_path))
    return tmp_path


def test_first_run_uses_the_default_lookback(monkeypatch: pytest.MonkeyPatch, output_dir: Path) -> None:
    seen: dict[str, str] = {}

    def fake_recent_sources(since: str, limit: int) -> list[Any]:
        seen["since"] = since
        return []

    monkeypatch.setattr(draft.engine, "recent_sources", fake_recent_sources)
    draft.draft_issue()
    since = datetime.fromisoformat(seen["since"])
    expected = datetime.now(UTC) - timedelta(hours=config.DEFAULT_LOOKBACK_HOURS)
    assert abs((since - expected).total_seconds()) < 120


def test_writes_an_issue_file_and_records_it(monkeypatch: pytest.MonkeyPatch, output_dir: Path) -> None:
    monkeypatch.setattr(draft.engine, "recent_sources", lambda since, limit: [_source("1", ["ada"])])
    monkeypatch.setattr(draft, "_llm_client", lambda: object())
    monkeypatch.setattr(draft, "write_story", lambda client, beat, cluster: Story(headline="H", body="B"))
    result = draft.draft_issue()
    written = list(output_dir.glob("*.md"))
    assert len(written) == 1
    assert "## H" in written[0].read_text()
    assert result["stories"] == 1
    with get_postgres_session() as s:
        assert s.query(Issue).count() >= 1


def test_advances_the_watermark_only_after_the_file_is_written(
    monkeypatch: pytest.MonkeyPatch, output_dir: Path
) -> None:
    """A crash mid-draft must re-cover the window. A duplicated issue is recoverable
    by hand; a silently skipped window is not."""
    monkeypatch.setattr(draft.engine, "recent_sources", lambda since, limit: [_source("1", ["ada"])])
    monkeypatch.setattr(draft, "_llm_client", lambda: object())
    monkeypatch.setattr(draft, "write_story", lambda client, beat, cluster: Story(headline="H", body="B"))
    monkeypatch.setattr(draft, "assemble_issue", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        draft.draft_issue()
    with get_postgres_session() as s:
        assert s.get(JobState, config.DRAFT_WATERMARK_KEY) is None


def test_a_failing_cluster_is_dropped_and_the_issue_still_ships(
    monkeypatch: pytest.MonkeyPatch, output_dir: Path
) -> None:
    monkeypatch.setattr(
        draft.engine, "recent_sources", lambda since, limit: [_source("1", ["ada"]), _source("2", ["zeta"])]
    )
    monkeypatch.setattr(draft, "_llm_client", lambda: object())

    def flaky(client: Any, beat: str, cluster: Cluster) -> Story:
        if any(e["id"] == "zeta" for e in cluster.entities):
            raise RuntimeError("model unavailable")
        return Story(headline="H", body="B")

    monkeypatch.setattr(draft, "write_story", flaky)
    result = draft.draft_issue()
    assert result["stories"] == 1
    assert result["clusters"] == 2


def test_an_empty_window_still_writes_an_issue_and_advances(monkeypatch: pytest.MonkeyPatch, output_dir: Path) -> None:
    monkeypatch.setattr(draft.engine, "recent_sources", lambda since, limit: [])
    result = draft.draft_issue()
    assert result["stories"] == 0
    with get_postgres_session() as s:
        assert s.get(JobState, config.DRAFT_WATERMARK_KEY) is not None


def test_all_clusters_failing_does_not_advance_the_watermark(monkeypatch: pytest.MonkeyPatch, output_dir: Path) -> None:
    """A non-empty window where every cluster's LLM call failed is not the same as a
    genuinely empty window. Conflating them (write "No new stories", advance anyway)
    would silently and permanently lose those sources — a duplicated issue is
    recoverable by hand, a silently skipped window is not."""
    monkeypatch.setattr(
        draft.engine, "recent_sources", lambda since, limit: [_source("1", ["ada"]), _source("2", ["zeta"])]
    )
    monkeypatch.setattr(draft, "_llm_client", lambda: object())
    monkeypatch.setattr(
        draft, "write_story", lambda client, beat, cluster: (_ for _ in ()).throw(RuntimeError("model down"))
    )
    with pytest.raises(RuntimeError):
        draft.draft_issue()
    assert list(output_dir.glob("*.md")) == []
    with get_postgres_session() as s:
        assert s.get(JobState, config.DRAFT_WATERMARK_KEY) is None


def test_truncated_window_advances_to_run_start_and_reports_and_logs_loudly(
    monkeypatch: pytest.MonkeyPatch, output_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The engine clamps `sources(since:)` at SOURCES_QUERY_LIMIT, newest-first, and
    has no `until`/ascending-order parameter to page through the rest. Advancing only
    to the oldest source seen (the prior behaviour) doesn't recover that remainder —
    it lies *before* that oldest-seen timestamp, i.e. still out of range next run — and
    it re-drafts the sources that DID fit into the very next window, burning a second
    round of LLM spend on them. So truncation advances to run_start exactly like the
    untruncated path, but must say so loudly: an ERROR log naming the uncovered window,
    and `truncated: True` in the return dict."""
    monkeypatch.setattr(draft.config, "SOURCES_QUERY_LIMIT", 2)
    rows = [
        _source("1", ["ada"]) | {"ingestedAt": "2026-07-21T00:00:00+00:00"},
        _source("2", ["zeta"]) | {"ingestedAt": "2026-07-20T00:00:00+00:00"},
    ]
    monkeypatch.setattr(draft.engine, "recent_sources", lambda since, limit: rows)
    monkeypatch.setattr(draft, "_llm_client", lambda: object())
    monkeypatch.setattr(draft, "write_story", lambda client, beat, cluster: Story(headline="H", body="B"))
    with caplog.at_level(logging.ERROR):
        result = draft.draft_issue()
    assert result["truncated"] is True
    assert any("TRUNCATED" in record.message for record in caplog.records)
    with get_postgres_session() as s:
        state = s.get(JobState, config.DRAFT_WATERMARK_KEY)
        assert state is not None
        assert abs((state.ran_at - datetime.now(UTC)).total_seconds()) < 120


def test_untruncated_window_reports_not_truncated(monkeypatch: pytest.MonkeyPatch, output_dir: Path) -> None:
    monkeypatch.setattr(draft.engine, "recent_sources", lambda since, limit: [_source("1", ["ada"])])
    monkeypatch.setattr(draft, "_llm_client", lambda: object())
    monkeypatch.setattr(draft, "write_story", lambda client, beat, cluster: Story(headline="H", body="B"))
    result = draft.draft_issue()
    assert result["truncated"] is False


def test_issue_body_is_persisted_to_postgres(monkeypatch: pytest.MonkeyPatch, output_dir: Path) -> None:
    """A CronJob pod's filesystem dies with it, so the markdown must live in the row.
    Without this, a deployed run leaves an Issue row pointing at a path nothing can read."""
    monkeypatch.setattr(draft.engine, "recent_sources", lambda since, limit: [_source("1", ["ada"])])
    monkeypatch.setattr(draft, "_llm_client", lambda: object())
    monkeypatch.setattr(draft, "write_story", lambda client, beat, cluster: Story(headline="H", body="B"))

    result = draft.draft_issue()

    on_disk = Path(result["path"]).read_text()
    with get_postgres_session() as s:
        row = s.query(Issue).order_by(Issue.generated_at.desc()).first()
        assert row is not None
        assert row.body is not None
        assert "## H" in row.body
        # The durable copy and the dev-convenience file must not diverge.
        assert row.body == on_disk
        s.query(Issue).filter(Issue.id == row.id).delete()
        s.commit()


def test_sources_query_limit_never_exceeds_the_engines_ceiling() -> None:
    """The whole truncation detection (`len(rows) >= SOURCES_QUERY_LIMIT` in draft.py)
    rests on this constant sitting AT the engine's own hard clamp (NODES_MAX_LIMIT =
    500, ingestion/config.py's `min(max(limit, 1), NODES_MAX_LIMIT)`), never above it.
    If it ever exceeded 500, a run that actually hit the engine's clamp would return
    fewer rows than SOURCES_QUERY_LIMIT, `truncated` would come back False, and the
    loss would go silent again — the exact failure this constant's own assertion in
    config.py exists to prevent. Pin both here so a change to either can't drift
    unnoticed."""
    assert config.SOURCES_QUERY_LIMIT <= 500
