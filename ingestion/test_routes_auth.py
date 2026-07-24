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
    from models import ApiKey, AuthSession, EmailToken, Org, OrgUser, User

    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one_or_none()
        if user is None:
            return
        org_ids = [row[0] for row in s.query(OrgUser.org_id).filter(OrgUser.user_id == user.id).all()]
        if org_ids:
            s.query(ApiKey).filter(ApiKey.org_id.in_(org_ids)).delete(synchronize_session=False)
        s.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
        s.query(EmailToken).filter(EmailToken.user_id == user.id).delete(synchronize_session=False)
        s.query(OrgUser).filter(OrgUser.user_id == user.id).delete(synchronize_session=False)
        if org_ids:
            s.query(Org).filter(Org.id.in_(org_ids)).delete(synchronize_session=False)
        s.query(User).filter(User.id == user.id).delete(synchronize_session=False)
        s.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from routes_auth import router

    app = FastAPI()
    app.include_router(router)
    # base_url must be https:// — the session cookie is Secure, so httpx's cookie jar
    # only attaches it back on an https connection (matching real browser behavior).
    yield TestClient(app, base_url="https://testserver")


def _unique_email() -> str:
    return f"routes-auth-test-{uuid.uuid4()}@example.com"


@requires_pg
def test_register_sets_cookie_and_me_returns_user_and_org(client: TestClient) -> None:
    email = _unique_email()
    try:
        resp = client.post("/api/auth/register", json={"email": email, "password": "hunter22", "name": "Ada"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["email"] == email
        assert body["email_verified"] is False
        assert len(body["orgs"]) == 1
        assert body["orgs"][0]["role"] == "owner"
        assert "session" in resp.cookies

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == email
        assert me.json()["orgs"][0]["org_id"] == body["orgs"][0]["org_id"]
    finally:
        _purge_user(email)


@requires_pg
def test_register_lowercases_and_defaults_org_name(client: TestClient) -> None:
    email = _unique_email()
    try:
        resp = client.post("/api/auth/register", json={"email": email.upper(), "password": "hunter22"})
        assert resp.status_code == 201
        assert resp.json()["orgs"][0]["org_name"] == "My workspace"
    finally:
        _purge_user(email)


@requires_pg
def test_register_duplicate_email_is_409(client: TestClient) -> None:
    email = _unique_email()
    try:
        first = client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        assert first.status_code == 201
        second = client.post("/api/auth/register", json={"email": email, "password": "other-password"})
        assert second.status_code == 409
    finally:
        _purge_user(email)


@requires_pg
def test_login_wrong_password_is_401(client: TestClient) -> None:
    email = _unique_email()
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        resp = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
        assert resp.status_code == 401
    finally:
        _purge_user(email)


@requires_pg
def test_login_unknown_email_is_401(client: TestClient) -> None:
    resp = client.post("/api/auth/login", json={"email": _unique_email(), "password": "whatever1"})
    assert resp.status_code == 401


@requires_pg
def test_me_without_cookie_is_401(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


@requires_pg
def test_logout_clears_session(client: TestClient) -> None:
    email = _unique_email()
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        assert client.get("/api/auth/me").status_code == 200

        logout = client.post("/api/auth/logout", headers=LOCALHOST_ORIGIN)
        assert logout.status_code == 204

        assert client.get("/api/auth/me").status_code == 401
    finally:
        _purge_user(email)


@requires_pg
def test_logout_requires_csrf_origin(client: TestClient) -> None:
    email = _unique_email()
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        resp = client.post("/api/auth/logout")  # no Origin header
        assert resp.status_code == 403
    finally:
        _purge_user(email)


@requires_pg
def test_verify_email_token_flow(client: TestClient) -> None:
    from db import get_postgres_session
    from models import User

    email = _unique_email()
    try:
        register = client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        assert register.json()["email_verified"] is False

        from accounts import create_email_token

        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            token = create_email_token(s, user.id, "verify")

        resp = client.post("/api/auth/verify-email", json={"token": token})
        assert resp.status_code == 200
        assert resp.json()["email_verified"] is True

        # single-use: the same token doesn't verify twice
        again = client.post("/api/auth/verify-email", json={"token": token})
        assert again.status_code == 400
    finally:
        _purge_user(email)


@requires_pg
def test_verify_email_rejects_bad_token(client: TestClient) -> None:
    resp = client.post("/api/auth/verify-email", json={"token": "not-a-real-token"})
    assert resp.status_code == 400


@requires_pg
def test_forgot_password_always_200_no_leak(client: TestClient) -> None:
    assert client.post("/api/auth/forgot-password", json={"email": _unique_email()}).status_code == 200


@requires_pg
def test_forgot_then_reset_invalidates_old_sessions(client: TestClient) -> None:
    from db import get_postgres_session
    from models import AuthSession, User

    email = _unique_email()
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        old_session_cookie = client.cookies.get("session")
        assert old_session_cookie is not None

        forgot = client.post("/api/auth/forgot-password", json={"email": email})
        assert forgot.status_code == 200

        from accounts import create_email_token

        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            reset_token = create_email_token(s, user.id, "reset")
            sessions_before = s.query(AuthSession).filter(AuthSession.user_id == user.id).count()
            assert sessions_before >= 1

        reset = client.post("/api/auth/reset-password", json={"token": reset_token, "password": "new-password1"})
        assert reset.status_code == 200

        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            remaining = s.query(AuthSession).filter(AuthSession.user_id == user.id).all()
            # exactly the fresh post-reset session should remain
            assert len(remaining) == 1

        # the old cookie must no longer authenticate
        stale_client = TestClient(client.app, base_url="https://testserver")
        stale_client.cookies.set("session", old_session_cookie)
        assert stale_client.get("/api/auth/me").status_code == 401

        # login with the new password succeeds
        login = client.post("/api/auth/login", json={"email": email, "password": "new-password1"})
        assert login.status_code == 200
    finally:
        _purge_user(email)


@requires_pg
def test_reset_password_rejects_bad_token(client: TestClient) -> None:
    resp = client.post("/api/auth/reset-password", json={"token": "nope", "password": "new-password1"})
    assert resp.status_code == 400
