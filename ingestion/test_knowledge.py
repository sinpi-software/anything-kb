import os
import uuid

import pytest
from sqlalchemy import text as sqlalchemy_text

# These tests need the docker Neo4j. Skip if the driver can't connect.
neo4j = pytest.importorskip("neo4j")

os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")
os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")
os.environ.setdefault("INGESTION_OPENROUTER_API_KEY", "test-key-not-used")

import knowledge as knowledge_mod  # noqa: E402
from db import get_postgres_session  # noqa: E402
from knowledge import (  # noqa: E402
    ExtractedEntity,
    KnowledgeExtraction,
    build_extraction_messages,
    candidate_query,
    normalize_name,
    upsert_entity,
    write_provenance,
    write_relationship,
)
from models import Artifact, Org, Transformation, TransformationType  # noqa: E402
from neo4j_client import bootstrap_schema, get_neo4j_session  # noqa: E402


def _neo4j_available() -> bool:
    try:
        with get_neo4j_session() as session:
            session.run("RETURN 1").consume()
        return True
    except Exception:
        return False


requires_neo4j = pytest.mark.skipif(not _neo4j_available(), reason="Neo4j not reachable")


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as session:
            session.execute(sqlalchemy_text("SELECT 1"))
        return True
    except Exception:
        return False


requires_neo4j_and_postgres = pytest.mark.skipif(
    not (_neo4j_available() and _postgres_available()), reason="Neo4j and/or Postgres not reachable"
)


@requires_neo4j
def test_bootstrap_is_idempotent() -> None:
    bootstrap_schema()
    bootstrap_schema()  # second call must not raise
    with get_neo4j_session() as session:
        names = {r["name"] for r in session.run("SHOW CONSTRAINTS YIELD name RETURN name")}
        assert "entity_id" in names


def test_extraction_parses_from_json() -> None:
    payload = (
        '{"entities": [{"name": "Ada Lovelace", "type": "Person", "description": "Mathematician", '
        '"aliases": ["Ada"]}], "relationships": [{"source_name": "Ada Lovelace", '
        '"target_name": "Analytical Engine", "type": "WORKED_ON"}]}'
    )
    result = KnowledgeExtraction.model_validate_json(payload)
    assert result.entities[0] == ExtractedEntity(
        name="Ada Lovelace", type="Person", description="Mathematician", aliases=["Ada"]
    )
    assert result.relationships[0].type == "WORKED_ON"


def test_extraction_defaults_aliases_empty() -> None:
    e = ExtractedEntity(name="X", type="Thing", description="d")
    assert e.aliases == []


def test_build_extraction_messages_includes_entity_types_and_text() -> None:
    msgs = build_extraction_messages("Find entities.", ["Person", "Place"], "Some article")
    joined = " ".join(m["content"] for m in msgs)
    assert "Person" in joined and "Place" in joined
    assert "Some article" in joined
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"


def test_normalize_name() -> None:
    assert normalize_name("  Barack   Obama ") == "barack obama"


def test_candidate_query_is_org_and_type_scoped() -> None:
    query, params = candidate_query("org-1", "Person", "ada lovelace", 5)
    assert "org_id" in query
    assert params["org_id"] == "org-1"
    assert params["type"] == "Person"
    assert params["name_normalized"] == "ada lovelace"
    assert params["limit"] == 5


def _cleanup(org_id: str) -> None:
    with get_neo4j_session() as session:
        session.run("MATCH (n) WHERE n.org_id = $org_id DETACH DELETE n", {"org_id": org_id})


@requires_neo4j
def test_upsert_and_relationship_roundtrip() -> None:
    bootstrap_schema()
    org = f"test-{uuid.uuid4()}"
    a_id, b_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        with get_neo4j_session() as session:
            upsert_entity(session, org, a_id, ExtractedEntity(name="Ada", type="Person", description="d"), "sum A")
            upsert_entity(session, org, b_id, ExtractedEntity(name="Engine", type="Thing", description="d"), "sum B")
            # Re-upsert A with a new summary — must update, not duplicate.
            upsert_entity(session, org, a_id, ExtractedEntity(name="Ada", type="Person", description="d"), "sum A v2")
            write_relationship(session, org, a_id, b_id, "WORKED_ON", "art-1")
            write_provenance(session, org, a_id, "art-1")

            count = session.run("MATCH (e:Entity {org_id: $o}) RETURN count(e) AS c", {"o": org}).single(True)["c"]
            assert count == 2  # no duplicate
            summ = session.run("MATCH (e:Entity {id: $id}) RETURN e.summary AS s", {"id": a_id}).single(True)["s"]
            assert summ == "sum A v2"
            rels = session.run(
                "MATCH (:Entity {org_id: $o})-[r:RELATED]->() RETURN r.type AS t", {"o": org}
            ).single(True)["t"]
            assert rels == "WORKED_ON"
    finally:
        _cleanup(org)


