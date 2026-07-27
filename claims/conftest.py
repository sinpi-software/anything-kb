"""Test-suite setup, imported by pytest before any test module."""

import os

import config

# Point at the test suite's own database, by assignment rather than setdefault: the
# `import config` above already ran its repo-root .env loading, and .env.sample sets
# CLAIMS_POSTGRES_URL to the operator's live database. Plain assignment unconditionally
# overwrites whatever dotenv set, so the suite can never end up on the live database —
# the semantics of `=` vs `setdefault` are what protect this, not import order.
os.environ[config.POSTGRES_URL_ENV] = os.environ.get(config.POSTGRES_TEST_URL_ENV, config.POSTGRES_TEST_URL_DEFAULT)

from sqlalchemy import text

from db import get_postgres_session


def _require_test_postgres() -> None:
    """Every Postgres-backed test needs claims_test. A missing or unmigrated database
    must FAIL the suite, not silently skip it — a skip here means the one-time setup in
    README.md was never done, and reported as a clean pass that is the worst outcome."""
    try:
        with get_postgres_session() as s:
            s.execute(text("SELECT 1 FROM claims_documents LIMIT 1"))
    except Exception as exc:
        raise RuntimeError(
            f"claims_test is not reachable at {config.POSTGRES_URL_ENV}="
            f"{os.environ.get(config.POSTGRES_URL_ENV)!r}. Create and migrate it once:\n"
            "  createdb -U ingestion -h localhost claims_test\n"
            "  CLAIMS_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/claims_test "
            "uv run alembic upgrade head"
        ) from exc


_require_test_postgres()
