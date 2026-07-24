"""Per-org API-key management, session-authenticated (cookie auth via `current_user`).

Keys minted here are ordinary rows in the `api_keys` table used by the engine's Bearer
auth (auth.py: generate_api_key/hash_key/resolve_org) — no separate mechanism, so a
freshly created key authenticates against /content, /config, /graphql immediately."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as OrmSession

from accounts import current_user, require_csrf
from auth import generate_api_key, hash_key
from db import get_postgres_session
from models import ApiKey, OrgUser, User
from schemas import ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyOut

router = APIRouter(prefix="/api/keys", tags=["API keys"], dependencies=[Depends(require_csrf)])

# Chars of the raw key shown back to the caller in the masked listing, e.g. "OaK3f9YbZ1".
KEY_PREFIX_LENGTH = 10


def _home_org_id(session: OrmSession, user_id: Any) -> str | None:
    """The org a user's API keys belong to: the earliest org they were added to
    (registration auto-creates one, so this is normally their only org)."""
    row = session.query(OrgUser.org_id).filter(OrgUser.user_id == user_id).order_by(OrgUser.created_at.asc()).first()
    return str(row[0]) if row is not None else None


@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def create_key(
    payload: ApiKeyCreateRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> ApiKeyCreateResponse:
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify your email to create an API key")
    with get_postgres_session() as session:
        org_id = _home_org_id(session, user.id)
        if org_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no org found for this account")
        raw = generate_api_key()
        key = ApiKey(
            org_id=org_id,
            key_hash=hash_key(raw),
            name=payload.name,
            prefix=raw[:KEY_PREFIX_LENGTH],
            created_by_id=user.id,
        )
        session.add(key)
        session.commit()
        session.refresh(key)
        return ApiKeyCreateResponse(
            id=str(key.id), name=key.name, prefix=key.prefix or "", key=raw, created_at=key.created_at
        )


@router.get("", response_model=list[ApiKeyOut])
def list_keys(user: User = Depends(current_user)) -> list[ApiKeyOut]:  # noqa: B008 — FastAPI dependency idiom
    with get_postgres_session() as session:
        org_id = _home_org_id(session, user.id)
        if org_id is None:
            return []
        rows = session.query(ApiKey).filter(ApiKey.org_id == org_id).order_by(ApiKey.created_at.desc()).all()
        return [
            ApiKeyOut(
                id=str(k.id),
                name=k.name,
                prefix=k.prefix,
                created_at=k.created_at,
                last_used_at=k.last_used_at,
                revoked_at=k.revoked_at,
            )
            for k in rows
        ]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(key_id: str, user: User = Depends(current_user)) -> None:  # noqa: B008 — FastAPI dependency idiom
    try:
        uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found") from None
    with get_postgres_session() as session:
        org_id = _home_org_id(session, user.id)
        key = session.get(ApiKey, key_id) if org_id is not None else None
        if key is None or str(key.org_id) != org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        key.revoked_at = datetime.now(UTC)
        session.commit()