@requires_neo4j
def test_org_isolation() -> None:
    bootstrap_schema()
    org_a, org_b = f"a-{uuid.uuid4()}", f"b-{uuid.uuid4()}"
    try:
        with get_neo4j_session() as session:
            ada = ExtractedEntity(name="Ada", type="Person", description="d")
            upsert_entity(session, org_a, str(uuid.uuid4()), ada, "A")
            upsert_entity(session, org_b, str(uuid.uuid4()), ada, "B")
            a_count = session.run(
                "MATCH (e:Entity {org_id: $o}) RETURN count(e) AS c", {"o": org_a}
            ).single(True)["c"]
            assert a_count == 1  # org_b's identically-named entity is invisible to org_a
    finally:
        _cleanup(org_a)
        _cleanup(org_b)


class _NullClient:
    """Stands in for OpenRouter. `chat.send` must never be reached because every
    LLM-backed function (extract_knowledge, resolve_entity, merge_summary) is monkeypatched
    below, so this client never needs to actually send anything."""

    def __enter__(self) -> "_NullClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


@requires_neo4j_and_postgres
def test_run_knowledge_writes_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a throwaway Org/Artifact/Transformation in Postgres, run_knowledge_transform
    with the LLM fully stubbed, then assert the resulting graph state in Neo4j and the
    persisted output Artifact. Both stores are cleaned up in the finally block."""
    bootstrap_schema()
    extraction = KnowledgeExtraction(
        entities=[
            ExtractedEntity(name="Ada", type="Person", description="Mathematician"),
            ExtractedEntity(name="Engine", type="Thing", description="A machine"),
        ],
        relationships=[knowledge_mod.ExtractedRelationship(source_name="Ada", target_name="Engine", type="WORKED_ON")],
    )
    # Stub every OpenRouter-backed step so no real LLM call happens.
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    monkeypatch.setattr(knowledge_mod, "resolve_entity", lambda *a, **k: None)  # every entity resolves as new
    monkeypatch.setattr(knowledge_mod, "merge_summary", lambda *a, **k: "merged")
    monkeypatch.setattr(knowledge_mod, "OpenRouter", lambda *a, **k: _NullClient())

    org_id: str | None = None
    artifact_id: str | None = None
    transformation_id: str | None = None
    output_artifact_id: str | None = None
    org_id_str: str | None = None
    try:
        with get_postgres_session() as session:
            org = Org(name=f"e2e-knowledge-{uuid.uuid4()}")
            session.add(org)
            session.flush()
            org_id = org.id
            org_id_str = str(org_id)

            artifact = Artifact(
                org_id=org_id,
                ref_table_name=Artifact.__tablename__,
                ref_table_id=uuid.uuid4(),
                type="text/markdown",
                data="Ada worked on the Engine.",
            )
            session.add(artifact)
            session.flush()
            artifact_id = str(artifact.id)

            transformation = Transformation(
                org_id=org_id,
                type=TransformationType.KNOWLEDGE.value,
                model="test/model",
                prompt="Extract entities.",
                params={"entity_types": ["Person", "Thing"]},
            )
            session.add(transformation)
            session.flush()
            transformation_id = str(transformation.id)
            session.commit()

        output_artifact_id = knowledge_mod.run_knowledge_transform(artifact_id, transformation_id)

        with get_neo4j_session() as neo:
            entity_count = neo.run(
                "MATCH (e:Entity {org_id: $o}) RETURN count(e) AS c", {"o": org_id_str}
            ).single(True)["c"]
            assert entity_count == 2
            rel_count = neo.run(
                "MATCH (:Entity {org_id: $o})-[r:RELATED]->() RETURN count(r) AS c", {"o": org_id_str}
            ).single(True)["c"]
            assert rel_count == 1

        with get_postgres_session() as session:
            output = session.get(Artifact, output_artifact_id)
            assert output is not None
            payload = knowledge_mod.KnowledgeTransformOutput.model_validate_json(output.data)
            assert payload.entities_created == 2
            assert payload.entities_merged == 0
            assert payload.relationships_created == 1
            assert payload.source_artifact_id == artifact_id
    finally:
        if org_id_str is not None:
            _cleanup(org_id_str)
        with get_postgres_session() as session:
            for model, ident in (
                (Artifact, output_artifact_id),
                (Artifact, artifact_id),
                (Transformation, transformation_id),
                (Org, org_id),
            ):
                if ident is None:
                    continue
                row = session.get(model, ident)
                if row is not None:
                    session.delete(row)
            session.commit()
