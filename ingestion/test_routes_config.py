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
    from routes_config import router

    app = FastAPI()
    app.include_router(router)

    key = generate_api_key()
    with get_postgres_session() as s:
        org = Org(name=f"config-test-{uuid.uuid4()}")
        s.add(org)
        s.flush()
        org_id = str(org.id)
        s.add(ApiKey(org_id=org.id, key_hash=hash_key(key)))
        s.commit()

    yield TestClient(app), org_id, key

    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM api_keys WHERE org_id = :o"), {"o": org_id})
        s.execute(sqltext("DELETE FROM org_configs WHERE org_id = :o"), {"o": org_id})
        s.execute(sqltext("DELETE FROM orgs WHERE id = :o"), {"o": org_id})
        s.commit()


@requires_pg
def test_put_config_creates_then_updates(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, org_id, key = client_and_org
    headers = {"Authorization": f"Bearer {key}"}

    body1 = {"relevance_prompt": "p1", "entity_types": ["Person"], "relationship_types": ["KNOWS"]}
    r1 = client.put("/config", json=body1, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["entity_types"] == ["Person"]

    body2 = {"relevance_prompt": "p2", "entity_types": ["Person", "Org"], "relationship_types": ["KNOWS", "WORKS_AT"]}
    r2 = client.put("/config", json=body2, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["relevance_prompt"] == "p2"
    assert r2.json()["relationship_types"] == ["KNOWS", "WORKS_AT"]

    from db import get_postgres_session
    from models import OrgConfig

    with get_postgres_session() as s:
        rows = s.query(OrgConfig).filter(OrgConfig.org_id == org_id).all()
    assert len(rows) == 1  # upsert, not insert-twice


@requires_pg
def test_put_config_requires_auth(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, _key = client_and_org
    body = {"relevance_prompt": "p", "entity_types": [], "relationship_types": []}
    assert client.put("/config", json=body).status_code == 401
