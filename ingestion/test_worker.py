import os
import uuid

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")
os.environ.setdefault("INGESTION_OPENROUTER_API_KEY", "test-key-not-used")

import pytest
from sqlalchemy import text as sqltext

import worker
from db import get_postgres_session
from knowledge import MergeResult
from models import IngestJob, JobStatus, KnowledgeBase, KnowledgeBaseConfig
from relevance import RelevanceResult


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as s:
            s.execute(sqltext("SELECT 1"))
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.fixture
def org_with_config():  # type: ignore[no-untyped-def]
    with get_postgres_session() as s:
        knowledge_base = KnowledgeBase(name=f"worker-test-{uuid.uuid4()}")
        s.add(knowledge_base)
        s.flush()
        knowledge_base_id = str(knowledge_base.id)
        s.add(
            KnowledgeBaseConfig(
                knowledge_base_id=knowledge_base.id,
                interests="anything",
                discover_types=True,
                entity_types=[{"name": "Person", "description": ""}],
                relationship_types=[{"name": "KNOWS", "description": ""}],
            )
        )
        s.commit()
    yield knowledge_base_id
    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM ingest_jobs WHERE knowledge_base_id = :o"), {"o": knowledge_base_id})
        s.execute(sqltext("DELETE FROM knowledge_base_configs WHERE knowledge_base_id = :o"), {"o": knowledge_base_id})
        s.execute(sqltext("DELETE FROM knowledge_bases WHERE id = :o"), {"o": knowledge_base_id})
        s.commit()


def _enqueue(knowledge_base_id: str, text: str) -> str:
    with get_postgres_session() as s:
        job = IngestJob(knowledge_base_id=knowledge_base_id, content=text)
        s.add(job)
        s.flush()
        job_id = str(job.id)
        s.commit()
    return job_id


def _status(job_id: str) -> str:
    with get_postgres_session() as s:
        job = s.get(IngestJob, job_id)
        assert job is not None
        return job.status


@requires_pg
def test_relevant_job_reaches_done(monkeypatch: pytest.MonkeyPatch, org_with_config) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(worker, "judge_relevance", lambda *a, **k: RelevanceResult(relevant=True, reason="ok"))
    monkeypatch.setattr(
        worker,
        "merge_content",
        lambda *a, **k: MergeResult(entities_created=0, entities_merged=0, relationships_created=0),
    )
    job_id = _enqueue(org_with_config, "content")
    worker.process_job(job_id)
    assert _status(job_id) == "done"


@requires_pg
def test_new_relationship_type_is_persisted_to_config(monkeypatch: pytest.MonkeyPatch, org_with_config) -> None:  # type: ignore[no-untyped-def]
    # A discover_types run that mints a new relationship type must grow the knowledge base's
    # own vocabulary, not just the in-memory result — otherwise the next job never sees it.
    monkeypatch.setattr(worker, "judge_relevance", lambda *a, **k: RelevanceResult(relevant=True, reason="ok"))
    monkeypatch.setattr(
        worker,
        "merge_content",
        lambda *a, **k: MergeResult(
            entities_created=0,
            entities_merged=0,
            relationships_created=1,
            new_relationship_types=[{"name": "Endorses", "description": "backs"}],
        ),
    )
    job_id = _enqueue(org_with_config, "content")
    worker.process_job(job_id)
    assert _status(job_id) == "done"
    with get_postgres_session() as s:
        cfg = s.query(KnowledgeBaseConfig).filter(KnowledgeBaseConfig.knowledge_base_id == org_with_config).one()
        names = {t["name"] for t in cfg.relationship_types}
        assert "Endorses" in names
        # Pre-existing type is preserved, not clobbered.
        assert "KNOWS" in names


@requires_pg
def test_irrelevant_job_is_skipped(monkeypatch: pytest.MonkeyPatch, org_with_config) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(worker, "judge_relevance", lambda *a, **k: RelevanceResult(relevant=False, reason="nope"))
    job_id = _enqueue(org_with_config, "content")
    worker.process_job(job_id)
    assert _status(job_id) == "skipped"
    with get_postgres_session() as s:
        assert s.get(IngestJob, job_id).relevance_reason == "nope"  # type: ignore[union-attr]


