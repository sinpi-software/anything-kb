"""Knowledge-base CRUD, session-authenticated (cookie auth via `current_user`).

Membership decides visibility and rank decides capability — see memberships.py. Every
refusal here is a 404, including one caused by role rather than existence.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as OrmSession

from accounts import current_user, require_csrf
from db import get_postgres_session
from memberships import create_knowledge_base, require_membership
from models import ApiKey, IngestJob, KnowledgeBase, KnowledgeBaseConfig, KnowledgeBaseUser, User
from neo4j_client import purge_knowledge_base
from sanitize import sanitize
from schemas import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseDeleteRequest,
    KnowledgeBaseOut,
    KnowledgeBaseUpdateRequest,
)

router = APIRouter(prefix="/api/knowledge-bases", tags=["Knowledge bases"], dependencies=[Depends(require_csrf)])


@router.get("", response_model=list[KnowledgeBaseOut])
def list_knowledge_bases(user: User = Depends(current_user)) -> list[KnowledgeBaseOut]:  # noqa: B008
    """The caller's knowledge bases, oldest first.

    Not role-gated: it filters the caller's own memberships, so it already returns
    exactly what they may see.
    """
    with get_postgres_session() as session:
        rows = (
            session.query(KnowledgeBase, KnowledgeBaseUser.role)
            .join(KnowledgeBaseUser, KnowledgeBaseUser.knowledge_base_id == KnowledgeBase.id)
            .filter(KnowledgeBaseUser.user_id == user.id)
            .order_by(KnowledgeBase.created_at.asc())
            .all()
        )
        return [
            KnowledgeBaseOut(
                id=str(kb.id), name=kb.name, charter=kb.charter, role=str(role), created_at=kb.created_at
            )
            for kb, role in rows
        ]


@router.post("", response_model=KnowledgeBaseOut, status_code=status.HTTP_201_CREATED)
def create(
    payload: KnowledgeBaseCreateRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> KnowledgeBaseOut:
    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="verify your email to create a knowledge base"
        )
    name = sanitize(payload.name).strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name is required")
    charter = sanitize(payload.charter).strip() if payload.charter else None
    with get_postgres_session() as session:
        kb = create_knowledge_base(session, user, name, charter)
        session.commit()
        session.refresh(kb)
        return KnowledgeBaseOut(
            id=str(kb.id), name=kb.name, charter=kb.charter, role="owner", created_at=kb.created_at
        )


@router.patch("/{kb_id}", response_model=KnowledgeBaseOut)
def update(
    kb_id: str,
    payload: KnowledgeBaseUpdateRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> KnowledgeBaseOut:
    with get_postgres_session() as session:
        role = require_membership(session, user.id, kb_id, "owner")
        kb = session.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge base not found")
        if payload.name is not None:
            name = sanitize(payload.name).strip()
            if not name:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name is required")
            kb.name = name
        if payload.charter is not None:
            kb.charter = sanitize(payload.charter).strip() or None
        kb.updated_by_id = user.id
        session.commit()
        session.refresh(kb)
        return KnowledgeBaseOut(
            id=str(kb.id), name=kb.name, charter=kb.charter, role=role, created_at=kb.created_at
        )


def _delete_postgres_rows(session: OrmSession, kb_id: str) -> None:
    """Every child row, then the knowledge base itself.

    Explicit because no knowledge_base_id foreign key declares ON DELETE CASCADE —
    only the users.id keys on sessions and email_tokens do. Named as a seam so a test
    can simulate the graph-succeeded / Postgres-failed split.
    """
    for model in (IngestJob, ApiKey, KnowledgeBaseConfig, KnowledgeBaseUser):
        session.query(model).filter(model.knowledge_base_id == kb_id).delete(synchronize_session=False)
    session.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).delete(synchronize_session=False)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    kb_id: str,
    payload: KnowledgeBaseDeleteRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> None:
    """Permanently delete a knowledge base: its graph, then its rows.

    The graph goes first deliberately. The two stores cannot be made atomic, and a
    failure between them leaves a knowledge base that is empty but still listed and
    still deletable — re-running converges. The reverse order strands graph nodes
    whose owning row is gone: invisible to every query and reclaimable by nothing.
    """
    with get_postgres_session() as session:
        require_membership(session, user.id, kb_id, "owner")
        # Canonicalize after the authorization check: Postgres matches uppercase and
        # dash-less UUID forms on cast, but purge_knowledge_base does an exact Cypher
        # string comparison against the canonical lowercase-dashed form nodes carry. A
        # non-canonical id here would purge 0 graph nodes and then delete every
        # Postgres row anyway — stranding the graph permanently.
        kb_id = str(uuid.UUID(kb_id))
        kb = session.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge base not found")
        if payload.confirm_name != kb.name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="confirm_name must match the knowledge base name exactly",
            )
        remaining = (
            session.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).count()
        )
        if remaining <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="cannot delete your only knowledge base"
            )

    purge_knowledge_base(kb_id)

    with get_postgres_session() as session:
        _delete_postgres_rows(session, kb_id)
        session.commit()
