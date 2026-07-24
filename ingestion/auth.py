import hashlib
import secrets

from fastapi import Header, HTTPException, status

from db import get_postgres_session
from models import ApiKey


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    # API keys are high-entropy random tokens, so a fast deterministic hash is safe
    # and — unlike a salted password hash — lets us look a key up by its hash.
    return hashlib.sha256(key.encode()).hexdigest()


def require_org(authorization: str = Header(default="")) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    with get_postgres_session() as session:
        row = (
            session.query(ApiKey)
            .filter(ApiKey.key_hash == hash_key(token), ApiKey.revoked_at.is_(None))
            .one_or_none()
        )
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    return str(row.org_id)
