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
    from routes_config import router

    app = FastAPI()
    app.include_router(router)

    key = generate_api_key()
    with get_postgres_session() as s:
        knowledge_base = KnowledgeBase(name=f"config-test-{uuid.uuid4()}")
        s.add(knowledge_base)
        s.flush()
        knowledge_base_id = str(knowledge_base.id)
        s.add(ApiKey(knowledge_base_id=knowledge_base.id, key_hash=hash_key(key)))
        s.commit()

    yield TestClient(app), knowledge_base_id, key

    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM api_keys WHERE knowledge_base_id = :o"), {"o": knowledge_base_id})
        s.execute(sqltext("DELETE FROM knowledge_base_configs WHERE knowledge_base_id = :o"), {"o": knowledge_base_id})
        s.execute(sqltext("DELETE FROM knowledge_bases WHERE id = :o"), {"o": knowledge_base_id})
        s.commit()


@requires_pg
def test_put_config_creates_then_updates(client_and_knowledge_base) -> None:  # type: ignore[no-untyped-def]
    client, knowledge_base_id, key = client_and_knowledge_base
    headers = {"Authorization": f"Bearer {key}"}

    body1 = {
        "interests": "p1",
        "discover_types": True,
        "entity_types": [{"name": "Person", "description": "a human"}],
        "relationship_types": [{"name": "KNOWS", "description": ""}],
    }
    r1 = client.put("/config", json=body1, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["entity_types"] == [{"name": "Person", "description": "a human", "pinned": False, "banned": False}]

    body2 = {
        "interests": "p2",
        "discover_types": False,
        "entity_types": [{"name": "Person", "description": ""}, {"name": "Organization", "description": ""}],
        "relationship_types": [{"name": "KNOWS", "description": ""}, {"name": "WORKS_AT", "description": ""}],
    }
    r2 = client.put("/config", json=body2, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["interests"] == "p2"
    assert r2.json()["discover_types"] is False
    assert r2.json()["relationship_types"] == [
        {"name": "KNOWS", "description": "", "pinned": False, "banned": False},
        {"name": "WORKS_AT", "description": "", "pinned": False, "banned": False},
    ]

    from db import get_postgres_session
    from models import KnowledgeBaseConfig

    with get_postgres_session() as s:
        rows = s.query(KnowledgeBaseConfig).filter(KnowledgeBaseConfig.knowledge_base_id == knowledge_base_id).all()
    assert len(rows) == 1  # upsert, not insert-twice


@requires_pg
def test_put_config_preserves_pinned_and_banned_flags(client_and_knowledge_base) -> None:  # type: ignore[no-untyped-def]
    client, _knowledge_base_id, key = client_and_knowledge_base
    headers = {"Authorization": f"Bearer {key}"}

    body = {
        "interests": "p1",
        "discover_types": True,
        "entity_types": [
            {"name": "Person", "description": "a human", "pinned": True, "banned": False},
            {"name": "Spam", "description": "not relevant", "pinned": False, "banned": True},
        ],
        "relationship_types": [],
    }
    put = client.put("/config", json=body, headers=headers)
    assert put.status_code == 200
    assert put.json()["entity_types"] == [
        {"name": "Person", "description": "a human", "pinned": True, "banned": False},
        {"name": "Spam", "description": "not relevant", "pinned": False, "banned": True},
    ]


@requires_pg
def test_put_config_requires_auth(client_and_knowledge_base) -> None:  # type: ignore[no-untyped-def]
    client, _knowledge_base_id, _key = client_and_knowledge_base
    body = {"interests": "p", "discover_types": True, "entity_types": [], "relationship_types": []}
    assert client.put("/config", json=body).status_code == 401
