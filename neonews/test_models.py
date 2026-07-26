import os

import config

# Point at the test suite's own database (config.POSTGRES_TEST_URL_ENV / _DEFAULT), by
# assignment rather than setdefault: the `import config` just above already ran its
# repo-root .env loading, and .env.sample sets NEONEWS_POSTGRES_URL to the operator's
# live database. Plain assignment (not setdefault) unconditionally overwrites whatever
# dotenv set, so the test suite can never end up pointed at the live database — the
# semantics of `=` vs `setdefault` are what protect this, not import order.
os.environ["NEONEWS_POSTGRES_URL"] = os.environ.get(config.POSTGRES_TEST_URL_ENV, config.POSTGRES_TEST_URL_DEFAULT)

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from db import get_postgres_session
from models import Item, Source


def _require_test_postgres() -> None:
    """Every test in this module needs neonews_test. A missing/unmigrated database
    must FAIL the suite, not silently skip it — a skip here means the one-time setup
    documented in README.md was never done, not that the environment legitimately
    lacks Postgres. Reported as a clean pass, that's exactly the failure mode this
    project has fought all the way through."""
    try:
        with get_postgres_session() as s:
            s.execute(text("SELECT 1"))
    except Exception as exc:
        raise RuntimeError(
            f"neonews_test is not reachable at NEONEWS_POSTGRES_URL={os.environ.get('NEONEWS_POSTGRES_URL')!r}. "
            "Create and migrate it once:\n"
            "  createdb -U ingestion -h localhost neonews_test\n"
            "  NEONEWS_TEST_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/neonews_test "
            "uv run alembic upgrade head"
        ) from exc


_require_test_postgres()


def test_migration_creates_neonews_tables() -> None:
    with get_postgres_session() as s:
        names = set(inspect(s.get_bind()).get_table_names())
    assert {"neonews_sources", "neonews_items", "neonews_issues", "neonews_job_state"} <= names


def test_migration_uses_its_own_alembic_version_table() -> None:
    """The engine's chain owns `alembic_version`; ours must not stamp over it."""
    with get_postgres_session() as s:
        names = set(inspect(s.get_bind()).get_table_names())
    assert "alembic_version_neonews" in names


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
