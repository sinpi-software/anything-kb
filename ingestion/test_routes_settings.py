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


def _purge_user(email: str) -> None:
    from db import get_postgres_session
    from models import AuthSession, EmailToken, KnowledgeBase, KnowledgeBaseConfig, KnowledgeBaseUser, User

    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one_or_none()
        if user is None:
            return
        knowledge_base_ids = [
            row[0]
            for row in s.query(KnowledgeBaseUser.knowledge_base_id).filter(KnowledgeBaseUser.user_id == user.id).all()
        ]
        if knowledge_base_ids:
            s.query(KnowledgeBaseConfig).filter(KnowledgeBaseConfig.knowledge_base_id.in_(knowledge_base_ids)).delete(
                synchronize_session=False
            )
        s.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
        s.query(EmailToken).filter(EmailToken.user_id == user.id).delete(synchronize_session=False)
        s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).delete(synchronize_session=False)
        if knowledge_base_ids:
            s.query(KnowledgeBase).filter(KnowledgeBase.id.in_(knowledge_base_ids)).delete(synchronize_session=False)
        s.query(User).filter(User.id == user.id).delete(synchronize_session=False)
        s.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from routes_auth import router as auth_router
    from routes_settings import router as settings_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(settings_router)
    yield TestClient(app, base_url="https://testserver", headers=LOCALHOST_ORIGIN)


def _unique_email() -> str:
    return f"routes-settings-test-{uuid.uuid4()}@example.com"


def _register_and_verify(client: TestClient, email: str) -> None:
    from db import get_postgres_session
    from models import User

    client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one()
        user.email_verified = True
        s.commit()


@requires_pg
def test_get_config_is_empty_for_fresh_account(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        resp = client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["interests"] == ""
        assert body["discover_types"] is True
        assert body["entity_types"] == []
        assert body["relationship_types"] == []
    finally:
        _purge_user(email)


@requires_pg
def test_put_then_get_roundtrips_config(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        put = client.put(
            "/api/config",
            json={
                "interests": "Is this about AI?",
                "discover_types": False,
                "entity_types": [
                    {"name": "Person", "description": "a named human"},
                    {"name": "Organization", "description": "a company"},
                ],
                "relationship_types": [{"name": "WORKS_AT", "description": ""}],
            },
            headers=LOCALHOST_ORIGIN,
        )
        assert put.status_code == 200
        got = client.get("/api/config").json()
        assert got["interests"] == "Is this about AI?"
        assert got["discover_types"] is False
        assert got["entity_types"] == [
            {"name": "Person", "description": "a named human", "pinned": False, "banned": False},
            {"name": "Organization", "description": "a company", "pinned": False, "banned": False},
        ]
        assert got["relationship_types"] == [{"name": "WORKS_AT", "description": "", "pinned": False, "banned": False}]
    finally:
        _purge_user(email)


@requires_pg
def test_put_config_preserves_pinned_and_banned_flags(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        put = client.put(
            "/api/config",
            json={
                "interests": "Is this about AI?",
                "discover_types": True,
                "entity_types": [
                    {"name": "Person", "description": "a named human", "pinned": True, "banned": False},
                    {"name": "Spam", "description": "not relevant", "pinned": False, "banned": True},
                ],
                "relationship_types": [],
            },
            headers=LOCALHOST_ORIGIN,
        )
        assert put.status_code == 200
        assert put.json()["entity_types"] == [
            {"name": "Person", "description": "a named human", "pinned": True, "banned": False},
            {"name": "Spam", "description": "not relevant", "pinned": False, "banned": True},
        ]
        got = client.get("/api/config").json()
        assert got["entity_types"] == [
            {"name": "Person", "description": "a named human", "pinned": True, "banned": False},
            {"name": "Spam", "description": "not relevant", "pinned": False, "banned": True},
        ]
    finally:
        _purge_user(email)


@requires_pg
def test_put_config_sanitizes_and_drops_blank_types(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        put = client.put(
            "/api/config",
            json={
                "interests": "keep\x00this",
                "discover_types": True,
                "entity_types": [
                    {"name": "Person", "description": "sane\x00desc"},
                    {"name": "  ", "description": "blank name dropped"},
                    {"name": "", "description": "also dropped"},
                    {"name": "Place", "description": ""},
                ],
                "relationship_types": [],
            },
            headers=LOCALHOST_ORIGIN,
        )
        assert put.status_code == 200
        body = put.json()
        assert body["interests"] == "keepthis"
        assert body["entity_types"] == [
            {"name": "Person", "description": "sanedesc", "pinned": False, "banned": False},
            {"name": "Place", "description": "", "pinned": False, "banned": False},
        ]
    finally:
        _purge_user(email)


@requires_pg
def test_put_config_is_403_when_email_unverified(client: TestClient) -> None:
    email = _unique_email()
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        resp = client.put(
            "/api/config",
            json={"interests": "x", "discover_types": True, "entity_types": [], "relationship_types": []},
            headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 403
    finally:
        _purge_user(email)


@requires_pg
def test_put_config_requires_csrf_origin(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        del client.headers["origin"]
        resp = client.put(
            "/api/config",
            json={"interests": "x", "discover_types": True, "entity_types": [], "relationship_types": []},
        )
        assert resp.status_code == 403
    finally:
        _purge_user(email)


@requires_pg
def test_config_requires_auth(client: TestClient) -> None:
    assert client.get("/api/config").status_code == 401
