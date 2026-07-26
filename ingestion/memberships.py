"""Knowledge-base membership: who may act on which knowledge base, and at what rank.

Kept out of `accounts.py`, which owns users, sessions and CSRF. This module answers
one question — may this user do this to this knowledge base — and later gains
knowledge-base creation, the other thing that is about membership rather than auth.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session as OrmSession

from models import KnowledgeBaseUser

# Least- to most-privileged. Callers compare ranks rather than roles, which is what
# makes `min_role` a floor ("admin or better") instead of an exact match.
ROLE_RANK: dict[str, int] = {"reader": 0, "editor": 1, "admin": 2, "owner": 3}


def membership_role(session: OrmSession, user_id: Any, kb_id: Any) -> str | None:
    """The caller's role in `kb_id`, or None if they are not a member."""
    row = (
        session.query(KnowledgeBaseUser.role)
        .filter(KnowledgeBaseUser.user_id == user_id, KnowledgeBaseUser.knowledge_base_id == kb_id)
        .one_or_none()
    )
    return str(row[0]) if row is not None else None


def require_membership(session: OrmSession, user_id: Any, kb_id: Any, min_role: str) -> str:
    """The caller's role in `kb_id`, or 404 if they lack `min_role`.

    404 rather than 403, always: a 403 confirms the knowledge base exists to someone
    with no membership in it, and a `knowledge_base_id` property filter is the only
    isolation the graph has. A permission failure, a knowledge base belonging to
    someone else, and a typo are deliberately indistinguishable to the caller.
    """
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge base not found")
    if kb_id is None:
        raise not_found
    try:
        uuid.UUID(str(kb_id))
    except ValueError:
        # A malformed id must not reach the query: Postgres raises on an invalid uuid
        # cast, which would surface as a 500 and reveal that the id was merely malformed.
        raise not_found from None
    role = membership_role(session, user_id, kb_id)
    if role is None or ROLE_RANK.get(role, -1) < ROLE_RANK[min_role]:
        raise not_found
    return role
