import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text as sqltext

from accounts import (
    consume_email_token,
    create_email_token,
    create_session,
    delete_session,
    hash_password,
    require_csrf,
    resolve_session_user,
    verify_password,
)
from db import get_postgres_session
from models import EmailToken, User


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as session:
            session.execute(sqltext("SELECT 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


def test_hash_password_then_verify_round_trips() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_verify_password_rejects_garbage_hash() -> None:
    assert not verify_password("anything", "not-a-real-argon2-hash")


@pytest.fixture
def seeded_user() -> Iterator[User]:
    with get_postgres_session() as session:
        user = User(
            name="Test User",
            email=f"accounts-test-{uuid.uuid4()}@example.com",
            password_hash=hash_password("irrelevant-password"),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        yield user
        session.execute(sqltext("DELETE FROM sessions WHERE user_id = :u"), {"u": user.id})
        session.execute(sqltext("DELETE FROM email_tokens WHERE user_id = :u"), {"u": user.id})
        session.query(User).filter(User.id == user.id).delete()
        session.commit()


@requires_postgres
def test_session_create_resolve_delete_round_trip(seeded_user: User) -> None:
    with get_postgres_session() as session:
        token = create_session(session, seeded_user.id)

    with get_postgres_session() as session:
        user = resolve_session_user(session, token)
        assert user is not None
        assert str(user.id) == str(seeded_user.id)

    with get_postgres_session() as session:
        delete_session(session, token)

    with get_postgres_session() as session:
        assert resolve_session_user(session, token) is None


@requires_postgres
def test_resolve_session_user_rejects_unknown_token(seeded_user: User) -> None:
    with get_postgres_session() as session:
        assert resolve_session_user(session, "not-a-real-token") is None


@requires_postgres
def test_resolve_session_user_rejects_expired_session(seeded_user: User) -> None:
    with get_postgres_session() as session:
        token = create_session(session, seeded_user.id)
        session.execute(
            sqltext("UPDATE sessions SET expires_at = :past WHERE user_id = :u"),
            {"past": datetime.now(UTC) - timedelta(days=1), "u": seeded_user.id},
        )
        session.commit()

    with get_postgres_session() as session:
        assert resolve_session_user(session, token) is None


@requires_postgres
def test_email_token_is_single_use(seeded_user: User) -> None:
    with get_postgres_session() as session:
        token = create_email_token(session, seeded_user.id, "verify")

    with get_postgres_session() as session:
        user = consume_email_token(session, token, "verify")
        assert user is not None
        assert str(user.id) == str(seeded_user.id)

    # a second redemption fails: it's already used
    with get_postgres_session() as session:
        assert consume_email_token(session, token, "verify") is None


@requires_postgres
def test_email_token_rejects_wrong_purpose(seeded_user: User) -> None:
    with get_postgres_session() as session:
        token = create_email_token(session, seeded_user.id, "verify")

    with get_postgres_session() as session:
        assert consume_email_token(session, token, "reset") is None


@requires_postgres
def test_email_token_rejects_expired(seeded_user: User) -> None:
    with get_postgres_session() as session:
        token = create_email_token(session, seeded_user.id, "reset")
        session.query(EmailToken).filter(EmailToken.user_id == seeded_user.id).update(
            {"expires_at": datetime.now(UTC) - timedelta(hours=1)}
        )
        session.commit()

    with get_postgres_session() as session:
        assert consume_email_token(session, token, "reset") is None


def _csrf_app() -> FastAPI:
    app = FastAPI()

    @app.get("/safe", dependencies=[Depends(require_csrf)])
    def safe() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/unsafe", dependencies=[Depends(require_csrf)])
    def unsafe() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_require_csrf_exempts_safe_methods() -> None:
    client = TestClient(_csrf_app())
    assert client.get("/safe").status_code == 200


def test_require_csrf_rejects_missing_origin() -> None:
    client = TestClient(_csrf_app())
    assert client.post("/unsafe").status_code == 403


def test_require_csrf_allows_localhost_in_dev() -> None:
    client = TestClient(_csrf_app())
    resp = client.post("/unsafe", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 200


def test_require_csrf_rejects_foreign_origin() -> None:
    client = TestClient(_csrf_app())
    resp = client.post("/unsafe", headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 403


def test_require_csrf_allows_configured_app_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ORIGINS", "https://desk.sinpi.software")
    client = TestClient(_csrf_app())
    resp = client.post("/unsafe", headers={"Origin": "https://desk.sinpi.software"})
    assert resp.status_code == 200
