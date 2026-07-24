import hashlib
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db import get_postgres_session
from models import ApiKey

# Registered so the OpenAPI docs document Bearer auth (Swagger "Authorize" button).
# auto_error=False: a missing/non-Bearer header yields None, and we raise our own 401.
bearer_scheme = HTTPBearer(auto_error=False, description="Your API key, sent as a Bearer token.")


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    # API keys are high-entropy random tokens, so a fast deterministic hash is safe
    # and — unlike a salted password hash — lets us look a key up by its hash.
    return hashlib.sha256(key.encode()).hexdigest()


def resolve_org(token: str | None) -> str:
    """Resolve an API-key token to its org id. Raises 401 if missing, unknown, or revoked."""
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    with get_postgres_session() as session:
        row = (
            session.query(ApiKey).filter(ApiKey.key_hash == hash_key(token), ApiKey.revoked_at.is_(None)).one_or_none()
        )
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    return str(row.org_id)


def require_org(creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> str:  # noqa: B008 — FastAPI dependency idiom
    """FastAPI dependency: the caller's org id, resolved from the Bearer API key."""
    return resolve_org(creds.credentials if creds else None)
