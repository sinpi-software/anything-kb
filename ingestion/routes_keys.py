"""Per-knowledge_base API-key management, session-authenticated (cookie auth via `current_user`).

Keys minted here are ordinary rows in the `api_keys` table used by the engine's Bearer
auth (auth.py: generate_api_key/hash_key/resolve_knowledge_base) — no separate mechanism, so a
freshly created key authenticates against /content, /config, /graphql immediately."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as OrmSession

from accounts import current_user, home_knowledge_base_id, require_csrf
from auth import generate_api_key, hash_key
from db import get_postgres_session
from memberships import require_membership
from models import ApiKey, User
from schemas import ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyOut

router = APIRouter(prefix="/api/keys", tags=["API keys"], dependencies=[Depends(require_csrf)])
scoped_router = APIRouter(prefix="/api/knowledge-bases", tags=["API keys"], dependencies=[Depends(require_csrf)])

# Chars of the raw key shown back to the caller in the masked listing, e.g. "OaK3f9YbZ1".
KEY_PREFIX_LENGTH = 10


def _create_key(session: OrmSession, kb_id: str, user: User, payload: ApiKeyCreateRequest) -> ApiKeyCreateResponse:
    raw = generate_api_key()
    key = ApiKey(
        knowledge_base_id=kb_id,
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


@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def create_key(
    payload: ApiKeyCreateRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> ApiKeyCreateResponse:
    """Legacy: the knowledge base is implied. Sub-project B removes this."""
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify your email to create an API key")
    with get_postgres_session() as session:
        kb_id = home_knowledge_base_id(session, user.id)
        require_membership(session, user.id, kb_id, "admin")
        assert kb_id is not None  # require_membership already 404s a None kb_id
        return _create_key(session, kb_id, user, payload)


@scoped_router.post("/{kb_id}/keys", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def create_key_scoped(
    kb_id: str,
    payload: ApiKeyCreateRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> ApiKeyCreateResponse:
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify your email to create an API key")
    with get_postgres_session() as session:
        require_membership(session, user.id, kb_id, "admin")
        return _create_key(session, kb_id, user, payload)


def _list_keys(session: OrmSession, kb_id: str) -> list[ApiKeyOut]:
    rows = (
        session.query(ApiKey)
        .filter(ApiKey.knowledge_base_id == kb_id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
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


@router.get("", response_model=list[ApiKeyOut])
def list_keys(user: User = Depends(current_user)) -> list[ApiKeyOut]:  # noqa: B008 — FastAPI dependency idiom
    """Legacy: the knowledge base is implied. Sub-project B removes this."""
    with get_postgres_session() as session:
        kb_id = home_knowledge_base_id(session, user.id)
        require_membership(session, user.id, kb_id, "admin")
        assert kb_id is not None  # require_membership already 404s a None kb_id
        return _list_keys(session, kb_id)


@scoped_router.get("/{kb_id}/keys", response_model=list[ApiKeyOut])
def list_keys_scoped(kb_id: str, user: User = Depends(current_user)) -> list[ApiKeyOut]:  # noqa: B008
    with get_postgres_session() as session:
        require_membership(session, user.id, kb_id, "admin")
        return _list_keys(session, kb_id)


def _revoke_key(session: OrmSession, kb_id: str, key_id: str) -> None:
    try:
        uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found") from None
    key = session.get(ApiKey, key_id)
    if key is None or str(key.knowledge_base_id) != kb_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    key.revoked_at = datetime.now(UTC)
    session.commit()


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(key_id: str, user: User = Depends(current_user)) -> None:  # noqa: B008 — FastAPI dependency idiom
    """Legacy: the knowledge base is implied. Sub-project B removes this."""
    with get_postgres_session() as session:
        kb_id = home_knowledge_base_id(session, user.id)
        require_membership(session, user.id, kb_id, "admin")
        assert kb_id is not None  # require_membership already 404s a None kb_id
        _revoke_key(session, kb_id, key_id)


@scoped_router.delete("/{kb_id}/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key_scoped(kb_id: str, key_id: str, user: User = Depends(current_user)) -> None:  # noqa: B008
    with get_postgres_session() as session:
        require_membership(session, user.id, kb_id, "admin")
        _revoke_key(session, kb_id, key_id)
