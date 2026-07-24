import os

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import text as sqlalchemy_text

from auth import generate_api_key, hash_key, require_org
from db import get_postgres_session
from models import ApiKey, Org


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


def test_require_org_rejects_missing_token() -> None:
    with pytest.raises(HTTPException) as exc:
        require_org(authorization="")
    assert exc.value.status_code == 401


def test_require_org_rejects_non_bearer() -> None:
    with pytest.raises(HTTPException) as exc:
        require_org(authorization="Basic abc")
    assert exc.value.status_code == 401


@requires_postgres
def test_require_org_rejects_unknown_key() -> None:
    with pytest.raises(HTTPException) as exc:
        require_org(authorization=f"Bearer {generate_api_key()}")
    assert exc.value.status_code == 401


@pytest.fixture
def seeded_org() -> Iterator[Org]:
    with get_postgres_session() as session:
        org = Org(name=f"test-org-{uuid.uuid4()}")
        session.add(org)
        session.commit()
        session.refresh(org)
        yield org
        session.query(ApiKey).filter(ApiKey.org_id == org.id).delete()
        session.query(Org).filter(Org.id == org.id).delete()
        session.commit()


@requires_postgres
def test_require_org_resolves_valid_key_to_org_id(seeded_org: Org) -> None:
    key = generate_api_key()
    with get_postgres_session() as session:
        session.add(ApiKey(org_id=seeded_org.id, key_hash=hash_key(key)))
        session.commit()

    assert require_org(authorization=f"Bearer {key}") == str(seeded_org.id)


@requires_postgres
def test_require_org_rejects_revoked_key(seeded_org: Org) -> None:
    key = generate_api_key()
    with get_postgres_session() as session:
        session.add(ApiKey(org_id=seeded_org.id, key_hash=hash_key(key), revoked_at=datetime.now(UTC)))
        session.commit()

    with pytest.raises(HTTPException) as exc:
        require_org(authorization=f"Bearer {key}")
    assert exc.value.status_code == 401
