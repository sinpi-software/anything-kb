import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

os.environ.setdefault("NEONEWS_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from sqlalchemy import text

import config
import draft
from cluster import Cluster
from db import get_postgres_session
from models import Issue, JobState
from write import Story


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as s:
            s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


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
    """Each test needs a clean starting watermark, but this table is shared with the
    real operator run: an autouse fixture that unconditionally deletes the row would
    destroy the real watermark the moment anyone ran the suite, silently skipping every
    source between the lost watermark and DEFAULT_LOOKBACK_HOURS ago on the next real
    run — the exact unrecoverable failure draft.py itself is designed to avoid. Save
    and restore instead of deleting outright."""
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


@requires_postgres
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


@requires_postgres
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


@requires_postgres
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


@requires_postgres
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


@requires_postgres
def test_an_empty_window_still_writes_an_issue_and_advances(
    monkeypatch: pytest.MonkeyPatch, output_dir: Path
) -> None:
    monkeypatch.setattr(draft.engine, "recent_sources", lambda since, limit: [])
    result = draft.draft_issue()
    assert result["stories"] == 0
    with get_postgres_session() as s:
        assert s.get(JobState, config.DRAFT_WATERMARK_KEY) is not None


@requires_postgres
def test_all_clusters_failing_does_not_advance_the_watermark(
    monkeypatch: pytest.MonkeyPatch, output_dir: Path
) -> None:
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


@requires_postgres
def test_truncated_window_advances_the_watermark_only_to_the_oldest_seen_source(
    monkeypatch: pytest.MonkeyPatch, output_dir: Path
) -> None:
    """The engine clamps `sources(since:)` at SOURCES_QUERY_LIMIT, newest first. If
    that clamp truncated the window, advancing to run_start (as the un-truncated case
    does) would skip everything older than what fit — advance only to the oldest
    source actually seen, so the remainder is picked up next run."""
    monkeypatch.setattr(draft.config, "SOURCES_QUERY_LIMIT", 2)
    rows = [
        _source("1", ["ada"]) | {"ingestedAt": "2026-07-21T00:00:00+00:00"},
        _source("2", ["zeta"]) | {"ingestedAt": "2026-07-20T00:00:00+00:00"},
    ]
    monkeypatch.setattr(draft.engine, "recent_sources", lambda since, limit: rows)
    monkeypatch.setattr(draft, "_llm_client", lambda: object())
    monkeypatch.setattr(draft, "write_story", lambda client, beat, cluster: Story(headline="H", body="B"))
    draft.draft_issue()
    with get_postgres_session() as s:
        state = s.get(JobState, config.DRAFT_WATERMARK_KEY)
        assert state is not None
        assert state.ran_at == datetime(2026, 7, 20, tzinfo=UTC)
