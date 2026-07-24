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
    from models import AuthSession, EmailToken, KnowledgeBase, KnowledgeBaseUser, User

    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one_or_none()
        if user is None:
            return
        kb_ids = [
            row[0]
            for row in s.query(KnowledgeBaseUser.knowledge_base_id).filter(KnowledgeBaseUser.user_id == user.id).all()
        ]
        s.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
        s.query(EmailToken).filter(EmailToken.user_id == user.id).delete(synchronize_session=False)
        s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).delete(synchronize_session=False)
        if kb_ids:
            s.query(KnowledgeBase).filter(KnowledgeBase.id.in_(kb_ids)).delete(synchronize_session=False)
        s.query(User).filter(User.id == user.id).delete(synchronize_session=False)
        s.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from graph_api import cookie_graphql_router
    from routes_auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(cookie_graphql_router, prefix="/api/graphql")
    yield TestClient(app, base_url="https://testserver", headers=LOCALHOST_ORIGIN)


def _unique_email() -> str:
    return f"routes-graphql-test-{uuid.uuid4()}@example.com"


@requires_stack
def test_graphql_requires_session() -> None:
    from graph_api import cookie_graphql_router
    from routes_auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(cookie_graphql_router, prefix="/api/graphql")
    anon = TestClient(app, base_url="https://testserver", headers=LOCALHOST_ORIGIN)
    resp = anon.post("/api/graphql", json={"query": "{ nodes { id } }"})
    assert resp.status_code == 401


@requires_stack
def test_graphql_runs_query_scoped_to_session_knowledge_base(client: TestClient) -> None:
    email = _unique_email()
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        resp = client.post("/api/graphql", json={"query": "{ nodes { id name type } }"})
        assert resp.status_code == 200
        body = resp.json()
        assert "errors" not in body, body
        # A fresh account's graph is empty, but the query resolves against its own knowledge base.
        assert body["data"] == {"nodes": []}
    finally:
        _purge_user(email)
