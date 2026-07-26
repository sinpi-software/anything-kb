import os
import uuid
from collections.abc import Iterator

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")
os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text as sqltext


def _stack_available() -> bool:
    try:
        from db import get_postgres_session
        from neo4j_client import get_neo4j_session

        with get_postgres_session() as s:
            s.execute(sqltext("SELECT 1"))
        with get_neo4j_session() as s:
            s.run("RETURN 1").consume()
        return True
    except Exception:
        return False


requires_stack = pytest.mark.skipif(not _stack_available(), reason="Postgres and/or Neo4j not reachable")

LOCALHOST_ORIGIN = {"Origin": "http://localhost:5173"}


def _purge_user(email: str) -> None:
    from db import get_postgres_session
    from models import AuthSession, EmailToken, KnowledgeBase, KnowledgeBaseConfig, KnowledgeBaseUser, User

    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one_or_none()
        if user is None:
            return
        kb_ids = [
            row[0]
            for row in s.query(KnowledgeBaseUser.knowledge_base_id).filter(KnowledgeBaseUser.user_id == user.id).all()
        ]
        if kb_ids:
            s.query(KnowledgeBaseConfig).filter(KnowledgeBaseConfig.knowledge_base_id.in_(kb_ids)).delete(
                synchronize_session=False
            )
        s.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
        s.query(EmailToken).filter(EmailToken.user_id == user.id).delete(synchronize_session=False)
        s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).delete(synchronize_session=False)
        if kb_ids:
            s.query(KnowledgeBase).filter(KnowledgeBase.id.in_(kb_ids)).delete(synchronize_session=False)
        s.query(User).filter(User.id == user.id).delete(synchronize_session=False)
        s.commit()


def _own_kb_id(email: str) -> str:
    from db import get_postgres_session
    from models import KnowledgeBaseUser, User

    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one()
        return str(
            s.query(KnowledgeBaseUser.knowledge_base_id).filter(KnowledgeBaseUser.user_id == user.id).one()[0]
        )


@pytest.fixture
def client() -> Iterator[TestClient]:
    from graph_api import cookie_graphql_router
    from routes_auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(cookie_graphql_router, prefix="/api/knowledge-bases/{kb_id}/graphql")
    yield TestClient(app, base_url="https://testserver", headers=LOCALHOST_ORIGIN)


def _unique_email() -> str:
    return f"routes-graphql-test-{uuid.uuid4()}@example.com"


@requires_stack
def test_graphql_requires_session(client: TestClient) -> None:
    kb_id = str(uuid.uuid4())
    resp = client.post(f"/api/knowledge-bases/{kb_id}/graphql", json={"query": "{ nodes { id } }"})
    assert resp.status_code == 401


@requires_stack
def test_graphql_runs_query_scoped_to_the_path_knowledge_base(client: TestClient) -> None:
    email = _unique_email()
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        kb_id = _own_kb_id(email)
        resp = client.post(f"/api/knowledge-bases/{kb_id}/graphql", json={"query": "{ nodes { id name type } }"})
        assert resp.status_code == 200
        body = resp.json()
        assert "errors" not in body, body
        # A fresh account's graph is empty, but the query resolves against its own knowledge base.
        assert body["data"] == {"nodes": []}
    finally:
        _purge_user(email)


@requires_stack
def test_scoped_graphql_requires_membership(client: TestClient) -> None:
    import uuid as _uuid

    email = _unique_email()
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        resp = client.post(
            f"/api/knowledge-bases/{_uuid.uuid4()}/graphql",
            json={"query": "{ nodes(limit: 1) { id } }"},
            headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 404
    finally:
        _purge_user(email)


def _plant_graph_nodes(kb_id: str, count: int = 2) -> None:
    """Create throwaway Entity nodes tagged with `kb_id`, mirroring test_routes_knowledge_bases.py."""
    from neo4j_client import get_driver

    with get_driver().session() as s:
        for _ in range(count):
            s.run(
                "CREATE (:Entity {id: $id, knowledge_base_id: $kb, name: 'planted', type: 'Topic'})",
                id=str(uuid.uuid4()), kb=kb_id,
            ).consume()


@requires_stack
def test_scoped_graphql_uppercase_uuid_reads_the_canonical_graph_nodes(client: TestClient) -> None:
    """`require_membership` accepts a non-canonical UUID (uppercase, dash-less) because
    Postgres canonicalizes on uuid cast, but Neo4j nodes carry the canonical
    lowercase-dashed form and graph_read does an exact Cypher string comparison.
    Querying via an uppercased id must still return the knowledge base's real nodes —
    not silently resolve to `{"nodes": []}`, indistinguishable from a genuinely empty
    knowledge base."""
    from neo4j_client import purge_knowledge_base

    email = _unique_email()
    kb_id = None
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        kb_id = _own_kb_id(email)

        _plant_graph_nodes(kb_id)

        resp = client.post(
            f"/api/knowledge-bases/{kb_id.upper()}/graphql",
            json={"query": "{ nodes { id name } }"},
            headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "errors" not in body, body
        assert len(body["data"]["nodes"]) == 2
    finally:
        if kb_id is not None:
            purge_knowledge_base(kb_id)
        _purge_user(email)
