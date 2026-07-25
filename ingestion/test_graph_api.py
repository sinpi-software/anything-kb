import os
import uuid
from typing import Any

os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")
os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from sqlalchemy import text as sqltext

import config
from graph_read import query_edges
from knowledge import ExtractedEntity, upsert_entity, write_provenance, write_relationship
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
        from db import get_postgres_session

        with get_postgres_session() as s:
            s.execute(sqltext("SELECT 1"))
        return True
    except Exception:
        return False


requires_stack = pytest.mark.skipif(
    not (_neo4j_available() and _postgres_available()), reason="Neo4j and/or Postgres not reachable"
)


@pytest.fixture
def seeded():  # type: ignore[no-untyped-def]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import generate_api_key, hash_key
    from db import get_postgres_session
    from graph_api import graphql_router
    from models import ApiKey, KnowledgeBase

    bootstrap_schema()
    knowledge_base_a = f"a-{uuid.uuid4()}"
    knowledge_base_b = f"b-{uuid.uuid4()}"
    ada_id, eng_id = str(uuid.uuid4()), str(uuid.uuid4())
    secret_id = str(uuid.uuid4())
    knowledge_base_b_ada_id = str(uuid.uuid4())
    with get_neo4j_session() as s:
        upsert_entity(
            s,
            knowledge_base_a,
            ada_id,
            ExtractedEntity(name="Ada", type="Person", description="d"),
            "Ada summary",
            "Ada article",
        )
        upsert_entity(
            s,
            knowledge_base_a,
            eng_id,
            ExtractedEntity(name="Engine", type="Thing", description="d"),
            "Engine summary",
            "Engine article",
        )
        write_relationship(s, knowledge_base_a, ada_id, eng_id, "WORKED_ON", "job-1")
        write_provenance(s, knowledge_base_a, ada_id, "job-ada", label="Ada Source", date="2024-01-01")
        upsert_entity(
            s, knowledge_base_b, secret_id, ExtractedEntity(name="Secret", type="Person", description="d"), "x", "x"
        )
        write_provenance(s, knowledge_base_b, secret_id, "job-secret", label="Secret Source", date="2024-02-02")
        # Same name as knowledge_base_a's node, but in knowledge_base_b: proves full-text
        # `search` is knowledge_base-scoped, not just name-scoped.
        upsert_entity(
            s,
            knowledge_base_b,
            knowledge_base_b_ada_id,
            ExtractedEntity(name="Ada", type="Person", description="d"),
            "knowledge_base_b Ada",
            "knowledge_base_b Ada article",
        )

    key = generate_api_key()
    with get_postgres_session() as s:
        knowledge_base = (
            KnowledgeBase(name=f"gql-{uuid.uuid4()}", id=knowledge_base_a)
            if False
            else KnowledgeBase(name=f"gql-{uuid.uuid4()}")
        )
        s.add(knowledge_base)
        s.flush()
        # bind the API key to knowledge_base_a's graph id by overriding knowledge_base_id directly
        s.add(ApiKey(knowledge_base_id=knowledge_base.id, key_hash=hash_key(key)))
        s.flush()
        pg_knowledge_base_id = str(knowledge_base.id)
        s.commit()

    # Re-key the seeded graph to the Postgres knowledge_base id so auth
    # (which returns the pg knowledge_base id) matches.
    with get_neo4j_session() as s:
        s.run(
            "MATCH (n) WHERE n.knowledge_base_id = $old SET n.knowledge_base_id = $new",
            {"old": knowledge_base_a, "new": pg_knowledge_base_id},
        )
        s.run(
            "MATCH ()-[r]->() WHERE r.knowledge_base_id = $old SET r.knowledge_base_id = $new",
            {"old": knowledge_base_a, "new": pg_knowledge_base_id},
        )

    app = FastAPI()
    app.include_router(graphql_router, prefix="/graphql")
    client = TestClient(app)

    yield client, key, pg_knowledge_base_id, ada_id, eng_id, knowledge_base_b, secret_id

    with get_neo4j_session() as s:
        s.run(
            "MATCH (n) WHERE n.knowledge_base_id IN [$a, $b] DETACH DELETE n",
            {"a": pg_knowledge_base_id, "b": knowledge_base_b},
        )
    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM api_keys WHERE knowledge_base_id = :o"), {"o": pg_knowledge_base_id})
        s.execute(sqltext("DELETE FROM knowledge_bases WHERE id = :o"), {"o": pg_knowledge_base_id})
        s.commit()


def _gql(client: Any, key: str, query: str) -> Any:
    return client.post("/graphql", json={"query": query}, headers={"Authorization": f"Bearer {key}"})


@requires_stack
def test_nodes_lists_only_callers_knowledge_base(seeded) -> None:  # type: ignore[no-untyped-def]
    client, key, _knowledge_base, _ada, _eng, _knowledge_base_b, _secret = seeded
    resp = _gql(client, key, "{ nodes { name type } }")
    names = {n["name"] for n in resp.json()["data"]["nodes"]}
    assert names == {"Ada", "Engine"}  # knowledge_base_b's "Secret" is invisible


