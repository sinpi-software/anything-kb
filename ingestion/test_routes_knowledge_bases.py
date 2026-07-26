import os
import uuid
from collections.abc import Iterator

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text as sqltext


def _postgres_available() -> bool:
    try:
        from db import get_postgres_session

        with get_postgres_session() as s:
            s.execute(sqltext("SELECT 1"))
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")
LOCALHOST_ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    from routes_auth import router as auth_router
    from routes_knowledge_bases import router as kb_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(kb_router)
    # base_url must be https:// — the session cookie is Secure, so httpx only sends it
    # back over https, matching real browser behavior.
    yield TestClient(app, base_url="https://testserver", headers=LOCALHOST_ORIGIN)


def _register_and_verify(client: TestClient, email: str) -> None:
    from db import get_postgres_session
    from models import User

    client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one()
        user.email_verified = True
        s.commit()


def _purge_everything(email: str) -> None:
    from db import get_postgres_session
    from models import (
        ApiKey,
        AuthSession,
        EmailToken,
        IngestJob,
        KnowledgeBase,
        KnowledgeBaseConfig,
        KnowledgeBaseUser,
        User,
    )

    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one_or_none()
        if user is None:
            return
        ids = [r[0] for r in s.query(KnowledgeBaseUser.knowledge_base_id).filter(
            KnowledgeBaseUser.user_id == user.id).all()]
        if ids:
            for model in (ApiKey, IngestJob, KnowledgeBaseConfig):
                s.query(model).filter(model.knowledge_base_id.in_(ids)).delete(synchronize_session=False)
        s.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
        s.query(EmailToken).filter(EmailToken.user_id == user.id).delete(synchronize_session=False)
        s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).delete(synchronize_session=False)
        if ids:
            s.query(KnowledgeBase).filter(KnowledgeBase.id.in_(ids)).delete(synchronize_session=False)
        s.query(User).filter(User.id == user.id).delete(synchronize_session=False)
        s.commit()


@requires_pg
def test_list_returns_the_registration_knowledge_base_with_role(client: TestClient) -> None:
    email = f"kb-list-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        resp = client.get("/api/knowledge-bases")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["name"] == "My workspace"
        assert body[0]["role"] == "owner"
    finally:
        _purge_everything(email)


@requires_pg
def test_create_adds_a_second_knowledge_base_owned_by_the_caller(client: TestClient) -> None:
    email = f"kb-create-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        resp = client.post("/api/knowledge-bases", json={"name": "Second brain"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 201
        assert resp.json()["role"] == "owner"
        assert len(client.get("/api/knowledge-bases").json()) == 2
    finally:
        _purge_everything(email)


@requires_pg
def test_create_is_403_when_email_unverified(client: TestClient) -> None:
    """Mirrors routes_keys.py's guard — a knowledge base is where API keys live."""
    email = f"kb-unver-{uuid.uuid4()}@example.com"
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        resp = client.post("/api/knowledge-bases", json={"name": "Nope"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 403
    finally:
        _purge_everything(email)


@requires_pg
def test_create_rejects_a_blank_name(client: TestClient) -> None:
    email = f"kb-blank-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        resp = client.post("/api/knowledge-bases", json={"name": "   "}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 422
    finally:
        _purge_everything(email)


@requires_pg
def test_rename_changes_the_name(client: TestClient) -> None:
    email = f"kb-rename-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        kb_id = client.get("/api/knowledge-bases").json()[0]["id"]
        resp = client.patch(f"/api/knowledge-bases/{kb_id}", json={"name": "Renamed"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"
    finally:
        _purge_everything(email)


@requires_pg
def test_another_users_knowledge_base_is_404_everywhere(client: TestClient) -> None:
    """404 not 403 — a 403 would confirm the knowledge base exists."""
    owner_email = f"kb-owner-{uuid.uuid4()}@example.com"
    other_email = f"kb-other-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, owner_email)
        kb_id = client.get("/api/knowledge-bases").json()[0]["id"]
        client.post("/api/auth/logout", headers=LOCALHOST_ORIGIN)
        _register_and_verify(client, other_email)

        assert client.get("/api/knowledge-bases").json()[0]["id"] != kb_id
        resp = client.patch(f"/api/knowledge-bases/{kb_id}", json={"name": "Stolen"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 404
    finally:
        _purge_everything(owner_email)
        _purge_everything(other_email)
