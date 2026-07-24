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
def client_and_knowledge_base():  # type: ignore[no-untyped-def]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import generate_api_key, hash_key
    from db import get_postgres_session
    from models import ApiKey, KnowledgeBase
    from routes_content import router

    app = FastAPI()
    app.include_router(router)

    key = generate_api_key()
    with get_postgres_session() as s:
        knowledge_base = KnowledgeBase(name=f"routes-test-{uuid.uuid4()}")
        s.add(knowledge_base)
        s.flush()
        knowledge_base_id = str(knowledge_base.id)
        s.add(ApiKey(knowledge_base_id=knowledge_base.id, key_hash=hash_key(key)))
        s.commit()

    yield TestClient(app), knowledge_base_id, key

    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM api_keys WHERE knowledge_base_id = :o"), {"o": knowledge_base_id})
        s.execute(sqltext("DELETE FROM ingest_jobs WHERE knowledge_base_id = :o"), {"o": knowledge_base_id})
        s.execute(sqltext("DELETE FROM knowledge_bases WHERE id = :o"), {"o": knowledge_base_id})
        s.commit()


@requires_pg
def test_post_content_enqueues_and_returns_202(client_and_knowledge_base) -> None:  # type: ignore[no-untyped-def]
    client, _knowledge_base_id, key = client_and_knowledge_base
    resp = client.post("/content", json={"text": "hello"}, headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 202
    assert "job_id" in resp.json()


@requires_pg
def test_get_content_reflects_status(client_and_knowledge_base) -> None:  # type: ignore[no-untyped-def]
    client, _knowledge_base_id, key = client_and_knowledge_base
    job_id = client.post("/content", json={"text": "hi"}, headers={"Authorization": f"Bearer {key}"}).json()["job_id"]
    resp = client.get(f"/content/{job_id}", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@requires_pg
def test_post_content_with_nul_in_text_is_sanitized(client_and_knowledge_base) -> None:  # type: ignore[no-untyped-def]
    client, _knowledge_base_id, key = client_and_knowledge_base
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
def test_post_content_with_nul_in_metadata_is_not_500(client_and_knowledge_base) -> None:  # type: ignore[no-untyped-def]
    client, _knowledge_base_id, key = client_and_knowledge_base
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
def test_missing_auth_is_401(client_and_knowledge_base) -> None:  # type: ignore[no-untyped-def]
    client, _knowledge_base_id, _key = client_and_knowledge_base
    assert client.post("/content", json={"text": "x"}).status_code == 401


@pytest.fixture
def two_knowledge_bases():  # type: ignore[no-untyped-def]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import generate_api_key, hash_key
    from db import get_postgres_session
    from models import ApiKey, IngestJob, KnowledgeBase
    from routes_content import router

    app = FastAPI()
    app.include_router(router)

    key_a = generate_api_key()
    key_b = generate_api_key()
    with get_postgres_session() as s:
        knowledge_base_a = KnowledgeBase(name=f"routes-test-a-{uuid.uuid4()}")
        knowledge_base_b = KnowledgeBase(name=f"routes-test-b-{uuid.uuid4()}")
        s.add_all([knowledge_base_a, knowledge_base_b])
        s.flush()
        org_a_id = str(knowledge_base_a.id)
        knowledge_base_b_id = str(knowledge_base_b.id)
        s.add(ApiKey(knowledge_base_id=knowledge_base_a.id, key_hash=hash_key(key_a)))
        s.add(ApiKey(knowledge_base_id=knowledge_base_b.id, key_hash=hash_key(key_b)))
        job_b = IngestJob(
            knowledge_base_id=knowledge_base_b.id, content="knowledge_base b's secret content", job_metadata=None
        )
        s.add(job_b)
        s.flush()
        job_b_id = str(job_b.id)
        s.commit()

    try:
        yield TestClient(app), org_a_id, key_a, knowledge_base_b_id, key_b, job_b_id
    finally:
        with get_postgres_session() as s:
            s.execute(
                sqltext("DELETE FROM api_keys WHERE knowledge_base_id IN (:a, :b)"),
                {"a": org_a_id, "b": knowledge_base_b_id},
            )
            s.execute(
                sqltext("DELETE FROM ingest_jobs WHERE knowledge_base_id IN (:a, :b)"),
                {"a": org_a_id, "b": knowledge_base_b_id},
            )
            s.execute(
                sqltext("DELETE FROM knowledge_bases WHERE id IN (:a, :b)"), {"a": org_a_id, "b": knowledge_base_b_id}
            )
            s.commit()


@requires_pg
def test_other_knowledge_bases_job_is_404(two_knowledge_bases) -> None:  # type: ignore[no-untyped-def]
    client, _knowledge_base_a_id, key_a, _knowledge_base_b_id, key_b, job_b_id = two_knowledge_bases

    # KnowledgeBase A must not be able to see knowledge_base B's real job.
    resp = client.get(f"/content/{job_b_id}", headers={"Authorization": f"Bearer {key_a}"})
    assert resp.status_code == 404
    missing_resp = client.get(f"/content/{uuid.uuid4()}", headers={"Authorization": f"Bearer {key_a}"})
    assert resp.json() == missing_resp.json()

    # Sanity: knowledge_base B can see its own job, proving it really exists and A is specifically blocked.
    own_resp = client.get(f"/content/{job_b_id}", headers={"Authorization": f"Bearer {key_b}"})
    assert own_resp.status_code == 200
    assert own_resp.json()["job_id"] == job_b_id


@requires_pg
def test_malformed_job_id_is_404(client_and_knowledge_base) -> None:  # type: ignore[no-untyped-def]
    client, _knowledge_base_id, key = client_and_knowledge_base
    resp = client.get("/content/not-a-uuid", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