@requires_stack
def test_nodes_limit_is_clamped(monkeypatch: pytest.MonkeyPatch, seeded) -> None:  # type: ignore[no-untyped-def]
    # knowledge_base_a has 2 seeded entities (Ada, Engine). Clamp the cap to 1 and request a huge
    # limit: a caller-requested 100000 must never reach Cypher; the server-side cap wins.
    monkeypatch.setattr(config, "NODES_MAX_LIMIT", 1)
    client, key, _knowledge_base, _ada, _eng, _knowledge_base_b, _secret = seeded
    resp = _gql(client, key, "{ nodes(limit: 100000) { name } }")
    assert resp.status_code == 200
    assert len(resp.json()["data"]["nodes"]) == 1


@requires_stack
def test_nodes_filter_by_type(seeded) -> None:  # type: ignore[no-untyped-def]
    client, key, _knowledge_base, _ada, _eng, _knowledge_base_b, _secret = seeded
    resp = _gql(client, key, '{ nodes(type: "Person") { name } }')
    assert [n["name"] for n in resp.json()["data"]["nodes"]] == ["Ada"]


@requires_stack
def test_node_and_edges(seeded) -> None:  # type: ignore[no-untyped-def]
    client, key, _knowledge_base, ada_id, _eng, _knowledge_base_b, _secret = seeded
    resp = _gql(client, key, f'{{ node(id: "{ada_id}") {{ name edges {{ type target {{ name }} }} }} }}')
    node = resp.json()["data"]["node"]
    assert node["name"] == "Ada"
    assert node["edges"][0]["type"] == "WORKED_ON"
    assert node["edges"][0]["target"]["name"] == "Engine"


@requires_stack
def test_graphql_requires_auth(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _key, _knowledge_base, _ada, _eng, _knowledge_base_b, _secret = seeded
    resp = client.post("/graphql", json={"query": "{ nodes { name } }"})
    assert resp.status_code == 401


@requires_stack
def test_node_foreign_knowledge_base_returns_null(seeded) -> None:  # type: ignore[no-untyped-def]
    # knowledge_base_b's "Secret" node exists, but knowledge_base_a's caller must never see it via node(id).
    client, key, _knowledge_base, _ada, _eng, _knowledge_base_b, secret_id = seeded
    resp = _gql(client, key, f'{{ node(id: "{secret_id}") {{ name }} }}')
    assert resp.json()["data"]["node"] is None


@requires_stack
def test_search_is_knowledge_base_scoped(seeded) -> None:  # type: ignore[no-untyped-def]
    # knowledge_base_b also has a node named "Ada" (knowledge_base_b_ada_id, seeded
    # alongside secret_id); full-text search
    # as knowledge_base_a must only ever return knowledge_base_a's own "Ada", never knowledge_base_b's same-named node.
    client, key, _knowledge_base, _ada, _eng, _knowledge_base_b, _secret = seeded
    resp = _gql(client, key, '{ nodes(search: "Ada") { name summary } }')
    results = resp.json()["data"]["nodes"]
    assert [r["name"] for r in results] == ["Ada"]
    assert results[0]["summary"] == "Ada summary"


@requires_stack
def test_node_exposes_article_and_references(seeded) -> None:  # type: ignore[no-untyped-def]
    client, key, _knowledge_base, ada_id, _eng, _knowledge_base_b, _secret = seeded
    resp = _gql(client, key, f'{{ node(id: "{ada_id}") {{ article references {{ label date }} }} }}')
    node = resp.json()["data"]["node"]
    assert node["article"] == "Ada article"
    assert node["references"] == [{"label": "Ada Source", "date": "2024-01-01"}]


@requires_stack
def test_query_edges_excludes_cross_knowledge_base_target() -> None:
    # Simulates a corrupt/foreign-target RELATED edge (knowledge_base-A source, knowledge_base-B target, edge stamped
    # knowledge_base_id=knowledge_base-A) that write_relationship could never produce but a future writer or a bad
    # migration could. query_edges must drop it because b.knowledge_base_id != $knowledge_base_id, not because the
    # write side happens to keep edges same-knowledge_base.
    knowledge_base_a, knowledge_base_b = f"a-{uuid.uuid4()}", f"b-{uuid.uuid4()}"
    src_id, foreign_target_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        with get_neo4j_session() as s:
            s.run(
                "CREATE (a:Entity {id: $src, knowledge_base_id: $knowledge_base_a, "
                "type: 'Person', name: 'Src', summary: 's'}), "
                "(b:Entity {id: $tgt, knowledge_base_id: $knowledge_base_b, "
                "type: 'Person', name: 'Foreign', summary: 's'}), "
                "(a)-[:RELATED {knowledge_base_id: $knowledge_base_a, type: 'LINKED'}]->(b)",
                {
                    "src": src_id,
                    "tgt": foreign_target_id,
                    "knowledge_base_a": knowledge_base_a,
                    "knowledge_base_b": knowledge_base_b,
                },
            )
        assert query_edges(knowledge_base_a, src_id, None) == []
    finally:
        with get_neo4j_session() as s:
            s.run(
                "MATCH (n) WHERE n.knowledge_base_id IN [$a, $b] DETACH DELETE n",
                {"a": knowledge_base_a, "b": knowledge_base_b},
            )
