import os
import uuid

os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")
os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")
os.environ.setdefault("INGESTION_OPENROUTER_API_KEY", "test-key-not-used")

import pytest
from sqlalchemy import text as sqltext

from backfill_articles import backfill_entity_articles, backfill_source_labels
from db import get_postgres_session
from knowledge import ExtractedEntity, upsert_entity, write_provenance
from models import IngestJob, KnowledgeBase
from neo4j_client import bootstrap_schema, get_neo4j_session


def _neo4j_available() -> bool:
    try:
        with get_neo4j_session() as s:
            s.run("RETURN 1").consume()
        return True
    except Exception:
        return False


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as s:
            s.execute(sqltext("SELECT 1"))
        return True
    except Exception:
        return False


requires_stack = pytest.mark.skipif(
    not (_neo4j_available() and _postgres_available()), reason="Neo4j and/or Postgres not reachable"
)


@requires_stack
def test_backfill_promotes_summary_to_article_and_labels_source() -> None:
    bootstrap_schema()
    knowledge_base_id = f"kb-{uuid.uuid4()}"
    entity_id = str(uuid.uuid4())
    old_summary = "Ada was a mathematician. She wrote the first algorithm."
    pg_knowledge_base_id: str | None = None
    job_id: str | None = None

    try:
        # Entity with only a summary (no article) — the pre-migration shape.
        with get_neo4j_session() as s:
            upsert_entity(
                s,
                knowledge_base_id,
                entity_id,
                ExtractedEntity(name="Ada", type="Person", description="d"),
                old_summary,
                "",
            )
            s.run("MATCH (e:Entity {id: $id}) SET e.article = NULL", {"id": entity_id})

        # A real ingest_jobs row with metadata.source, so the Source can be resolved by job_id.
        with get_postgres_session() as s:
            knowledge_base = KnowledgeBase(name=f"backfill-test-{uuid.uuid4()}")
            s.add(knowledge_base)
            s.flush()
            pg_knowledge_base_id = str(knowledge_base.id)
            job = IngestJob(knowledge_base_id=pg_knowledge_base_id, content="x", job_metadata={"source": "x"})
            s.add(job)
            s.flush()
            job_id = str(job.id)
            s.commit()

        # Source with a job_id but no label/date — the pre-migration shape.
        with get_neo4j_session() as s:
            write_provenance(s, knowledge_base_id, entity_id, job_id)
            s.run("MATCH (s:Source {job_id: $job_id}) SET s.label = NULL", {"job_id": job_id})

        entities_backfilled = backfill_entity_articles()
        sources_labeled = backfill_source_labels()
        assert entities_backfilled >= 1
        assert sources_labeled >= 1

        with get_neo4j_session() as s:
            entity_row = s.run(
                "MATCH (e:Entity {id: $id}) RETURN e.article AS article, e.summary AS summary", {"id": entity_id}
            ).single()
            source_row = s.run(
                "MATCH (s:Source {job_id: $job_id}) RETURN s.label AS label, s.date AS date", {"job_id": job_id}
            ).single()

        assert entity_row is not None
        assert entity_row["article"] == old_summary
        assert entity_row["summary"] == "Ada was a mathematician."

        assert source_row is not None
        assert source_row["label"] == "x"
        assert source_row["date"]
    finally:
        with get_neo4j_session() as s:
            s.run("MATCH (n) WHERE n.knowledge_base_id = $kb DETACH DELETE n", {"kb": knowledge_base_id})
        with get_postgres_session() as s:
            if job_id is not None:
                s.execute(sqltext("DELETE FROM ingest_jobs WHERE id = :id"), {"id": job_id})
            if pg_knowledge_base_id is not None:
                s.execute(sqltext("DELETE FROM knowledge_bases WHERE id = :id"), {"id": pg_knowledge_base_id})
            s.commit()
