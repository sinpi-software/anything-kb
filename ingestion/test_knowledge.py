import os
import uuid

import pytest

# These tests need the docker Neo4j. Skip if the driver can't connect.
neo4j = pytest.importorskip("neo4j")

os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")

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
from neo4j_client import bootstrap_schema, get_neo4j_session  # noqa: E402


def _neo4j_available() -> bool:
    try:
        with get_neo4j_session() as session:
            session.run("RETURN 1").consume()
        return True
    except Exception:
        return False


requires_neo4j = pytest.mark.skipif(not _neo4j_available(), reason="Neo4j not reachable")


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
