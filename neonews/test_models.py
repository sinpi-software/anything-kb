import os

os.environ.setdefault("NEONEWS_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from db import get_postgres_session
from models import Item, Source


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as s:
            s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@requires_postgres
def test_migration_creates_neonews_tables() -> None:
    with get_postgres_session() as s:
        names = set(inspect(s.get_bind()).get_table_names())
    assert {"neonews_sources", "neonews_items", "neonews_issues", "neonews_job_state"} <= names


@requires_postgres
def test_migration_uses_its_own_alembic_version_table() -> None:
    """The engine's chain owns `alembic_version`; ours must not stamp over it."""
    with get_postgres_session() as s:
        names = set(inspect(s.get_bind()).get_table_names())
    assert "alembic_version_neonews" in names


@requires_postgres
def test_item_dedup_key_is_unique_per_source() -> None:
    with get_postgres_session() as s:
        source = Source(kind="rss", locator=f"https://example.com/{os.urandom(4).hex()}.xml")
        s.add(source)
        s.flush()
        s.add(Item(source_id=source.id, dedup_key="same"))
        s.flush()
        s.add(Item(source_id=source.id, dedup_key="same"))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
