import os
import uuid

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from fastapi import HTTPException
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


def _make_kb_with_role(role: str) -> tuple[str, str]:
    """A throwaway user and knowledge base with that user at `role`. Returns (user_id, kb_id)."""
    from db import get_postgres_session
    from models import KnowledgeBase, KnowledgeBaseUser, User

    with get_postgres_session() as s:
        user = User(email=f"memb-{uuid.uuid4()}@example.com", password_hash="x", name="t")
        s.add(user)
        s.flush()
        kb = KnowledgeBase(name=f"kb-{uuid.uuid4()}", created_by_id=user.id, updated_by_id=user.id)
        s.add(kb)
        s.flush()
        s.add(
            KnowledgeBaseUser(
                knowledge_base_id=kb.id, user_id=user.id, role=role, created_by_id=user.id, updated_by_id=user.id
            )
        )
        s.commit()
        return str(user.id), str(kb.id)


def _purge(user_id: str, kb_id: str) -> None:
    from db import get_postgres_session
    from models import KnowledgeBase, KnowledgeBaseUser, User

    with get_postgres_session() as s:
        s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.knowledge_base_id == kb_id).delete(
            synchronize_session=False
        )
        s.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).delete(synchronize_session=False)
        s.query(User).filter(User.id == user_id).delete(synchronize_session=False)
        s.commit()


def test_rank_order_is_reader_editor_admin_owner() -> None:
    from memberships import ROLE_RANK

    assert ROLE_RANK == {"reader": 0, "editor": 1, "admin": 2, "owner": 3}


@requires_pg
def test_role_at_or_above_the_floor_is_allowed() -> None:
    from db import get_postgres_session
    from memberships import require_membership

    user_id, kb_id = _make_kb_with_role("admin")
    try:
        with get_postgres_session() as s:
            assert require_membership(s, user_id, kb_id, "reader") == "admin"
            assert require_membership(s, user_id, kb_id, "admin") == "admin"
    finally:
        _purge(user_id, kb_id)


@requires_pg
def test_role_below_the_floor_is_404_not_403() -> None:
    """404 rather than 403: a 403 confirms the knowledge base exists to someone who
    may not see it, and tenancy is the only isolation the graph has."""
    from db import get_postgres_session
    from memberships import require_membership

    user_id, kb_id = _make_kb_with_role("editor")
    try:
        with get_postgres_session() as s, pytest.raises(HTTPException) as excinfo:
            require_membership(s, user_id, kb_id, "admin")
        assert excinfo.value.status_code == 404
    finally:
        _purge(user_id, kb_id)


@requires_pg
def test_non_member_is_404() -> None:
    from db import get_postgres_session
    from memberships import require_membership

    user_id, kb_id = _make_kb_with_role("owner")
    other_user_id, other_kb_id = _make_kb_with_role("owner")
    try:
        with get_postgres_session() as s, pytest.raises(HTTPException) as excinfo:
            require_membership(s, other_user_id, kb_id, "reader")
        assert excinfo.value.status_code == 404
    finally:
        _purge(user_id, kb_id)
        _purge(other_user_id, other_kb_id)


@requires_pg
def test_malformed_and_missing_ids_are_404_not_a_database_error() -> None:
    """A non-UUID id must not reach the query — SQLAlchemy would raise a DataError,
    which surfaces as a 500 and leaks that the id was merely malformed."""
    from db import get_postgres_session
    from memberships import require_membership

    user_id, kb_id = _make_kb_with_role("owner")
    try:
        with get_postgres_session() as s:
            for bad in ["not-a-uuid", None, str(uuid.uuid4())]:
                with pytest.raises(HTTPException) as excinfo:
                    require_membership(s, user_id, bad, "reader")
                assert excinfo.value.status_code == 404
    finally:
        _purge(user_id, kb_id)
