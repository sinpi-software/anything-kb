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
def _clean_watermark() -> Generator[None, None, None]:
    with get_postgres_session() as s:
        s.query(JobState).filter(JobState.key == config.DRAFT_WATERMARK_KEY).delete()
        s.commit()
    yield


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