@requires_pg
def test_extraction_error_fails_after_max_attempts(monkeypatch: pytest.MonkeyPatch, org_with_config) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(worker, "judge_relevance", lambda *a, **k: RelevanceResult(relevant=True, reason="ok"))

    def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("extraction blew up")

    monkeypatch.setattr(worker, "merge_content", _boom)
    monkeypatch.setattr(worker.config, "WORKER_MAX_ATTEMPTS", 1)
    job_id = _enqueue(org_with_config, "content")
    worker.process_job(job_id)
    assert _status(job_id) == "failed"
    with get_postgres_session() as s:
        job = s.get(IngestJob, job_id)
        assert job is not None
        assert job.attempts == 1
        assert "extraction blew up" in (job.error or "")


@requires_pg
def test_relevance_failure_retries_not_skips(monkeypatch: pytest.MonkeyPatch, org_with_config) -> None:  # type: ignore[no-untyped-def]
    # A relevance check that can't complete must NOT be recorded as skipped (which would
    # silently drop the content); it goes back to pending for retry (attempts under cap).
    from relevance import RelevanceError

    def _boom(*a: object, **k: object) -> None:
        raise RelevanceError("relevance check failed: empty LLM response")

    monkeypatch.setattr(worker, "judge_relevance", _boom)
    monkeypatch.setattr(worker.config, "WORKER_MAX_ATTEMPTS", 3)
    job_id = _enqueue(org_with_config, "content")
    worker.process_job(job_id)
    assert _status(job_id) == "pending"
    with get_postgres_session() as s:
        job = s.get(IngestJob, job_id)
        assert job is not None and job.attempts == 1


def test_main_survives_run_once_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A raising run_once() (e.g. a DB blip during claim or job-load) must not kill main()'s
    # poll loop. Force run_once() to raise, and make time.sleep() raise a sentinel so main()
    # completes exactly one guarded iteration and exits via the sentinel instead of hanging.
    def _boom() -> int:
        raise RuntimeError("db exploded")

    def _sleep_sentinel(_seconds: float) -> None:
        raise StopIteration

    monkeypatch.setattr(worker, "run_once", _boom)
    monkeypatch.setattr(worker.time, "sleep", _sleep_sentinel)
    with pytest.raises(StopIteration):
        worker.main()


def test_drain_jobs_flow_returns_the_processed_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flow wraps one drain pass. Prefect's `serve` schedules it on an interval;
    the FOR UPDATE SKIP LOCKED claim in run_once is what makes overlapping runs safe,
    so the flow needs no locking of its own."""
    monkeypatch.setattr(worker, "run_once", lambda: 3)
    monkeypatch.setattr(worker, "bootstrap_schema", lambda: None)
    assert worker.drain_jobs() == {"processed": 3}


def test_drain_jobs_flow_bootstraps_the_neo4j_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() bootstrapped the schema before looping; the flow must too, or a fresh
    cluster's first run writes entities with no fulltext index behind them."""
    calls: list[str] = []
    monkeypatch.setattr(worker, "run_once", lambda: 0)
    monkeypatch.setattr(worker, "bootstrap_schema", lambda: calls.append("bootstrap"))
    worker.drain_jobs()
    assert calls == ["bootstrap"]


@requires_pg
def test_skip_locked_prevents_double_claim(org_with_config) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    _enqueue(org_with_config, "a")
    _enqueue(org_with_config, "b")
    sa = get_postgres_session()
    sb = get_postgres_session()
    try:
        stmt = (
            select(IngestJob.id)
            .where(IngestJob.status == JobStatus.PENDING.value, IngestJob.knowledge_base_id == org_with_config)
            .order_by(IngestJob.created_at)
            .with_for_update(skip_locked=True)
        )
        a_ids = {str(x) for x in sa.execute(stmt).scalars().all()}  # locks both rows
        b_ids = {str(x) for x in sb.execute(stmt).scalars().all()}  # sees none of A's locked rows
        assert a_ids and not (a_ids & b_ids)
    finally:
        sa.rollback()
        sb.rollback()
        sa.close()
        sb.close()
