import os
import uuid

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from sqlalchemy import text as sqltext

from models import ApiKey, IngestJob, JobStatus, KnowledgeBase, KnowledgeBaseConfig


def _postgres_available() -> bool:
    try:
        from db import get_postgres_session

        with get_postgres_session() as s:
            s.execute(sqltext("SELECT 1"))
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


def test_job_status_values() -> None:
    assert {s.value for s in JobStatus} == {"pending", "processing", "done", "skipped", "failed"}


def test_ingest_job_columns() -> None:
    cols = IngestJob.__table__.columns
    assert "knowledge_base_id" in cols and "content" in cols
    # Attribute is job_metadata but the DB column is `metadata` (SQLAlchemy reserves .metadata).
    assert IngestJob.job_metadata.property.columns[0].name == "metadata"
    assert cols["status"].server_default is not None
    assert cols["attempts"].server_default is not None


def test_knowledge_base_config_is_unique_per_knowledge_base() -> None:
    uniques = [c for c in KnowledgeBaseConfig.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"]  # type: ignore[attr-defined]  # Table narrows to FromClause under mypy strict
    assert any({col.name for col in c.columns} == {"knowledge_base_id"} for c in uniques)


def test_api_key_hash_is_unique() -> None:
    assert ApiKey.__table__.columns["key_hash"].unique is True
    assert ApiKey.__table__.columns["revoked_at"].nullable is True


@requires_pg
def test_knowledge_base_config_discover_types_defaults_true() -> None:
    from db import get_postgres_session

    with get_postgres_session() as s:
        knowledge_base = KnowledgeBase(name=f"models-test-{uuid.uuid4()}")
        s.add(knowledge_base)
        s.flush()
        knowledge_base_id = knowledge_base.id
        config = KnowledgeBaseConfig(
            knowledge_base_id=knowledge_base_id,
            interests="x",
            entity_types=[],
            relationship_types=[],
        )
        s.add(config)
        s.flush()
        assert config.discover_types

        s.execute(
            sqltext("DELETE FROM knowledge_base_configs WHERE knowledge_base_id = :id"),
            {"id": knowledge_base_id},
        )
        s.execute(sqltext("DELETE FROM knowledge_bases WHERE id = :id"), {"id": knowledge_base_id})
        s.commit()
