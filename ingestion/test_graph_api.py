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
from knowledge import ExtractedEntity, upsert_entity, write_relationship
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
    from models import ApiKey, Org

    bootstrap_schema()
    org_a = f"a-{uuid.uuid4()}"
    org_b = f"b-{uuid.uuid4()}"
    ada_id, eng_id = str(uuid.uuid4()), str(uuid.uuid4())
    secret_id = str(uuid.uuid4())
    org_b_ada_id = str(uuid.uuid4())
    with get_neo4j_session() as s:
        upsert_entity(s, org_a, ada_id, ExtractedEntity(name="Ada", type="Person", description="d"), "Ada summary")
        upsert_entity(s, org_a, eng_id, ExtractedEntity(name="Engine", type="Thing", description="d"), "Engine summary")
        write_relationship(s, org_a, ada_id, eng_id, "WORKED_ON", "job-1")
        upsert_entity(s, org_b, secret_id, ExtractedEntity(name="Secret", type="Person", description="d"), "x")
        # Same name as org_a's node, in org_b: proves full-text `search` is org-scoped, not just name-scoped.
        upsert_entity(s, org_b, org_b_ada_id, ExtractedEntity(name="Ada", type="Person", description="d"), "org_b Ada")

    key = generate_api_key()
    with get_postgres_session() as s:
        org = Org(name=f"gql-{uuid.uuid4()}", id=org_a) if False else Org(name=f"gql-{uuid.uuid4()}")
        s.add(org)
        s.flush()
        # bind the API key to org_a's graph id by overriding org_id directly
        s.add(ApiKey(org_id=org.id, key_hash=hash_key(key)))
        s.flush()
        pg_org_id = str(org.id)
        s.commit()

    # Re-key the seeded graph to the Postgres org id so auth (which returns the pg org id) matches.
    with get_neo4j_session() as s:
        s.run("MATCH (n) WHERE n.org_id = $old SET n.org_id = $new", {"old": org_a, "new": pg_org_id})
        s.run(
            "MATCH ()-[r]->() WHERE r.org_id = $old SET r.org_id = $new",
            {"old": org_a, "new": pg_org_id},
        )

    app = FastAPI()
    app.include_router(graphql_router, prefix="/graphql")
    client = TestClient(app)

    yield client, key, pg_org_id, ada_id, eng_id, org_b, secret_id

    with get_neo4j_session() as s:
        s.run("MATCH (n) WHERE n.org_id IN [$a, $b] DETACH DELETE n", {"a": pg_org_id, "b": org_b})
    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM api_keys WHERE org_id = :o"), {"o": pg_org_id})
        s.execute(sqltext("DELETE FROM orgs WHERE id = :o"), {"o": pg_org_id})
        s.commit()


def _gql(client: Any, key: str, query: str) -> Any:
    return client.post("/graphql", json={"query": query}, headers={"Authorization": f"Bearer {key}"})


@requires_stack
def test_nodes_lists_only_callers_org(seeded) -> None:  # type: ignore[no-untyped-def]
    client, key, _org, _ada, _eng, _org_b, _secret = seeded
    resp = _gql(client, key, "{ nodes { name type } }")
    names = {n["name"] for n in resp.json()["data"]["nodes"]}
    assert names == {"Ada", "Engine"}  # org_b's "Secret" is invisible


@requires_stack
def test_nodes_limit_is_clamped(monkeypatch: pytest.MonkeyPatch, seeded) -> None:  # type: ignore[no-untyped-def]
    # org_a has 2 seeded entities (Ada, Engine). Clamp the cap to 1 and request a huge
    # limit: a caller-requested 100000 must never reach Cypher; the server-side cap wins.
    monkeypatch.setattr(config, "NODES_MAX_LIMIT", 1)
    client, key, _org, _ada, _eng, _org_b, _secret = seeded
    resp = _gql(client, key, "{ nodes(limit: 100000) { name } }")
    assert resp.status_code == 200
    assert len(resp.json()["data"]["nodes"]) == 1


@requires_stack
def test_nodes_filter_by_type(seeded) -> None:  # type: ignore[no-untyped-def]
    client, key, _org, _ada, _eng, _org_b, _secret = seeded
    resp = _gql(client, key, '{ nodes(type: "Person") { name } }')
    assert [n["name"] for n in resp.json()["data"]["nodes"]] == ["Ada"]


@requires_stack
def test_node_and_edges(seeded) -> None:  # type: ignore[no-untyped-def]
    client, key, _org, ada_id, _eng, _org_b, _secret = seeded
    resp = _gql(client, key, f'{{ node(id: "{ada_id}") {{ name edges {{ type target {{ name }} }} }} }}')
    node = resp.json()["data"]["node"]
    assert node["name"] == "Ada"
    assert node["edges"][0]["type"] == "WORKED_ON"
    assert node["edges"][0]["target"]["name"] == "Engine"


@requires_stack
def test_graphql_requires_auth(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _key, _org, _ada, _eng, _org_b, _secret = seeded
    resp = client.post("/graphql", json={"query": "{ nodes { name } }"})
    assert resp.status_code == 401


@requires_stack
def test_node_foreign_org_returns_null(seeded) -> None:  # type: ignore[no-untyped-def]
    # org_b's "Secret" node exists, but org_a's caller must never see it via node(id).
    client, key, _org, _ada, _eng, _org_b, secret_id = seeded
    resp = _gql(client, key, f'{{ node(id: "{secret_id}") {{ name }} }}')
    assert resp.json()["data"]["node"] is None


@requires_stack
def test_search_is_org_scoped(seeded) -> None:  # type: ignore[no-untyped-def]
    # org_b also has a node named "Ada" (org_b_ada_id, seeded alongside secret_id); full-text search
    # as org_a must only ever return org_a's own "Ada", never org_b's same-named node.
    client, key, _org, _ada, _eng, _org_b, _secret = seeded
    resp = _gql(client, key, '{ nodes(search: "Ada") { name summary } }')
    results = resp.json()["data"]["nodes"]
    assert [r["name"] for r in results] == ["Ada"]
    assert results[0]["summary"] == "Ada summary"


@requires_stack
def test_query_edges_excludes_cross_org_target() -> None:
    # Simulates a corrupt/foreign-target RELATED edge (org-A source, org-B target, edge stamped
    # org_id=org-A) that write_relationship could never produce but a future writer or a bad
    # migration could. query_edges must drop it because b.org_id != $org_id, not because the
    # write side happens to keep edges same-org.
    org_a, org_b = f"a-{uuid.uuid4()}", f"b-{uuid.uuid4()}"
    src_id, foreign_target_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        with get_neo4j_session() as s:
            s.run(
                "CREATE (a:Entity {id: $src, org_id: $org_a, type: 'Person', name: 'Src', summary: 's'}), "
                "(b:Entity {id: $tgt, org_id: $org_b, type: 'Person', name: 'Foreign', summary: 's'}), "
                "(a)-[:RELATED {org_id: $org_a, type: 'LINKED'}]->(b)",
                {"src": src_id, "tgt": foreign_target_id, "org_a": org_a, "org_b": org_b},
            )
        assert query_edges(org_a, src_id, None) == []
    finally:
        with get_neo4j_session() as s:
            s.run("MATCH (n) WHERE n.org_id IN [$a, $b] DETACH DELETE n", {"a": org_a, "b": org_b})
