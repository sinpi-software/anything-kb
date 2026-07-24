import os

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import text as sqlalchemy_text

from auth import generate_api_key, hash_key, require_knowledge_base, resolve_knowledge_base
from db import get_postgres_session
from models import ApiKey, KnowledgeBase


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as session:
            session.execute(sqlalchemy_text("SELECT 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


def test_generate_api_key_is_random_and_urlsafe() -> None:
    a, b = generate_api_key(), generate_api_key()
    assert a != b
    assert len(a) >= 32
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_key_is_deterministic_and_hex() -> None:
    key = "some-key"
    assert hash_key(key) == hash_key(key)
    assert len(hash_key(key)) == 64
    assert hash_key(key) != hash_key("other-key")


def test_resolve_knowledge_base_rejects_missing_token() -> None:
    for missing in (None, ""):
        with pytest.raises(HTTPException) as exc:
            resolve_knowledge_base(missing)
        assert exc.value.status_code == 401


def test_require_knowledge_base_dependency_rejects_no_credentials() -> None:
    # HTTPBearer yields None for a missing / non-Bearer header; the dependency 401s.
    with pytest.raises(HTTPException) as exc:
        require_knowledge_base(creds=None)
    assert exc.value.status_code == 401


@requires_postgres
def test_resolve_knowledge_base_rejects_unknown_key() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_knowledge_base(generate_api_key())
    assert exc.value.status_code == 401


@pytest.fixture
def seeded_knowledge_base() -> Iterator[KnowledgeBase]:
    with get_postgres_session() as session:
        knowledge_base = KnowledgeBase(name=f"test-knowledge_base-{uuid.uuid4()}")
        session.add(knowledge_base)
        session.commit()
        session.refresh(knowledge_base)
        yield knowledge_base
        session.query(ApiKey).filter(ApiKey.knowledge_base_id == knowledge_base.id).delete()
        session.query(KnowledgeBase).filter(KnowledgeBase.id == knowledge_base.id).delete()
        session.commit()


@requires_postgres
def test_resolves_valid_key_to_knowledge_base_id(seeded_knowledge_base: KnowledgeBase) -> None:
    key = generate_api_key()
    with get_postgres_session() as session:
        session.add(ApiKey(knowledge_base_id=seeded_knowledge_base.id, key_hash=hash_key(key)))
        session.commit()

    assert resolve_knowledge_base(key) == str(seeded_knowledge_base.id)
    # and through the FastAPI dependency, with real Bearer credentials
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=key)
    assert require_knowledge_base(creds=creds) == str(seeded_knowledge_base.id)


@requires_postgres
def test_resolve_knowledge_base_rejects_revoked_key(seeded_knowledge_base: KnowledgeBase) -> None:
    key = generate_api_key()
    with get_postgres_session() as session:
        session.add(
            ApiKey(knowledge_base_id=seeded_knowledge_base.id, key_hash=hash_key(key), revoked_at=datetime.now(UTC))
        )
        session.commit()

    with pytest.raises(HTTPException) as exc:
        resolve_knowledge_base(key)
    assert exc.value.status_code == 401
