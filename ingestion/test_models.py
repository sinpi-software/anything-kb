from models import ApiKey, IngestJob, JobStatus, KnowledgeBaseConfig


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
