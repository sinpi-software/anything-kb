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
    from models import ApiKey, AuthSession, EmailToken, KnowledgeBase, KnowledgeBaseUser, User

    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one_or_none()
        if user is None:
            return
        knowledge_base_ids = [
            row[0]
            for row in s.query(KnowledgeBaseUser.knowledge_base_id).filter(KnowledgeBaseUser.user_id == user.id).all()
        ]
        if knowledge_base_ids:
            s.query(ApiKey).filter(ApiKey.knowledge_base_id.in_(knowledge_base_ids)).delete(synchronize_session=False)
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
    from routes_keys import router as keys_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(keys_router)
    # base_url must be https:// — the session cookie is Secure, so httpx's cookie jar
    # only attaches it back on an https connection (matching real browser behavior).
    # A default Origin header keeps setup calls (register/login) past require_csrf,
    # since it's an allowed localhost origin — same as our real SPA would send.
    yield TestClient(app, base_url="https://testserver", headers=LOCALHOST_ORIGIN)


def _unique_email() -> str:
    return f"routes-keys-test-{uuid.uuid4()}@example.com"


def _register_and_verify(client: TestClient, email: str) -> None:
    """Register a user and mark their email verified directly (bypassing the mail flow)."""
    from db import get_postgres_session
    from models import User

    client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one()
        user.email_verified = True
        s.commit()


@requires_pg
def test_create_key_is_403_when_email_unverified(client: TestClient) -> None:
    email = _unique_email()
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        resp = client.post("/api/keys", json={"name": "my key"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 403
    finally:
        _purge_user(email)


@requires_pg
def test_create_key_requires_csrf_origin(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        del client.headers["origin"]  # the fixture's default Origin would otherwise pass the check
        resp = client.post("/api/keys", json={"name": "my key"})  # no Origin
        assert resp.status_code == 403
    finally:
        _purge_user(email)


@requires_pg
def test_create_key_is_201_when_verified_and_key_authenticates_against_engine(client: TestClient) -> None:
    from auth import hash_key, resolve_knowledge_base
    from db import get_postgres_session
    from models import ApiKey, KnowledgeBaseUser, User

    email = _unique_email()
    try:
        _register_and_verify(client, email)
        resp = client.post("/api/keys", json={"name": "ci key"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "ci key"
        raw_key = body["key"]
        assert raw_key  # the raw key is shown once
        assert body["prefix"] == raw_key[:10]

        # never persisted in plaintext
        with get_postgres_session() as s:
            row = s.query(ApiKey).filter(ApiKey.id == body["id"]).one()
            assert row.key_hash != raw_key
            assert row.key_hash == hash_key(raw_key)

            user = s.query(User).filter(User.email == email).one()
            knowledge_base_id = (
                s.query(KnowledgeBaseUser.knowledge_base_id).filter(KnowledgeBaseUser.user_id == user.id).scalar()
            )

        # the engine's Bearer auth (resolve_knowledge_base) accepts the freshly minted key unchanged
        assert resolve_knowledge_base(raw_key) == str(knowledge_base_id)
    finally:
        _purge_user(email)


@requires_pg
def test_list_keys_never_leaks_raw_key_or_hash(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        create = client.post("/api/keys", json={"name": "listable"}, headers=LOCALHOST_ORIGIN)
        raw_key = create.json()["key"]
        key_hash_prefix = create.json()["id"]

        listing = client.get("/api/keys")
        assert listing.status_code == 200
        keys = listing.json()
        assert len(keys) == 1
        entry = keys[0]
        assert entry["id"] == key_hash_prefix
        assert entry["name"] == "listable"
        assert entry["prefix"] == raw_key[:10]
        assert "key" not in entry
        assert "key_hash" not in entry
        assert raw_key not in str(entry)
    finally:
        _purge_user(email)


@requires_pg
def test_revoke_key_stops_it_from_resolving(client: TestClient) -> None:
    from auth import resolve_knowledge_base

    email = _unique_email()
    try:
        _register_and_verify(client, email)
        create = client.post("/api/keys", json={"name": "revoke-me"}, headers=LOCALHOST_ORIGIN)
        key_id = create.json()["id"]
        raw_key = create.json()["key"]
        assert resolve_knowledge_base(raw_key)  # works before revocation

        revoke = client.delete(f"/api/keys/{key_id}", headers=LOCALHOST_ORIGIN)
        assert revoke.status_code == 204

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            resolve_knowledge_base(raw_key)
        assert exc.value.status_code == 401

        listing = client.get("/api/keys").json()
        assert listing[0]["revoked_at"] is not None
    finally:
        _purge_user(email)


@requires_pg
def test_revoke_unknown_key_is_404(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        resp = client.delete(f"/api/keys/{uuid.uuid4()}", headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 404
    finally:
        _purge_user(email)


@requires_pg
def test_keys_require_login(client: TestClient) -> None:
    assert client.get("/api/keys").status_code == 401
    assert client.post("/api/keys", json={"name": "x"}, headers=LOCALHOST_ORIGIN).status_code == 401
