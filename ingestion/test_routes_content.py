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
def test_post_content_with_nul_in_text_is_sanitized(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, key = client_and_org
    resp = client.post("/content", json={"text": "foo\x00bar"}, headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    from db import get_postgres_session
    from models import IngestJob

    with get_postgres_session() as s:
        job = s.get(IngestJob, job_id)
        assert job is not None
        assert "\x00" not in job.content
        assert job.content == "foobar"


@requires_pg
def test_post_content_with_nul_in_metadata_is_not_500(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, key = client_and_org
    resp = client.post(
        "/content",
        json={"text": "hello", "metadata": {"source": "foo\x00bar"}},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code in (202, 422)
    if resp.status_code == 202:
        job_id = resp.json()["job_id"]

        from db import get_postgres_session
        from models import IngestJob

        with get_postgres_session() as s:
            job = s.get(IngestJob, job_id)
            assert job is not None
            assert "\x00" not in (job.job_metadata or {}).get("source", "")


@requires_pg
def test_missing_auth_is_401(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, _key = client_and_org
    assert client.post("/content", json={"text": "x"}).status_code == 401


@pytest.fixture
def two_orgs():  # type: ignore[no-untyped-def]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import generate_api_key, hash_key
    from db import get_postgres_session
    from models import ApiKey, IngestJob, Org
    from routes_content import router

    app = FastAPI()
    app.include_router(router)

    key_a = generate_api_key()
    key_b = generate_api_key()
    with get_postgres_session() as s:
        org_a = Org(name=f"routes-test-a-{uuid.uuid4()}")
        org_b = Org(name=f"routes-test-b-{uuid.uuid4()}")
        s.add_all([org_a, org_b])
        s.flush()
        org_a_id = str(org_a.id)
        org_b_id = str(org_b.id)
        s.add(ApiKey(org_id=org_a.id, key_hash=hash_key(key_a)))
        s.add(ApiKey(org_id=org_b.id, key_hash=hash_key(key_b)))
        job_b = IngestJob(org_id=org_b.id, content="org b's secret content", job_metadata=None)
        s.add(job_b)
        s.flush()
        job_b_id = str(job_b.id)
        s.commit()

    try:
        yield TestClient(app), org_a_id, key_a, org_b_id, key_b, job_b_id
    finally:
        with get_postgres_session() as s:
            s.execute(sqltext("DELETE FROM api_keys WHERE org_id IN (:a, :b)"), {"a": org_a_id, "b": org_b_id})
            s.execute(sqltext("DELETE FROM ingest_jobs WHERE org_id IN (:a, :b)"), {"a": org_a_id, "b": org_b_id})
            s.execute(sqltext("DELETE FROM orgs WHERE id IN (:a, :b)"), {"a": org_a_id, "b": org_b_id})
            s.commit()


@requires_pg
def test_other_orgs_job_is_404(two_orgs) -> None:  # type: ignore[no-untyped-def]
    client, _org_a_id, key_a, _org_b_id, key_b, job_b_id = two_orgs

    # Org A must not be able to see org B's real job.
    resp = client.get(f"/content/{job_b_id}", headers={"Authorization": f"Bearer {key_a}"})
    assert resp.status_code == 404
    missing_resp = client.get(f"/content/{uuid.uuid4()}", headers={"Authorization": f"Bearer {key_a}"})
    assert resp.json() == missing_resp.json()

    # Sanity: org B can see its own job, proving it really exists and A is specifically blocked.
    own_resp = client.get(f"/content/{job_b_id}", headers={"Authorization": f"Bearer {key_b}"})
    assert own_resp.status_code == 200
    assert own_resp.json()["job_id"] == job_b_id


@requires_pg
def test_malformed_job_id_is_404(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, key = client_and_org
    resp = client.get("/content/not-a-uuid", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
