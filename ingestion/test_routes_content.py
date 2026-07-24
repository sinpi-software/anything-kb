import os
import uuid

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
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


@pytest.fixture
def client_and_org():  # type: ignore[no-untyped-def]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import generate_api_key, hash_key
    from db import get_postgres_session
    from models import ApiKey, Org
    from routes_content import router

    app = FastAPI()
    app.include_router(router)

    key = generate_api_key()
    with get_postgres_session() as s:
        org = Org(name=f"routes-test-{uuid.uuid4()}")
        s.add(org)
        s.flush()
        org_id = str(org.id)
        s.add(ApiKey(org_id=org.id, key_hash=hash_key(key)))
        s.commit()

    yield TestClient(app), org_id, key

    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM api_keys WHERE org_id = :o"), {"o": org_id})
        s.execute(sqltext("DELETE FROM ingest_jobs WHERE org_id = :o"), {"o": org_id})
        s.execute(sqltext("DELETE FROM orgs WHERE id = :o"), {"o": org_id})
        s.commit()


@requires_pg
def test_post_content_enqueues_and_returns_202(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, key = client_and_org
    resp = client.post("/content", json={"text": "hello"}, headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 202
    assert "job_id" in resp.json()


@requires_pg
def test_get_content_reflects_status(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, key = client_and_org
    job_id = client.post("/content", json={"text": "hi"}, headers={"Authorization": f"Bearer {key}"}).json()["job_id"]
    resp = client.get(f"/content/{job_id}", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@requires_pg
def test_missing_auth_is_401(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, _key = client_and_org
    assert client.post("/content", json={"text": "x"}).status_code == 401


@requires_pg
def test_other_orgs_job_is_404(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, key = client_and_org
    resp = client.get(f"/content/{uuid.uuid4()}", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
