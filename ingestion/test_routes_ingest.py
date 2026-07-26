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
    from models import AuthSession, EmailToken, IngestJob, KnowledgeBase, KnowledgeBaseConfig, KnowledgeBaseUser, User

    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one_or_none()
        if user is None:
            return
        knowledge_base_ids = [
            row[0]
            for row in s.query(KnowledgeBaseUser.knowledge_base_id).filter(KnowledgeBaseUser.user_id == user.id).all()
        ]
        if knowledge_base_ids:
            s.query(IngestJob).filter(IngestJob.knowledge_base_id.in_(knowledge_base_ids)).delete(
                synchronize_session=False
            )
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
    from routes_ingest import router as ingest_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(ingest_router)
    yield TestClient(app, base_url="https://testserver", headers=LOCALHOST_ORIGIN)


def _unique_email() -> str:
    return f"routes-ingest-test-{uuid.uuid4()}@example.com"


def _register_and_verify(client: TestClient, email: str) -> None:
    from db import get_postgres_session
    from models import User

    client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one()
        user.email_verified = True
        s.commit()


def _own_kb_id(email: str) -> str:
    from db import get_postgres_session
    from models import KnowledgeBaseUser, User

    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one()
        return str(
            s.query(KnowledgeBaseUser.knowledge_base_id).filter(KnowledgeBaseUser.user_id == user.id).one()[0]
        )


@requires_pg
def test_ingest_is_403_when_email_unverified(client: TestClient) -> None:
    email = _unique_email()
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        kb_id = _own_kb_id(email)
        resp = client.post(f"/api/knowledge-bases/{kb_id}/content", json={"text": "hello"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 403
    finally:
        _purge_user(email)


@requires_pg
def test_ingest_requires_csrf_origin(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        kb_id = _own_kb_id(email)
        del client.headers["origin"]  # the fixture's default Origin would otherwise pass the check
        resp = client.post(f"/api/knowledge-bases/{kb_id}/content", json={"text": "hello"})  # no Origin
        assert resp.status_code == 403
    finally:
        _purge_user(email)


@requires_pg
def test_ingest_is_401_when_not_logged_in(client: TestClient) -> None:
    kb_id = str(uuid.uuid4())
    resp = client.post(f"/api/knowledge-bases/{kb_id}/content", json={"text": "hello"}, headers=LOCALHOST_ORIGIN)
    assert resp.status_code == 401


@requires_pg
def test_ingest_creates_pending_job_in_callers_knowledge_base(client: TestClient) -> None:
    from db import get_postgres_session
    from models import IngestJob, JobStatus

    email = _unique_email()
    try:
        _register_and_verify(client, email)
        kb_id = _own_kb_id(email)
        resp = client.post(
            f"/api/knowledge-bases/{kb_id}/content",
            json={"text": "Ada\x00 Lovelace", "metadata": {"source": "walkthrough"}},
            headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        with get_postgres_session() as s:
            job = s.query(IngestJob).filter(IngestJob.id == job_id).one()
            assert str(job.knowledge_base_id) == kb_id
            assert job.status == JobStatus.PENDING.value
            assert job.content == "Ada Lovelace"  # NUL sanitized out
            assert job.job_metadata == {"source": "walkthrough"}
    finally:
        _purge_user(email)


@requires_pg
def test_job_status_is_readable_by_owner(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        kb_id = _own_kb_id(email)
        job_id = client.post(
            f"/api/knowledge-bases/{kb_id}/content", json={"text": "hi"}, headers=LOCALHOST_ORIGIN
        ).json()["job_id"]
        resp = client.get(f"/api/knowledge-bases/{kb_id}/content/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == job_id
        assert body["status"] == "pending"
    finally:
        _purge_user(email)


@requires_pg
def test_another_knowledge_bases_job_is_404(client: TestClient) -> None:
    """Exercises `_job_status`'s ownership check, not `require_membership`: the intruder
    is a legitimate member of their own kb (so membership passes) and asks for the
    owner's job through *their own* kb path — the 404 must come from
    `str(job.knowledge_base_id) != kb_id`, not from being refused their own kb."""
    owner = _unique_email()
    intruder = _unique_email()
    try:
        # owner ingests a job (register auto-logs-in, so the cookie is the owner's)
        _register_and_verify(client, owner)
        owner_kb_id = _own_kb_id(owner)
        job_id = client.post(
            f"/api/knowledge-bases/{owner_kb_id}/content", json={"text": "secret"}, headers=LOCALHOST_ORIGIN
        ).json()["job_id"]

        # registering the intruder overwrites the session cookie with theirs
        _register_and_verify(client, intruder)
        intruder_kb_id = _own_kb_id(intruder)
        resp = client.get(f"/api/knowledge-bases/{intruder_kb_id}/content/{job_id}")
        assert resp.status_code == 404
    finally:
        _purge_user(owner)
        _purge_user(intruder)


@requires_pg
def test_job_status_404_for_garbage_id(client: TestClient) -> None:
    email = _unique_email()
    try:
        _register_and_verify(client, email)
        kb_id = _own_kb_id(email)
        resp = client.get(f"/api/knowledge-bases/{kb_id}/content/not-a-uuid")
        assert resp.status_code == 404
    finally:
        _purge_user(email)


@requires_pg
def test_reader_may_not_ingest_but_editor_may(client: TestClient) -> None:
    """The ingest boundary pair: editor is the floor, reader is refused."""
    from db import get_postgres_session
    from models import KnowledgeBaseUser, User

    email = _unique_email()
    try:
        _register_and_verify(client, email)
        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            membership = s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).one()
            kb_id = str(membership.knowledge_base_id)
            membership.role = "reader"
            s.commit()
        refused = client.post(
            f"/api/knowledge-bases/{kb_id}/content", json={"text": "hi"}, headers=LOCALHOST_ORIGIN
        )
        assert refused.status_code == 404

        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).one().role = "editor"
            s.commit()
        allowed = client.post(
            f"/api/knowledge-bases/{kb_id}/content", json={"text": "hi"}, headers=LOCALHOST_ORIGIN
        )
        assert allowed.status_code == 202
    finally:
        _purge_user(email)
