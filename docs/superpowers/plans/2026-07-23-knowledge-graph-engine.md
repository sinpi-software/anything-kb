# Knowledge Graph Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-architect `ingestion/` into a single-purpose, multi-tenant knowledge-graph engine: authenticated async `POST /content` → per-org binary relevance filter → typed entity/relationship extraction constrained to the org's configured types → merge into Neo4j → read the graph over a generic GraphQL API.

**Architecture:** FastAPI serves the HTTP surface (`POST /content`, `GET /content/{job_id}`, `PUT /config`, `POST /graphql`). `POST /content` durably enqueues a Postgres `ingest_jobs` row and returns `202 {job_id}`. A standalone `worker.py` process claims pending rows with `SELECT ... FOR UPDATE SKIP LOCKED`, runs the relevance filter, and (if relevant) runs the kept-and-adapted `knowledge.py` extraction/resolution/merge into Neo4j. Strawberry resolvers read Neo4j, every query scoped by the caller's `org_id`. Prefect, RSS, transforms, and gates are deleted.

**Tech Stack:** Python 3.12, FastAPI + uvicorn, Strawberry GraphQL, SQLAlchemy 2.0 + Alembic (Postgres), the `neo4j` driver, OpenRouter (LLM), `argon2-cffi`/`hashlib` for auth, pytest.

## Global Constraints

Every task's requirements implicitly include this section.

- **Ruff** line-length 120, target `py312`, lint select `["E","F","I","UP","B","SIM","C4","RUF"]`; run `uv run ruff check .` and `uv run ruff format .`. `alembic/versions` is excluded.
- **Mypy** strict, `python_version = 3.12`; run `uv run mypy .`. `alembic/versions/` and `.venv/` excluded.
- **Tests** via `uv run pytest`. Tests live alongside modules as `test_<module>.py` (existing convention — no `tests/` dir). Neo4j/Postgres-backed tests set env vars in a top-of-file preamble and self-skip when the store is unreachable (see the existing `test_knowledge.py` preamble + `requires_neo4j` / `requires_neo4j_and_postgres` markers). Reuse that exact preamble in new integration test files.
- **Tooling** is always invoked through `uv run ...`.
- **Everything is org-scoped.** Every Postgres row, every Neo4j node/edge, and every Neo4j query carries/filters `org_id`. No cross-org read or write.
- **API-key auth on every HTTP endpoint.** `Authorization: Bearer <key>` → hash → `api_keys` row → `org_id`. Missing/invalid/revoked ⇒ `401`. GraphQL is mounted behind the same dependency.
- **No Prefect.** No `prefect` import, deployment, `serve`, event trigger, or concurrency pool anywhere. LLM concurrency is bounded by an in-process `threading.Semaphore`.
- Dev-only database: dropping the old tables in the migration is acceptable; there is nothing to preserve.

---

## File Structure

**Created:**
- `ingestion/auth.py` — API-key generation, hashing (SHA-256 lookup hash), and the `require_org` FastAPI dependency resolving a bearer token to an `org_id`.
- `ingestion/schemas.py` — Pydantic request/response models for the HTTP endpoints.
- `ingestion/routes_content.py` — `POST /content` + `GET /content/{job_id}` router.
- `ingestion/routes_config.py` — `PUT /config` router (upsert `OrgConfig`).
- `ingestion/relevance.py` — the one-call binary relevance filter (`judge_relevance`); raises `RelevanceError` on empty/unparseable output so the worker retries rather than silently dropping content.
- `ingestion/graph_read.py` — Neo4j read helpers (`query_nodes`, `query_node`, `query_edges`) for the GraphQL resolvers.
- `ingestion/graph_api.py` — Strawberry schema (`Query`, `Node`, `Edge`) + `GraphQLRouter`, org-scoped via context.
- `ingestion/worker.py` — standalone worker loop: claim → relevance → extract/merge → status transitions → bounded retry.
- `ingestion/alembic/versions/a1b2c3d4e5f6_engine_reset.py` — migration dropping old tables and creating `org_configs`, `api_keys`, `ingest_jobs`.
- Test files: `test_auth.py`, `test_routes_content.py`, `test_routes_config.py`, `test_relevance.py`, `test_worker.py`, `test_graph_api.py`.

**Modified:**
- `ingestion/models.py` — delete transform/rss/artifact models + their enums; add `OrgConfig`, `ApiKey`, `IngestJob`, `JobStatus`.
- `ingestion/knowledge.py` — drop Prefect + `Transformation`/`Artifact` coupling; replace the concurrency gate with a semaphore; add relationship-type constraint; replace `run_knowledge_transform` with `merge_content(org_id, content, entity_types, relationship_types, job_id)`; provenance references the job id.
- `ingestion/config.py` — trim to LLM + Neo4j + worker settings.
- `ingestion/main.py` — FastAPI app assembling the routers + GraphQL, bootstrapping Neo4j schema on startup.
- `ingestion/seed.py` — seed org + `OrgConfig` + one printed API key.
- `ingestion/pyproject.toml` — add uvicorn + strawberry (+ httpx dev); remove prefect + dead rss deps; refresh isort/mypy config.
- `ingestion/test_knowledge.py` — drop deleted-symbol tests; adapt to the new extraction/merge signatures.
- `ingestion/README.md` — run docs.

**Deleted:**
- `ingestion/transformations.py`, `ingestion/gates.py`, `ingestion/rss_feeds.py`, `ingestion/events.py`, `ingestion/test_gates.py`, `ingestion/test_transformations.py`.
- The React frontend at `app/` (Task 12).

**Left as-is (unrelated, not in the way):** `db.py`, `neo4j_client.py` (extended with reads only in Task 10 via `graph_read.py`; the connection layer is untouched), `sanitize.py` + `test_sanitize.py`, `alembic.ini`, `alembic/env.py`, `models.py`'s `User`/`Org`/`OrgUser`/`OrgSettings`/`AppSettings`/`WikiPage`/`WikiPageVersion`/`OrgUserRole`, `docker-compose.yml`.

---

## Task 1: Demolition — remove transforms, gates, RSS, Prefect

Delete the dead subsystems and everything that imports them, leaving a reduced but green package. No new behaviour.

**Files:**
- Delete: `ingestion/transformations.py`, `ingestion/gates.py`, `ingestion/rss_feeds.py`, `ingestion/events.py`, `ingestion/test_gates.py`, `ingestion/test_transformations.py`
- Modify: `ingestion/models.py`, `ingestion/config.py`, `ingestion/knowledge.py`, `ingestion/main.py`, `ingestion/seed.py`, `ingestion/test_knowledge.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config.LLM_MODEL: str`, `config.LLM_CONCURRENCY: int`, `config.LLM_TIMEOUT_MS: int`, `config.OPENROUTER_API_KEY_ENV: str`, `config.KNOWLEDGE_RESOLUTION_CANDIDATES: int`, `config.NEO4J_URI_ENV/NEO4J_USER_ENV/NEO4J_PASSWORD_ENV: str`, `config.WORKER_BATCH_SIZE/WORKER_MAX_ATTEMPTS: int`, `config.WORKER_POLL_INTERVAL_SECONDS: float`. `knowledge.py` retains `normalize_name`, `escape_lucene`, `_chat`, `_strict_schema`, `build_extraction_messages`, `extract_knowledge`, `candidate_query`, `fulltext_candidate_query`, `resolve_entities_batch`, `merge_summary`, `upsert_entity`, `write_relationship`, `write_provenance` (signatures unchanged this task except the concurrency gate) and no longer defines `run_knowledge_transform`.

- [ ] **Step 1: Delete the dead modules and their tests**

```bash
cd ingestion
git rm transformations.py gates.py rss_feeds.py events.py test_gates.py test_transformations.py
```

- [ ] **Step 2: Trim `config.py` to the surviving settings**

Replace the entire file with:

```python
"""Central configuration. Every tunable lives here or in a referenced env var."""

# --- LLM (OpenRouter) ---
OPENROUTER_API_KEY_ENV = "INGESTION_OPENROUTER_API_KEY"
# Default model for relevance judging and knowledge extraction.
LLM_MODEL = "openai/gpt-5-nano"
# Per-request timeout. Without it a stuck reasoning model hangs the worker forever.
LLM_TIMEOUT_MS = 90_000
# Max concurrent OpenRouter calls, enforced by a threading.Semaphore in knowledge._chat.
LLM_CONCURRENCY = 5
# How many existing entities to offer the LLM as resolution candidates.
KNOWLEDGE_RESOLUTION_CANDIDATES = 5

# --- Neo4j ---
NEO4J_URI_ENV = "INGESTION_NEO4J_URI"
NEO4J_USER_ENV = "INGESTION_NEO4J_USER"
NEO4J_PASSWORD_ENV = "INGESTION_NEO4J_PASSWORD"

# --- Worker ---
# Jobs claimed per loop iteration (FOR UPDATE SKIP LOCKED batch size).
WORKER_BATCH_SIZE = 5
# A job that has failed this many times stays failed instead of retrying.
WORKER_MAX_ATTEMPTS = 3
# Seconds to sleep when a claim finds no pending jobs.
WORKER_POLL_INTERVAL_SECONDS = 2.0
```

- [ ] **Step 3: Remove deleted models + enums from `models.py`**

Delete the classes `Artifact`, `Transformation`, `TransformRun`, `RssFeed`, `RssFeedItem` and the enums `TransformationType`, `TransformRunStatus`, `RssFeedItemStatus`. Keep `Base`, `OrgUserRole`, `_BaseModel`, `_AuthoredModel`, `User`, `Org`, `OrgSettings`, `OrgUser`, `AppSettings`, `WikiPage`, `WikiPageVersion`. After deletion, remove now-unused imports: `Index` and `JSONB` are no longer referenced — verify with `uv run ruff check models.py` and drop whatever ruff flags as unused (`F401`).

- [ ] **Step 4: De-Prefect `knowledge.py`**

In `knowledge.py`, replace the Prefect concurrency gate with a semaphore and drop the transform/artifact coupling.

Change the imports block (top of file) from:

```python
import os
import re
import uuid
from typing import Any

from neo4j import Session
from openrouter import OpenRouter
from prefect.concurrency.sync import concurrency
from pydantic import BaseModel

import config
from db import get_postgres_session
from models import Artifact, Transformation
from neo4j_client import get_neo4j_session
```

to:

```python
import os
import re
import threading
import uuid
from typing import Any

from neo4j import Session
from openrouter import OpenRouter
from pydantic import BaseModel

import config
from neo4j_client import get_neo4j_session

# Bounds concurrent OpenRouter calls in place of the old Prefect concurrency pool.
_llm_semaphore = threading.Semaphore(config.LLM_CONCURRENCY)
```

In `_chat`, replace:

```python
    with concurrency(config.LLM_CONCURRENCY_NAME, occupy=1):
        result = client.chat.send(**kwargs)
```

with:

```python
    with _llm_semaphore:
        result = client.chat.send(**kwargs)
```

Delete the entire `run_knowledge_transform` function (lines from `def run_knowledge_transform(` to the end of the file) and delete the `KnowledgeTransformOutput` class. (`merge_content` replaces them in Task 8.)

- [ ] **Step 5: Reduce `main.py` to a Neo4j-bootstrap stub**

Replace the entire file with (the real FastAPI app arrives in Task 11):

```python
from neo4j_client import bootstrap_schema


def main() -> None:
    bootstrap_schema()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Reduce `seed.py` to admin + org only**

Replace the entire file with (config + API key seeding arrives in Task 11):

```python
import os
from typing import Any

import dotenv
from sqlalchemy.orm import DeclarativeBase, Session

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.sample")


def get_or_create[ModelT: DeclarativeBase](
    session: Session,
    model: type[ModelT],
    defaults: dict[str, Any] | None = None,
    **filters: Any,
) -> tuple[ModelT, bool]:
    instance = session.query(model).filter_by(**filters).one_or_none()
    if instance is not None:
        return instance, False
    instance = model(**{**filters, **(defaults or {})})
    session.add(instance)
    session.flush()
    return instance, True


def seed_database() -> None:
    from argon2 import PasswordHasher

    from db import get_postgres_session
    from models import Org, OrgUser, OrgUserRole, User

    ph = PasswordHasher()
    admin_email = os.getenv("INGESTION_ADMIN_EMAIL", "admin@sinpi.software")

    with get_postgres_session() as session:
        admin, admin_created = get_or_create(
            session,
            User,
            defaults={
                "name": os.getenv("INGESTION_ADMIN_NAME", "Admin User"),
                "password_hash": ph.hash(os.getenv("INGESTION_ADMIN_PASSWORD", "adminpassword")),
                "email_verified": True,
                "is_admin": True,
            },
            email=admin_email,
        )
        if admin_created:
            admin.created_by_id = admin.id
            admin.updated_by_id = admin.id
        audit = {"created_by": admin, "updated_by": admin}

        org, org_created = get_or_create(
            session,
            Org,
            defaults=dict(charter="This is the default organization.", **audit),
            name="Default Organization",
        )
        _membership, membership_created = get_or_create(
            session,
            OrgUser,
            defaults=dict(role=OrgUserRole.OWNER.value, **audit),
            org_id=org.id,
            user_id=admin.id,
        )
        session.commit()

    for label, created in [
        (f"admin user {admin_email!r}", admin_created),
        ("default org", org_created),
        ("org membership", membership_created),
    ]:
        print(f"  {'created' if created else 'exists '}  {label}")


if __name__ == "__main__":
    seed_database()
```

- [ ] **Step 7: Prune deleted-symbol tests from `test_knowledge.py`**

In `test_knowledge.py`:
- Change the `from models import ...` line (currently `from models import Artifact, Org, Transformation, TransformationType`) to `from models import Org` — but since the only remaining user of `Org` is the e2e test being removed, delete that import line entirely.
- Delete the `_NullClient` class and the entire `test_run_knowledge_writes_graph` test function (they reference `Artifact`, `Transformation`, `TransformationType`, and `run_knowledge_transform`).
- Delete the now-unused `requires_neo4j_and_postgres` marker definition and the `from db import get_postgres_session` / `_postgres_available` helper **only if** ruff flags them unused after the e2e test removal; re-add in Task 8 when the new e2e test needs them. (Run `uv run ruff check test_knowledge.py` and follow F401/F811.)

- [ ] **Step 8: Run the reduced suite and linters — verify green**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q
```
Expected: PASS. `test_knowledge.py` unit tests (normalize/escape/candidate-query/resolve-batch/strict-schema) and `test_sanitize.py` pass; Neo4j-backed tests skip if Neo4j is down.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor: demolish transforms/gates/rss/prefect subsystems"
```

---

## Task 2: New Postgres models + Alembic migration

**Files:**
- Modify: `ingestion/models.py`
- Create: `ingestion/alembic/versions/a1b2c3d4e5f6_engine_reset.py`
- Test: `ingestion/test_models.py`

**Interfaces:**
- Consumes: `_BaseModel`, `Org` from `models.py`.
- Produces:
  - `class JobStatus(Enum)` with members `PENDING="pending"`, `PROCESSING="processing"`, `DONE="done"`, `SKIPPED="skipped"`, `FAILED="failed"`.
  - `OrgConfig(_BaseModel)` — cols `org_id: str`, `relevance_prompt: str`, `entity_types: list[str]`, `relationship_types: list[str]`; unique on `org_id`.
  - `ApiKey(_BaseModel)` — cols `org_id: str`, `key_hash: str` (unique), `revoked_at: datetime | None`.
  - `IngestJob(_BaseModel)` — cols `org_id: str`, `content: str`, `job_metadata: dict | None` (DB column named `metadata`), `status: str` (default `pending`), `relevance_reason: str | None`, `error: str | None`, `attempts: int` (default 0), `processed_at: datetime | None`.

- [ ] **Step 1: Write the failing test**

Create `ingestion/test_models.py`:

```python
from models import ApiKey, IngestJob, JobStatus, OrgConfig


def test_job_status_values() -> None:
    assert {s.value for s in JobStatus} == {"pending", "processing", "done", "skipped", "failed"}


def test_ingest_job_columns() -> None:
    cols = IngestJob.__table__.columns
    assert "org_id" in cols and "content" in cols
    # Attribute is job_metadata but the DB column is `metadata` (SQLAlchemy reserves .metadata).
    assert IngestJob.job_metadata.property.columns[0].name == "metadata"
    assert cols["status"].server_default is not None
    assert cols["attempts"].server_default is not None


def test_org_config_is_unique_per_org() -> None:
    uniques = [c for c in OrgConfig.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in c.columns} == {"org_id"} for c in uniques)


def test_api_key_hash_is_unique() -> None:
    assert ApiKey.__table__.columns["key_hash"].unique is True
    assert ApiKey.__table__.columns["revoked_at"].nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test_models.py -q`
Expected: FAIL with `ImportError: cannot import name 'ApiKey' from 'models'`.

- [ ] **Step 3: Add the models**

In `models.py`, add `ARRAY` to the postgres-dialect import and `backref` to the orm import:

```python
from sqlalchemy.dialects.postgresql import ARRAY, BOOLEAN, JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    backref,
    declared_attr,
    mapped_column,
    relationship,
)
```

Append the new enum + models (after `Org`, before `OrgSettings` is fine — placement is cosmetic):

```python
class JobStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class OrgConfig(_BaseModel):
    __tablename__ = "org_configs"
    __table_args__ = (UniqueConstraint("org_id"),)
    org_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id"), nullable=False)
    org: Mapped["Org"] = relationship("Org", backref=backref("config", uselist=False))
    relevance_prompt: Mapped[str] = mapped_column(TEXT, nullable=False)
    entity_types: Mapped[list[str]] = mapped_column(ARRAY(TEXT), nullable=False, server_default=text("'{}'"))
    relationship_types: Mapped[list[str]] = mapped_column(ARRAY(TEXT), nullable=False, server_default=text("'{}'"))


class ApiKey(_BaseModel):
    __tablename__ = "api_keys"
    org_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id"), nullable=False)
    org: Mapped["Org"] = relationship("Org", backref="api_keys")
    key_hash: Mapped[str] = mapped_column(TEXT, nullable=False, unique=True)
    revoked_at: Mapped[DateTime | None] = mapped_column(TIMESTAMP, nullable=True)


class IngestJob(_BaseModel):
    __tablename__ = "ingest_jobs"
    org_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id"), nullable=False)
    org: Mapped["Org"] = relationship("Org", backref="ingest_jobs")
    content: Mapped[str] = mapped_column(TEXT, nullable=False)
    # `metadata` is reserved on Declarative classes, so the attribute is job_metadata.
    job_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=JobStatus.PENDING.value)
    relevance_reason: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    error: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    processed_at: Mapped[DateTime | None] = mapped_column(TIMESTAMP, nullable=True)
```

Note: `JSONB` was removed in Task 1 if unused; re-add it to the postgres-dialect import here since `IngestJob.job_metadata` needs it. Keep `Any` imported (already at top of `models.py`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test_models.py -q`
Expected: PASS.

- [ ] **Step 5: Write the migration**

Create `ingestion/alembic/versions/a1b2c3d4e5f6_engine_reset.py`:

```python
"""engine reset: drop transform/rss/artifact tables, create engine tables

Revision ID: a1b2c3d4e5f6
Revises: 172d87c4ab7b
Create Date: 2026-07-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "172d87c4ab7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop dependents before parents (FKs). Dev-only DB — data is disposable.
    op.drop_table("transform_runs")
    op.drop_table("rss_feed_items")
    op.drop_table("transformations")
    op.drop_table("rss_feeds")
    op.drop_table("artifacts")

    op.create_table(
        "org_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("created_at", postgresql.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("relevance_prompt", sa.Text(), nullable=False),
        sa.Column("entity_types", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("relationship_types", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.UniqueConstraint("org_id"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("created_at", postgresql.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("revoked_at", postgresql.TIMESTAMP(), nullable=True),
    )
    op.create_table(
        "ingest_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("created_at", postgresql.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("relevance_reason", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("processed_at", postgresql.TIMESTAMP(), nullable=True),
    )
    op.create_index("ix_ingest_jobs_status", "ingest_jobs", ["status"])


def downgrade() -> None:
    raise NotImplementedError("engine_reset is a one-way dev migration")
```

- [ ] **Step 6: Apply the migration and verify tables**

Run (Postgres must be up — `docker compose up -d postgres` from the repo root):
```bash
uv run alembic upgrade head
uv run python -c "from db import get_postgres_session; from sqlalchemy import text as t; s=get_postgres_session(); print(sorted(r[0] for r in s.execute(t(\"select tablename from pg_tables where schemaname='public'\"))))"
```
Expected: the printed list contains `api_keys`, `ingest_jobs`, `org_configs` and does NOT contain `artifacts`, `transformations`, `transform_runs`, `rss_feeds`, `rss_feed_items`.

- [ ] **Step 7: Commit**

```bash
git add models.py test_models.py alembic/versions/a1b2c3d4e5f6_engine_reset.py
git commit -m "feat: add OrgConfig/ApiKey/IngestJob models + engine-reset migration"
```

---

## Task 3: Dependencies — add FastAPI stack, drop Prefect

**Files:**
- Modify: `ingestion/pyproject.toml`, `ingestion/uv.lock`

**Interfaces:**
- Consumes: nothing.
- Produces: importable `fastapi`, `uvicorn`, `strawberry` (with `strawberry.fastapi.GraphQLRouter`), `httpx` (dev); no importable `prefect`.

- [ ] **Step 1: Edit `pyproject.toml` dependencies**

Set the `[project].dependencies` array to (drops `prefect`, `feedparser`, `trafilatura`, `curl-cffi` — all only used by now-deleted modules — and adds uvicorn + strawberry; `fastapi` is already present):

```toml
dependencies = [
    "alembic>=1.18.5",
    "argon2-cffi>=25.1.0",
    "dotenv>=0.9.9",
    "fastapi>=0.139.2",
    "neo4j>=6.2.0",
    "openrouter>=0.10.8",
    "psycopg2-binary>=2.9.12",
    "pydantic>=2.13.4",
    "sqlalchemy>=2.0.51",
    "strawberry-graphql[fastapi]>=0.230.0",
    "uvicorn[standard]>=0.30.0",
]
```

Set the dev group (adds `httpx` for FastAPI's `TestClient`):

```toml
[dependency-groups]
dev = [
    "httpx>=0.27.0",
    "mypy>=2.3.0",
    "pytest>=9.1.1",
    "ruff>=0.15.22",
]
```

Update the isort first-party list and mypy overrides (drop `prefect`/`curl_cffi`/`feedparser`, add new modules):

```toml
[tool.ruff.lint.isort]
known-first-party = [
    "auth",
    "config",
    "db",
    "graph_api",
    "graph_read",
    "knowledge",
    "main",
    "models",
    "neo4j_client",
    "relevance",
    "routes_config",
    "routes_content",
    "schemas",
    "seed",
    "worker",
]
```

```toml
[[tool.mypy.overrides]]
module = ["argon2.*"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["neo4j.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Resolve the lockfile and verify imports**

Run:
```bash
uv lock && uv sync
uv run python -c "import fastapi, uvicorn, strawberry, httpx; from strawberry.fastapi import GraphQLRouter; print('ok')"
uv run python -c "import prefect" ; echo "prefect exit: $?"
```
Expected: first prints `ok`; the `import prefect` line prints a `ModuleNotFoundError` and a non-zero exit code.

- [ ] **Step 3: Verify lint/type/tests still green**

Run: `uv run ruff check . && uv run mypy . && uv run pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add fastapi/uvicorn/strawberry, drop prefect and dead rss deps"
```

---

## Task 4: API-key auth dependency

**Files:**
- Create: `ingestion/auth.py`
- Test: `ingestion/test_auth.py`

**Interfaces:**
- Consumes: `ApiKey` from `models.py`, `get_postgres_session` from `db.py`.
- Produces:
  - `generate_api_key() -> str`
  - `hash_key(key: str) -> str` (SHA-256 hex; deterministic so a key can be looked up)
  - `require_org(authorization: str = Header(default="")) -> str` — FastAPI dependency returning `org_id`; raises `HTTPException(401)` on missing/invalid/revoked.

- [ ] **Step 1: Write the failing test**

Create `ingestion/test_auth.py`:

```python
import os

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from auth import generate_api_key, hash_key, require_org  # noqa: E402


def test_generate_api_key_is_random_and_urlsafe() -> None:
    a, b = generate_api_key(), generate_api_key()
    assert a != b
    assert len(a) >= 32
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_key_is_deterministic_and_hex() -> None:
    key = "some-key"
    assert hash_key(key) == hash_key(key)
    assert len(hash_key(key)) == 64
    assert hash_key(key) != hash_key("other-key")


def test_require_org_rejects_missing_token() -> None:
    with pytest.raises(HTTPException) as exc:
        require_org(authorization="")
    assert exc.value.status_code == 401


def test_require_org_rejects_non_bearer() -> None:
    with pytest.raises(HTTPException) as exc:
        require_org(authorization="Basic abc")
    assert exc.value.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test_auth.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'auth'`.

- [ ] **Step 3: Implement `auth.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test_auth.py -q`
Expected: PASS (the two `require_org` tests exercise the no-DB rejection paths; the valid/revoked DB paths are covered end-to-end in Task 5).

- [ ] **Step 5: Commit**

```bash
git add auth.py test_auth.py
git commit -m "feat: API-key auth (generate/hash/require_org dependency)"
```

---

## Task 5: `POST /content` + `GET /content/{job_id}`

**Files:**
- Create: `ingestion/schemas.py`, `ingestion/routes_content.py`
- Test: `ingestion/test_routes_content.py`

**Interfaces:**
- Consumes: `require_org` (Task 4), `hash_key`/`generate_api_key` (Task 4), `IngestJob`/`ApiKey`/`Org` (models), `get_postgres_session`.
- Produces:
  - `schemas.ContentRequest{ text: str, metadata: dict[str, Any] | None }`
  - `schemas.ContentAccepted{ job_id: str }`
  - `schemas.JobStatusResponse{ job_id: str, status: str, relevance_reason: str | None, error: str | None }`
  - `routes_content.router: APIRouter` with `POST /content` (202) and `GET /content/{job_id}`.

- [ ] **Step 1: Write the failing test**

Create `ingestion/test_routes_content.py`:

```python
import os
import uuid

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest  # noqa: E402
from sqlalchemy import text as sqltext  # noqa: E402


def _postgres_available() -> bool:
    try:
        from db import get_postgres_session

        with get_postgres_session() as s:
            s.execute(sqltext("SELECT 1"))
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.fixture
def client_and_org():  # type: ignore[no-untyped-def]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import generate_api_key, hash_key
    from db import get_postgres_session
    from models import ApiKey, Org
    from routes_content import router

    app = FastAPI()
    app.include_router(router)

    key = generate_api_key()
    with get_postgres_session() as s:
        org = Org(name=f"routes-test-{uuid.uuid4()}")
        s.add(org)
        s.flush()
        org_id = str(org.id)
        s.add(ApiKey(org_id=org.id, key_hash=hash_key(key)))
        s.commit()

    yield TestClient(app), org_id, key

    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM api_keys WHERE org_id = :o"), {"o": org_id})
        s.execute(sqltext("DELETE FROM ingest_jobs WHERE org_id = :o"), {"o": org_id})
        s.execute(sqltext("DELETE FROM orgs WHERE id = :o"), {"o": org_id})
        s.commit()


@requires_pg
def test_post_content_enqueues_and_returns_202(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, key = client_and_org
    resp = client.post("/content", json={"text": "hello"}, headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 202
    assert "job_id" in resp.json()


@requires_pg
def test_get_content_reflects_status(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, key = client_and_org
    job_id = client.post("/content", json={"text": "hi"}, headers={"Authorization": f"Bearer {key}"}).json()["job_id"]
    resp = client.get(f"/content/{job_id}", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@requires_pg
def test_missing_auth_is_401(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, _key = client_and_org
    assert client.post("/content", json={"text": "x"}).status_code == 401


@requires_pg
def test_other_orgs_job_is_404(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, key = client_and_org
    resp = client.get(f"/content/{uuid.uuid4()}", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test_routes_content.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'routes_content'`.

- [ ] **Step 3: Implement `schemas.py`**

```python
from typing import Any

from pydantic import BaseModel


class ContentRequest(BaseModel):
    text: str
    metadata: dict[str, Any] | None = None


class ContentAccepted(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    relevance_reason: str | None = None
    error: str | None = None


class ConfigRequest(BaseModel):
    relevance_prompt: str
    entity_types: list[str]
    relationship_types: list[str]


class ConfigResponse(BaseModel):
    org_id: str
    relevance_prompt: str
    entity_types: list[str]
    relationship_types: list[str]
```

- [ ] **Step 4: Implement `routes_content.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, status

from auth import require_org
from db import get_postgres_session
from models import IngestJob
from schemas import ContentAccepted, ContentRequest, JobStatusResponse

router = APIRouter()


@router.post("/content", status_code=status.HTTP_202_ACCEPTED, response_model=ContentAccepted)
def post_content(body: ContentRequest, org_id: str = Depends(require_org)) -> ContentAccepted:
    with get_postgres_session() as session:
        job = IngestJob(org_id=org_id, content=body.text, job_metadata=body.metadata)
        session.add(job)
        session.flush()
        job_id = str(job.id)
        session.commit()
    return ContentAccepted(job_id=job_id)


@router.get("/content/{job_id}", response_model=JobStatusResponse)
def get_content(job_id: str, org_id: str = Depends(require_org)) -> JobStatusResponse:
    with get_postgres_session() as session:
        job = session.get(IngestJob, job_id)
        if job is None or str(job.org_id) != org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
        return JobStatusResponse(
            job_id=str(job.id),
            status=job.status,
            relevance_reason=job.relevance_reason,
            error=job.error,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest test_routes_content.py -q`
Expected: PASS (skips if Postgres is down).

- [ ] **Step 6: Commit**

```bash
git add schemas.py routes_content.py test_routes_content.py
git commit -m "feat: POST /content + GET /content/{job_id} endpoints"
```

---

## Task 6: `PUT /config` (upsert OrgConfig)

**Files:**
- Create: `ingestion/routes_config.py`
- Test: `ingestion/test_routes_config.py`

**Interfaces:**
- Consumes: `require_org` (Task 4), `ConfigRequest`/`ConfigResponse` (Task 5 schemas), `OrgConfig`, `get_postgres_session`.
- Produces: `routes_config.router: APIRouter` with `PUT /config`.

- [ ] **Step 1: Write the failing test**

Create `ingestion/test_routes_config.py`:

```python
import os
import uuid

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest  # noqa: E402
from sqlalchemy import text as sqltext  # noqa: E402


def _postgres_available() -> bool:
    try:
        from db import get_postgres_session

        with get_postgres_session() as s:
            s.execute(sqltext("SELECT 1"))
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.fixture
def client_and_org():  # type: ignore[no-untyped-def]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import generate_api_key, hash_key
    from db import get_postgres_session
    from models import ApiKey, Org
    from routes_config import router

    app = FastAPI()
    app.include_router(router)

    key = generate_api_key()
    with get_postgres_session() as s:
        org = Org(name=f"config-test-{uuid.uuid4()}")
        s.add(org)
        s.flush()
        org_id = str(org.id)
        s.add(ApiKey(org_id=org.id, key_hash=hash_key(key)))
        s.commit()

    yield TestClient(app), org_id, key

    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM api_keys WHERE org_id = :o"), {"o": org_id})
        s.execute(sqltext("DELETE FROM org_configs WHERE org_id = :o"), {"o": org_id})
        s.execute(sqltext("DELETE FROM orgs WHERE id = :o"), {"o": org_id})
        s.commit()


@requires_pg
def test_put_config_creates_then_updates(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, org_id, key = client_and_org
    headers = {"Authorization": f"Bearer {key}"}

    body1 = {"relevance_prompt": "p1", "entity_types": ["Person"], "relationship_types": ["KNOWS"]}
    r1 = client.put("/config", json=body1, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["entity_types"] == ["Person"]

    body2 = {"relevance_prompt": "p2", "entity_types": ["Person", "Org"], "relationship_types": ["KNOWS", "WORKS_AT"]}
    r2 = client.put("/config", json=body2, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["relevance_prompt"] == "p2"
    assert r2.json()["relationship_types"] == ["KNOWS", "WORKS_AT"]

    from db import get_postgres_session
    from models import OrgConfig

    with get_postgres_session() as s:
        rows = s.query(OrgConfig).filter(OrgConfig.org_id == org_id).all()
    assert len(rows) == 1  # upsert, not insert-twice


@requires_pg
def test_put_config_requires_auth(client_and_org) -> None:  # type: ignore[no-untyped-def]
    client, _org_id, _key = client_and_org
    body = {"relevance_prompt": "p", "entity_types": [], "relationship_types": []}
    assert client.put("/config", json=body).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test_routes_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'routes_config'`.

- [ ] **Step 3: Implement `routes_config.py`**

```python
from fastapi import APIRouter, Depends

from auth import require_org
from db import get_postgres_session
from models import OrgConfig
from schemas import ConfigRequest, ConfigResponse

router = APIRouter()


@router.put("/config", response_model=ConfigResponse)
def put_config(body: ConfigRequest, org_id: str = Depends(require_org)) -> ConfigResponse:
    with get_postgres_session() as session:
        cfg = session.query(OrgConfig).filter(OrgConfig.org_id == org_id).one_or_none()
        if cfg is None:
            cfg = OrgConfig(
                org_id=org_id,
                relevance_prompt=body.relevance_prompt,
                entity_types=body.entity_types,
                relationship_types=body.relationship_types,
            )
            session.add(cfg)
        else:
            cfg.relevance_prompt = body.relevance_prompt
            cfg.entity_types = body.entity_types
            cfg.relationship_types = body.relationship_types
        session.commit()
    return ConfigResponse(
        org_id=org_id,
        relevance_prompt=body.relevance_prompt,
        entity_types=body.entity_types,
        relationship_types=body.relationship_types,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test_routes_config.py -q`
Expected: PASS (skips if Postgres is down).

- [ ] **Step 5: Commit**

```bash
git add routes_config.py test_routes_config.py
git commit -m "feat: PUT /config upserts per-org OrgConfig"
```

---

## Task 7: Relevance filter

**Files:**
- Create: `ingestion/relevance.py`
- Test: `ingestion/test_relevance.py`

**Interfaces:**
- Consumes: `_chat`, `_strict_schema` from `knowledge.py`; `config.LLM_MODEL`, `config.OPENROUTER_API_KEY_ENV`; `OpenRouter`.
- Produces:
  - `relevance.RelevanceResult{ relevant: bool, reason: str }`
  - `relevance.build_relevance_messages(relevance_prompt: str, content: str) -> list[dict[str, str]]`
  - `relevance.RelevanceError(RuntimeError)` — raised when the check can't be completed.
  - `relevance.judge_relevance(relevance_prompt: str, content: str) -> RelevanceResult` — raises `RelevanceError` on empty/unparseable LLM output so the worker retries rather than silently skipping the content; returns a clean verdict only on a successful judgment. A `relevant=False` verdict means the LLM *did* judge and found it irrelevant (→ skipped); an inability to judge is never expressed as `relevant=False`.

- [ ] **Step 1: Write the failing test**

Create `ingestion/test_relevance.py`:

```python
import os

os.environ.setdefault("INGESTION_OPENROUTER_API_KEY", "test-key-not-used")

import pytest  # noqa: E402

import relevance  # noqa: E402


class _CtxNull:
    def __enter__(self) -> "_CtxNull":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relevance, "OpenRouter", lambda **k: _CtxNull())


def test_build_relevance_messages_includes_prompt_and_content() -> None:
    msgs = relevance.build_relevance_messages("Only politics.", "A story about an election.")
    joined = " ".join(m["content"] for m in msgs)
    assert "Only politics." in joined
    assert "A story about an election." in joined
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"


def test_relevant_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relevance, "_chat", lambda *a, **k: '{"relevant": true, "reason": "on topic"}')
    result = relevance.judge_relevance("prompt", "content")
    assert result.relevant is True
    assert result.reason == "on topic"


def test_relevant_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relevance, "_chat", lambda *a, **k: '{"relevant": false, "reason": "off topic"}')
    assert relevance.judge_relevance("prompt", "content").relevant is False


def test_unparseable_output_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # A failed judgment must NOT masquerade as relevant=False (which the worker would
    # record as `skipped`, silently dropping the content). It raises so the worker
    # retries / marks the job failed.
    monkeypatch.setattr(relevance, "_chat", lambda *a, **k: "this is not json")
    with pytest.raises(relevance.RelevanceError):
        relevance.judge_relevance("prompt", "content")


def test_empty_output_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relevance, "_chat", lambda *a, **k: None)
    with pytest.raises(relevance.RelevanceError):
        relevance.judge_relevance("prompt", "content")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test_relevance.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'relevance'`.

- [ ] **Step 3: Implement `relevance.py`**

```python
import os

from openrouter import OpenRouter
from pydantic import BaseModel, ValidationError

import config
from knowledge import _chat, _strict_schema


class RelevanceResult(BaseModel):
    relevant: bool
    reason: str


class RelevanceError(RuntimeError):
    """The relevance check could not be completed (empty/unparseable LLM output).
    Raised so the worker retries or marks the job failed, rather than silently
    treating undecidable content as irrelevant and dropping it as `skipped`."""


def build_relevance_messages(relevance_prompt: str, content: str) -> list[dict[str, str]]:
    system = (
        f"{relevance_prompt}\n\nDecide whether the following content is relevant under that instruction. "
        "Return relevant=true or relevant=false and a brief reason."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def judge_relevance(relevance_prompt: str, content: str) -> RelevanceResult:
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "relevance",
            "strict": True,
            "schema": _strict_schema(RelevanceResult.model_json_schema()),
        },
    }
    with OpenRouter(api_key=os.environ[config.OPENROUTER_API_KEY_ENV]) as client:
        out = _chat(client, config.LLM_MODEL, build_relevance_messages(relevance_prompt, content), {}, schema)
    if out is None:
        raise RelevanceError("relevance check failed: empty LLM response")
    try:
        return RelevanceResult.model_validate_json(out)
    except ValidationError as exc:
        raise RelevanceError("relevance check failed: unparseable LLM response") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test_relevance.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add relevance.py test_relevance.py
git commit -m "feat: binary relevance filter; raises on bad output so the worker retries"
```

---

## Task 8: Adapt knowledge extraction — constrain relationships, provenance = job id

**Files:**
- Modify: `ingestion/knowledge.py`, `ingestion/test_knowledge.py`

**Interfaces:**
- Consumes: existing `knowledge.py` helpers; `config.LLM_MODEL`; `get_neo4j_session`; `OpenRouter`.
- Produces:
  - `build_extraction_messages(entity_types: list[str], relationship_types: list[str], text: str) -> list[dict[str, str]]` (signature changed: drops the free-form `prompt`, adds `relationship_types`).
  - `extract_knowledge(client, model, entity_types: list[str], relationship_types: list[str], text: str, llm_params) -> KnowledgeExtraction` (signature changed).
  - `write_relationship(session, org_id, source_id, target_id, rel_type, job_id) -> None` (param renamed `artifact_id`→`job_id`; Neo4j prop `source_job_id`).
  - `write_provenance(session, org_id, entity_id, job_id) -> None` (Source node keyed by `job_id`).
  - `MergeResult{ entities_created: int, entities_merged: int, relationships_created: int }`.
  - `merge_content(org_id: str, content: str, entity_types: list[str], relationship_types: list[str], job_id: str) -> MergeResult` (replaces `run_knowledge_transform`).

- [ ] **Step 1: Write/adapt the failing tests**

In `test_knowledge.py`, replace the existing `test_build_extraction_messages_includes_entity_types_and_text` with the new signature and add a relationship-constraint test. Also add a Postgres+Neo4j e2e for `merge_content`. First, ensure the file's import block includes (add what's missing, keep the existing ones that survive):

```python
from knowledge import (  # noqa: E402
    ExtractedEntity,
    ExtractedRelationship,
    KnowledgeExtraction,
    MergeResult,
    build_extraction_messages,
    candidate_query,
    escape_lucene,
    fulltext_candidate_query,
    merge_content,
    normalize_name,
    upsert_entity,
    write_provenance,
    write_relationship,
)
```

Re-add (if Task 1 removed them) the Postgres availability helper and the combined marker near the top of the file:

```python
def _postgres_available() -> bool:
    try:
        with get_postgres_session() as session:
            session.execute(sqlalchemy_text("SELECT 1"))
        return True
    except Exception:
        return False


requires_neo4j_and_postgres = pytest.mark.skipif(
    not (_neo4j_available() and _postgres_available()), reason="Neo4j and/or Postgres not reachable"
)
```

Replace the extraction-messages test and add the merge e2e:

```python
def test_build_extraction_messages_includes_entity_and_relationship_types() -> None:
    msgs = build_extraction_messages(["Person", "Place"], ["KNOWS", "BORN_IN"], "Some article")
    joined = " ".join(m["content"] for m in msgs)
    assert "Person" in joined and "Place" in joined
    assert "KNOWS" in joined and "BORN_IN" in joined
    assert "Some article" in joined
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"


@requires_neo4j_and_postgres
def test_merge_content_constrains_types_and_records_job_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap_schema()
    extraction = KnowledgeExtraction(
        entities=[
            ExtractedEntity(name="Ada", type="Person", description="Mathematician"),
            ExtractedEntity(name="Engine", type="Thing", description="A machine"),
            ExtractedEntity(name="Rover", type="Robot", description="Off-list entity type"),
        ],
        relationships=[
            ExtractedRelationship(source_name="Ada", target_name="Engine", type="WORKED_ON"),
            # Off-list relationship type — must be dropped.
            ExtractedRelationship(source_name="Ada", target_name="Engine", type="HATES"),
        ],
    )
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))
    monkeypatch.setattr(knowledge_mod, "merge_summary", lambda *a, **k: "merged")

    class _NullClient:
        def __enter__(self) -> "_NullClient":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(knowledge_mod, "OpenRouter", lambda *a, **k: _NullClient())

    org_id = f"merge-{uuid.uuid4()}"
    job_id = str(uuid.uuid4())
    try:
        result = merge_content(org_id, "Ada worked on the Engine.", ["Person", "Thing"], ["WORKED_ON"], job_id)
        assert result.entities_created == 2  # Rover filtered
        assert result.relationships_created == 1  # HATES filtered
        with get_neo4j_session() as neo:
            ecount = neo.run("MATCH (e:Entity {org_id: $o}) RETURN count(e) AS c", {"o": org_id}).single(True)["c"]
            assert ecount == 2
            rel = neo.run(
                "MATCH (:Entity {org_id: $o})-[r:RELATED]->() RETURN r.type AS t, r.source_job_id AS j",
                {"o": org_id},
            ).single(True)
            assert rel["t"] == "WORKED_ON"
            assert rel["j"] == job_id
            src = neo.run(
                "MATCH (:Entity {org_id: $o})-[:MENTIONED_IN]->(s:Source) RETURN s.job_id AS j LIMIT 1",
                {"o": org_id},
            ).single(True)
            assert src["j"] == job_id
    finally:
        with get_neo4j_session() as neo:
            neo.run("MATCH (n) WHERE n.org_id = $o DETACH DELETE n", {"o": org_id})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest test_knowledge.py -q`
Expected: FAIL — `ImportError: cannot import name 'merge_content'` (and, once imports resolve, the messages test fails on the old signature).

- [ ] **Step 3: Update `build_extraction_messages` and `extract_knowledge`**

Replace `build_extraction_messages` with:

```python
def build_extraction_messages(entity_types: list[str], relationship_types: list[str], text: str) -> list[dict[str, str]]:
    system = (
        f"Extract only entities of these types: {', '.join(entity_types)}. "
        "For each entity, write a thorough, self-contained description capturing everything this "
        "article says about it (who/what it is, key facts, context) — a rich paragraph, not a label. "
        f"Also extract relationships between them, using only these relationship types: "
        f"{', '.join(relationship_types)}. Use the exact type strings given; do not invent new ones."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]
```

Replace `extract_knowledge` with:

```python
def extract_knowledge(
    client: OpenRouter,
    model: str,
    entity_types: list[str],
    relationship_types: list[str],
    text: str,
    llm_params: dict[str, Any],
) -> KnowledgeExtraction:
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "knowledge",
            "strict": True,
            "schema": _strict_schema(KnowledgeExtraction.model_json_schema()),
        },
    }
    content = _chat(client, model, build_extraction_messages(entity_types, relationship_types, text), llm_params, schema)
    if content is None:
        raise ValueError("LLM returned no text content")
    return KnowledgeExtraction.model_validate_json(content)
```

- [ ] **Step 4: Rename provenance params to `job_id`**

Replace `write_relationship`:

```python
def write_relationship(
    session: Session, org_id: str, source_id: str, target_id: str, rel_type: str, job_id: str
) -> None:
    session.run(
        "MATCH (a:Entity {id: $source_id, org_id: $org_id}), (b:Entity {id: $target_id, org_id: $org_id}) "
        "MERGE (a)-[r:RELATED {type: $rel_type, org_id: $org_id}]->(b) "
        "ON CREATE SET r.source_job_id = $job_id, r.created_at = datetime()",
        {
            "source_id": source_id,
            "target_id": target_id,
            "org_id": org_id,
            "rel_type": rel_type,
            "job_id": job_id,
        },
    )
```

Replace `write_provenance`:

```python
def write_provenance(session: Session, org_id: str, entity_id: str, job_id: str) -> None:
    session.run(
        "MERGE (s:Source {org_id: $org_id, job_id: $job_id}) "
        "WITH s MATCH (e:Entity {id: $entity_id, org_id: $org_id}) MERGE (e)-[:MENTIONED_IN]->(s)",
        {"org_id": org_id, "job_id": job_id, "entity_id": entity_id},
    )
```

- [ ] **Step 5: Add `MergeResult` + `merge_content`**

Append to `knowledge.py`:

```python
class MergeResult(BaseModel):
    entities_created: int
    entities_merged: int
    relationships_created: int


def merge_content(
    org_id: str,
    content: str,
    entity_types: list[str],
    relationship_types: list[str],
    job_id: str,
) -> MergeResult:
    allowed_entities = {t.lower() for t in entity_types}
    allowed_rels = {t.upper() for t in relationship_types}
    llm_params: dict[str, Any] = {}
    created = merged = rels = 0
    name_to_id: dict[str, str] = {}
    with OpenRouter(api_key=os.environ[config.OPENROUTER_API_KEY_ENV]) as client, get_neo4j_session() as neo:
        extraction = extract_knowledge(client, config.LLM_MODEL, entity_types, relationship_types, content, llm_params)
        entities = [e for e in extraction.entities if e.type.lower() in allowed_entities]
        resolved_ids = resolve_entities_batch(neo, client, config.LLM_MODEL, org_id, entities, llm_params)
        for entity, existing_id in zip(entities, resolved_ids, strict=True):
            if existing_id is None:
                entity_id, summary = str(uuid.uuid4()), entity.description
                created += 1
            else:
                entity_id = existing_id
                row = neo.run(
                    "MATCH (e:Entity {id: $id, org_id: $org_id}) RETURN e.summary AS s",
                    {"id": entity_id, "org_id": org_id},
                ).single()
                summary = merge_summary(client, config.LLM_MODEL, row["s"] if row else "", entity.description, llm_params)
                merged += 1
            upsert_entity(neo, org_id, entity_id, entity, summary)
            write_provenance(neo, org_id, entity_id, job_id)
            name_to_id[normalize_name(entity.name)] = entity_id

        for rel in extraction.relationships:
            if rel.type.upper() not in allowed_rels:
                continue
            src = name_to_id.get(normalize_name(rel.source_name))
            tgt = name_to_id.get(normalize_name(rel.target_name))
            if src and tgt:
                write_relationship(neo, org_id, src, tgt, rel.type, job_id)
                rels += 1
    return MergeResult(entities_created=created, entities_merged=merged, relationships_created=rels)
```

- [ ] **Step 6: Run tests + linters to verify green**

Run: `uv run ruff check knowledge.py test_knowledge.py && uv run mypy knowledge.py && uv run pytest test_knowledge.py -q`
Expected: PASS (Neo4j-backed cases skip if Neo4j is down; the messages/unit tests pass regardless).

- [ ] **Step 7: Commit**

```bash
git add knowledge.py test_knowledge.py
git commit -m "feat: merge_content with relationship-type constraint + job provenance"
```

---

## Task 9: Worker loop

**Files:**
- Create: `ingestion/worker.py`
- Test: `ingestion/test_worker.py`

**Interfaces:**
- Consumes: `IngestJob`, `JobStatus`, `OrgConfig` (models); `get_postgres_session`; `judge_relevance` (Task 7); `merge_content` (Task 8); `config.WORKER_*`.
- Produces:
  - `claim_pending_job_ids(session: Session, batch_size: int) -> list[str]` — locks pending rows with `FOR UPDATE SKIP LOCKED`, flips them to `processing`, commits, returns their ids.
  - `process_job(job_id: str) -> None` — relevance → skip/merge → status transition; on exception increments `attempts` and sets `failed` (or back to `pending` while under `WORKER_MAX_ATTEMPTS`).
  - `run_once() -> int` — claim a batch and process each; returns count processed.
  - `main() -> None` — poll loop.

- [ ] **Step 1: Write the failing test**

Create `ingestion/test_worker.py`:

```python
import os
import uuid

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")
os.environ.setdefault("INGESTION_OPENROUTER_API_KEY", "test-key-not-used")

import pytest  # noqa: E402
from sqlalchemy import text as sqltext  # noqa: E402

import worker  # noqa: E402
from db import get_postgres_session  # noqa: E402
from models import IngestJob, JobStatus, Org, OrgConfig  # noqa: E402
from relevance import RelevanceResult  # noqa: E402


def _postgres_available() -> bool:
    try:
        with get_postgres_session() as s:
            s.execute(sqltext("SELECT 1"))
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.fixture
def org_with_config():  # type: ignore[no-untyped-def]
    with get_postgres_session() as s:
        org = Org(name=f"worker-test-{uuid.uuid4()}")
        s.add(org)
        s.flush()
        org_id = str(org.id)
        s.add(
            OrgConfig(
                org_id=org.id,
                relevance_prompt="anything",
                entity_types=["Person"],
                relationship_types=["KNOWS"],
            )
        )
        s.commit()
    yield org_id
    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM ingest_jobs WHERE org_id = :o"), {"o": org_id})
        s.execute(sqltext("DELETE FROM org_configs WHERE org_id = :o"), {"o": org_id})
        s.execute(sqltext("DELETE FROM orgs WHERE id = :o"), {"o": org_id})
        s.commit()


def _enqueue(org_id: str, text: str) -> str:
    with get_postgres_session() as s:
        job = IngestJob(org_id=org_id, content=text)
        s.add(job)
        s.flush()
        job_id = str(job.id)
        s.commit()
    return job_id


def _status(job_id: str) -> str:
    with get_postgres_session() as s:
        job = s.get(IngestJob, job_id)
        assert job is not None
        return job.status


@requires_pg
def test_relevant_job_reaches_done(monkeypatch: pytest.MonkeyPatch, org_with_config) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(worker, "judge_relevance", lambda *a, **k: RelevanceResult(relevant=True, reason="ok"))
    monkeypatch.setattr(worker, "merge_content", lambda *a, **k: None)
    job_id = _enqueue(org_with_config, "content")
    worker.process_job(job_id)
    assert _status(job_id) == "done"


@requires_pg
def test_irrelevant_job_is_skipped(monkeypatch: pytest.MonkeyPatch, org_with_config) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(worker, "judge_relevance", lambda *a, **k: RelevanceResult(relevant=False, reason="nope"))
    job_id = _enqueue(org_with_config, "content")
    worker.process_job(job_id)
    assert _status(job_id) == "skipped"
    with get_postgres_session() as s:
        assert s.get(IngestJob, job_id).relevance_reason == "nope"  # type: ignore[union-attr]


@requires_pg
def test_extraction_error_fails_after_max_attempts(monkeypatch: pytest.MonkeyPatch, org_with_config) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(worker, "judge_relevance", lambda *a, **k: RelevanceResult(relevant=True, reason="ok"))

    def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("extraction blew up")

    monkeypatch.setattr(worker, "merge_content", _boom)
    monkeypatch.setattr(worker.config, "WORKER_MAX_ATTEMPTS", 1)
    job_id = _enqueue(org_with_config, "content")
    worker.process_job(job_id)
    assert _status(job_id) == "failed"
    with get_postgres_session() as s:
        job = s.get(IngestJob, job_id)
        assert job is not None
        assert job.attempts == 1
        assert "extraction blew up" in (job.error or "")


@requires_pg
def test_relevance_failure_retries_not_skips(monkeypatch: pytest.MonkeyPatch, org_with_config) -> None:  # type: ignore[no-untyped-def]
    # A relevance check that can't complete must NOT be recorded as skipped (which would
    # silently drop the content); it goes back to pending for retry (attempts under cap).
    from relevance import RelevanceError

    def _boom(*a: object, **k: object) -> None:
        raise RelevanceError("relevance check failed: empty LLM response")

    monkeypatch.setattr(worker, "judge_relevance", _boom)
    monkeypatch.setattr(worker.config, "WORKER_MAX_ATTEMPTS", 3)
    job_id = _enqueue(org_with_config, "content")
    worker.process_job(job_id)
    assert _status(job_id) == "pending"
    with get_postgres_session() as s:
        job = s.get(IngestJob, job_id)
        assert job is not None and job.attempts == 1


@requires_pg
def test_skip_locked_prevents_double_claim(org_with_config) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    _enqueue(org_with_config, "a")
    _enqueue(org_with_config, "b")
    sa = get_postgres_session()
    sb = get_postgres_session()
    try:
        stmt = (
            select(IngestJob.id)
            .where(IngestJob.status == JobStatus.PENDING.value, IngestJob.org_id == org_with_config)
            .order_by(IngestJob.created_at)
            .with_for_update(skip_locked=True)
        )
        a_ids = {str(x) for x in sa.execute(stmt).scalars().all()}  # locks both rows
        b_ids = {str(x) for x in sb.execute(stmt).scalars().all()}  # sees none of A's locked rows
        assert a_ids and not (a_ids & b_ids)
    finally:
        sa.rollback()
        sb.rollback()
        sa.close()
        sb.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test_worker.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'worker'`.

- [ ] **Step 3: Implement `worker.py`**

```python
import os
import time
from datetime import UTC, datetime

import dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from db import get_postgres_session
from knowledge import merge_content
from models import IngestJob, JobStatus, OrgConfig
from relevance import judge_relevance

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.sample")


def claim_pending_job_ids(session: Session, batch_size: int) -> list[str]:
    jobs = (
        session.execute(
            select(IngestJob)
            .where(IngestJob.status == JobStatus.PENDING.value)
            .order_by(IngestJob.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    ids = []
    for job in jobs:
        job.status = JobStatus.PROCESSING.value
        ids.append(str(job.id))
    session.commit()
    return ids


def process_job(job_id: str) -> None:
    with get_postgres_session() as session:
        job = session.get(IngestJob, job_id)
        if job is None:
            return
        org_id = str(job.org_id)
        content = job.content
        cfg = session.query(OrgConfig).filter(OrgConfig.org_id == org_id).one_or_none()
        relevance_prompt = cfg.relevance_prompt if cfg else ""
        entity_types = list(cfg.entity_types) if cfg else []
        relationship_types = list(cfg.relationship_types) if cfg else []

    try:
        verdict = judge_relevance(relevance_prompt, content)
        if not verdict.relevant:
            _finalize(job_id, JobStatus.SKIPPED, relevance_reason=verdict.reason)
            return
        merge_content(org_id, content, entity_types, relationship_types, job_id)
        _finalize(job_id, JobStatus.DONE, relevance_reason=verdict.reason)
    except Exception as exc:  # noqa: BLE001 — any failure marks the job, never crashes the loop
        _record_failure(job_id, str(exc))


def _finalize(job_id: str, status: JobStatus, relevance_reason: str | None = None) -> None:
    with get_postgres_session() as session:
        job = session.get(IngestJob, job_id)
        if job is None:
            return
        job.status = status.value
        job.relevance_reason = relevance_reason
        job.error = None
        job.processed_at = datetime.now(UTC)
        session.commit()


def _record_failure(job_id: str, error: str) -> None:
    with get_postgres_session() as session:
        job = session.get(IngestJob, job_id)
        if job is None:
            return
        job.attempts += 1
        job.error = error
        # Retry (back to pending) while under the cap; otherwise stay failed.
        if job.attempts < config.WORKER_MAX_ATTEMPTS:
            job.status = JobStatus.PENDING.value
        else:
            job.status = JobStatus.FAILED.value
            job.processed_at = datetime.now(UTC)
        session.commit()


def run_once() -> int:
    with get_postgres_session() as session:
        job_ids = claim_pending_job_ids(session, config.WORKER_BATCH_SIZE)
    for job_id in job_ids:
        process_job(job_id)
    return len(job_ids)


def main() -> None:
    while True:
        if run_once() == 0:
            time.sleep(config.WORKER_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
```

Note on the failure test: it sets `WORKER_MAX_ATTEMPTS=1`, so the first failure (`attempts` becomes 1, not `< 1`) lands in `failed` — matching the assertion.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test_worker.py -q`
Expected: PASS (skips if Postgres is down).

- [ ] **Step 5: Commit**

```bash
git add worker.py test_worker.py
git commit -m "feat: worker loop with SKIP LOCKED claim + bounded retry"
```

---

## Task 10: GraphQL read API (Strawberry, generic schema)

**Files:**
- Create: `ingestion/graph_read.py`, `ingestion/graph_api.py`
- Test: `ingestion/test_graph_api.py`

**Interfaces:**
- Consumes: `get_neo4j_session` (neo4j_client), `escape_lucene` (knowledge), `require_org` (auth), `upsert_entity`/`write_relationship` (for the test's seeded graph).
- Produces:
  - `graph_read.query_nodes(org_id, type_, search, limit) -> list[dict]` with keys `id,type,name,summary`.
  - `graph_read.query_node(org_id, node_id) -> dict | None`.
  - `graph_read.query_edges(org_id, node_id, type_) -> list[dict]` with keys `type` and `target` (a dict `id,type,name,summary`).
  - `graph_api.schema: strawberry.Schema` and `graph_api.graphql_router: GraphQLRouter` (org from context via `require_org`).

- [ ] **Step 1: Write the failing test**

Create `ingestion/test_graph_api.py`:

```python
import os
import uuid

os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")
os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest  # noqa: E402
from sqlalchemy import text as sqltext  # noqa: E402

from knowledge import ExtractedEntity, upsert_entity, write_relationship  # noqa: E402
from neo4j_client import bootstrap_schema, get_neo4j_session  # noqa: E402


def _neo4j_available() -> bool:
    try:
        with get_neo4j_session() as s:
            s.run("RETURN 1").consume()
        return True
    except Exception:
        return False


def _postgres_available() -> bool:
    try:
        from db import get_postgres_session

        with get_postgres_session() as s:
            s.execute(sqltext("SELECT 1"))
        return True
    except Exception:
        return False


requires_stack = pytest.mark.skipif(
    not (_neo4j_available() and _postgres_available()), reason="Neo4j and/or Postgres not reachable"
)


@pytest.fixture
def seeded():  # type: ignore[no-untyped-def]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import generate_api_key, hash_key
    from db import get_postgres_session
    from graph_api import graphql_router
    from models import ApiKey, Org

    bootstrap_schema()
    org_a = f"a-{uuid.uuid4()}"
    org_b = f"b-{uuid.uuid4()}"
    ada_id, eng_id = str(uuid.uuid4()), str(uuid.uuid4())
    with get_neo4j_session() as s:
        upsert_entity(s, org_a, ada_id, ExtractedEntity(name="Ada", type="Person", description="d"), "Ada summary")
        upsert_entity(s, org_a, eng_id, ExtractedEntity(name="Engine", type="Thing", description="d"), "Engine summary")
        write_relationship(s, org_a, ada_id, eng_id, "WORKED_ON", "job-1")
        upsert_entity(s, org_b, str(uuid.uuid4()), ExtractedEntity(name="Secret", type="Person", description="d"), "x")

    key = generate_api_key()
    with get_postgres_session() as s:
        org = Org(name=f"gql-{uuid.uuid4()}", id=org_a) if False else Org(name=f"gql-{uuid.uuid4()}")
        s.add(org)
        s.flush()
        # bind the API key to org_a's graph id by overriding org_id directly
        s.add(ApiKey(org_id=org.id, key_hash=hash_key(key)))
        s.flush()
        pg_org_id = str(org.id)
        s.commit()

    # Re-key the seeded graph to the Postgres org id so auth (which returns the pg org id) matches.
    with get_neo4j_session() as s:
        s.run("MATCH (n) WHERE n.org_id = $old SET n.org_id = $new", {"old": org_a, "new": pg_org_id})
        s.run(
            "MATCH ()-[r]->() WHERE r.org_id = $old SET r.org_id = $new",
            {"old": org_a, "new": pg_org_id},
        )

    app = FastAPI()
    app.include_router(graphql_router, prefix="/graphql")
    client = TestClient(app)

    yield client, key, pg_org_id, ada_id, eng_id, org_b

    with get_neo4j_session() as s:
        s.run("MATCH (n) WHERE n.org_id IN [$a, $b] DETACH DELETE n", {"a": pg_org_id, "b": org_b})
    with get_postgres_session() as s:
        s.execute(sqltext("DELETE FROM api_keys WHERE org_id = :o"), {"o": pg_org_id})
        s.execute(sqltext("DELETE FROM orgs WHERE id = :o"), {"o": pg_org_id})
        s.commit()


def _gql(client, key, query):  # type: ignore[no-untyped-def]
    return client.post("/graphql", json={"query": query}, headers={"Authorization": f"Bearer {key}"})


@requires_stack
def test_nodes_lists_only_callers_org(seeded) -> None:  # type: ignore[no-untyped-def]
    client, key, _org, _ada, _eng, _org_b = seeded
    resp = _gql(client, key, "{ nodes { name type } }")
    names = {n["name"] for n in resp.json()["data"]["nodes"]}
    assert names == {"Ada", "Engine"}  # org_b's "Secret" is invisible


@requires_stack
def test_nodes_filter_by_type(seeded) -> None:  # type: ignore[no-untyped-def]
    client, key, _org, _ada, _eng, _org_b = seeded
    resp = _gql(client, key, '{ nodes(type: "Person") { name } }')
    assert [n["name"] for n in resp.json()["data"]["nodes"]] == ["Ada"]


@requires_stack
def test_node_and_edges(seeded) -> None:  # type: ignore[no-untyped-def]
    client, key, _org, ada_id, _eng, _org_b = seeded
    resp = _gql(client, key, '{ node(id: "%s") { name edges { type target { name } } } }' % ada_id)
    node = resp.json()["data"]["node"]
    assert node["name"] == "Ada"
    assert node["edges"][0]["type"] == "WORKED_ON"
    assert node["edges"][0]["target"]["name"] == "Engine"


@requires_stack
def test_graphql_requires_auth(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _key, _org, _ada, _eng, _org_b = seeded
    resp = client.post("/graphql", json={"query": "{ nodes { name } }"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test_graph_api.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'graph_api'`.

- [ ] **Step 3: Implement `graph_read.py`**

```python
from typing import Any

from knowledge import escape_lucene
from neo4j_client import get_neo4j_session

_NODE_RETURN = "RETURN e.id AS id, e.type AS type, e.name AS name, e.summary AS summary"


def query_nodes(org_id: str, type_: str | None, search: str | None, limit: int) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"org_id": org_id, "limit": limit}
    if search:
        cypher = (
            "CALL db.index.fulltext.queryNodes('entity_name', $q) YIELD node AS e, score "
            "WHERE e.org_id = $org_id "
        )
        params["q"] = escape_lucene(search)
        if type_:
            cypher += "AND e.type = $type "
            params["type"] = type_
        cypher += f"{_NODE_RETURN} ORDER BY score DESC LIMIT $limit"
    else:
        cypher = "MATCH (e:Entity {org_id: $org_id}) "
        if type_:
            cypher += "WHERE e.type = $type "
            params["type"] = type_
        cypher += f"{_NODE_RETURN} LIMIT $limit"
    with get_neo4j_session() as session:
        return [dict(r) for r in session.run(cypher, params)]


def query_node(org_id: str, node_id: str) -> dict[str, Any] | None:
    with get_neo4j_session() as session:
        record = session.run(
            f"MATCH (e:Entity {{id: $id, org_id: $org_id}}) {_NODE_RETURN}",
            {"id": node_id, "org_id": org_id},
        ).single()
    return dict(record) if record else None


def query_edges(org_id: str, node_id: str, type_: str | None) -> list[dict[str, Any]]:
    cypher = (
        "MATCH (a:Entity {id: $id, org_id: $org_id})-[r:RELATED {org_id: $org_id}]->(b:Entity) "
    )
    params: dict[str, Any] = {"id": node_id, "org_id": org_id}
    if type_:
        cypher += "WHERE r.type = $type "
        params["type"] = type_
    cypher += "RETURN r.type AS type, b.id AS id, b.type AS ntype, b.name AS name, b.summary AS summary"
    with get_neo4j_session() as session:
        return [
            {
                "type": row["type"],
                "target": {"id": row["id"], "type": row["ntype"], "name": row["name"], "summary": row["summary"]},
            }
            for row in session.run(cypher, params)
        ]
```

- [ ] **Step 4: Implement `graph_api.py`**

```python
from typing import Any

import strawberry
from fastapi import Depends
from strawberry.fastapi import GraphQLRouter

import graph_read
from auth import require_org


@strawberry.type
class Node:
    id: strawberry.ID
    type: str
    name: str
    summary: str | None
    org_id: strawberry.Private[str]

    @strawberry.field
    def edges(self, type: str | None = None) -> list["Edge"]:
        rows = graph_read.query_edges(self.org_id, str(self.id), type)
        return [Edge(type=row["type"], target=_to_node(row["target"], self.org_id)) for row in rows]


@strawberry.type
class Edge:
    type: str
    target: Node


def _to_node(row: dict[str, Any], org_id: str) -> Node:
    return Node(
        id=strawberry.ID(str(row["id"])),
        type=row["type"],
        name=row["name"],
        summary=row["summary"],
        org_id=org_id,
    )


@strawberry.type
class Query:
    @strawberry.field
    def nodes(
        self, info: strawberry.Info, type: str | None = None, search: str | None = None, limit: int = 50
    ) -> list[Node]:
        org_id: str = info.context["org_id"]
        return [_to_node(row, org_id) for row in graph_read.query_nodes(org_id, type, search, limit)]

    @strawberry.field
    def node(self, info: strawberry.Info, id: strawberry.ID) -> Node | None:
        org_id: str = info.context["org_id"]
        row = graph_read.query_node(org_id, str(id))
        return _to_node(row, org_id) if row else None


schema = strawberry.Schema(query=Query)


async def get_context(org_id: str = Depends(require_org)) -> dict[str, Any]:
    return {"org_id": org_id}


graphql_router: GraphQLRouter[dict[str, Any], None] = GraphQLRouter(schema, context_getter=get_context)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest test_graph_api.py -q`
Expected: PASS (skips if the stack is down). Also run `uv run mypy graph_api.py graph_read.py`.

- [ ] **Step 6: Commit**

```bash
git add graph_read.py graph_api.py test_graph_api.py
git commit -m "feat: generic org-scoped GraphQL read API over Neo4j"
```

---

## Task 11: Wiring — FastAPI app, worker entrypoint, seed, README

**Files:**
- Modify: `ingestion/main.py`, `ingestion/seed.py`, `ingestion/README.md`
- Test: `ingestion/test_main.py`

**Interfaces:**
- Consumes: `bootstrap_schema`; the three routers (`routes_content.router`, `routes_config.router`, `graph_api.graphql_router`); `generate_api_key`/`hash_key`; models.
- Produces: `main.app: FastAPI`; a runnable `worker.main()` (already from Task 9); `seed.seed_database()` printing an API key.

- [ ] **Step 1: Write the failing test**

Create `ingestion/test_main.py`:

```python
import os

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")
os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")

from main import app  # noqa: E402


def test_app_exposes_all_routes() -> None:
    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert "/content" in paths
    assert "/content/{job_id}" in paths
    assert "/config" in paths
    assert "/graphql" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test_main.py -q`
Expected: FAIL — `main` has no `app` (currently the Task 1 stub).

- [ ] **Step 3: Rewrite `main.py`**

```python
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import dotenv
from fastapi import FastAPI

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.sample")

from graph_api import graphql_router  # noqa: E402
from neo4j_client import bootstrap_schema  # noqa: E402
from routes_config import router as config_router  # noqa: E402
from routes_content import router as content_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    bootstrap_schema()
    yield


app = FastAPI(title="Knowledge Graph Engine", lifespan=lifespan)
app.include_router(content_router)
app.include_router(config_router)
app.include_router(graphql_router, prefix="/graphql")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test_main.py -q`
Expected: PASS.

- [ ] **Step 5: Extend `seed.py` with OrgConfig + a printed API key**

In `seed.py`, extend `seed_database`. After the `OrgUser` membership block and before `session.commit()`, add config upsert + a one-time API key:

```python
        from auth import generate_api_key, hash_key
        from models import ApiKey, OrgConfig

        cfg, config_created = get_or_create(
            session,
            OrgConfig,
            defaults={
                "relevance_prompt": "Is this content about technology, science, or business news?",
                "entity_types": ["Person", "Organization", "Place", "Topic"],
                "relationship_types": ["WORKS_AT", "LOCATED_IN", "RELATED_TO", "FOUNDED"],
            },
            org_id=org.id,
        )

        api_key_plaintext: str | None = None
        existing_key = session.query(ApiKey).filter(ApiKey.org_id == org.id, ApiKey.revoked_at.is_(None)).first()
        if existing_key is None:
            api_key_plaintext = generate_api_key()
            session.add(ApiKey(org_id=org.id, key_hash=hash_key(api_key_plaintext)))
```

Then update the print block to report config and the key (only shown when freshly created):

```python
    for label, created in [
        (f"admin user {admin_email!r}", admin_created),
        ("default org", org_created),
        ("org membership", membership_created),
        ("org config", config_created),
    ]:
        print(f"  {'created' if created else 'exists '}  {label}")
    if api_key_plaintext is not None:
        print(f"\n  API KEY (shown once): {api_key_plaintext}\n")
```

Move `api_key_plaintext` initialization so it is defined before the `with` block (declare `api_key_plaintext: str | None = None` at the top of `seed_database`, and drop the inner re-declaration to a plain assignment).

- [ ] **Step 6: Run seed and verify a key prints**

Run (Postgres up, migration applied):
```bash
uv run python seed.py
```
Expected: prints `created org config` on first run and an `API KEY (shown once): ...` line.

- [ ] **Step 7: Write `README.md` run docs**

Replace `ingestion/README.md` with:

````markdown
# Knowledge Graph Engine

Content in → per-org relevance filter → typed entity/relationship extraction → Neo4j graph, read over GraphQL.

## Prerequisites

- Postgres + Neo4j: `docker compose up -d postgres neo4j` (from the repo root)
- Copy `.env.sample` to `.env` and set `INGESTION_OPENROUTER_API_KEY`.

## Setup

```bash
uv sync
uv run alembic upgrade head
uv run python seed.py   # creates the default org + config and prints an API key (once)
```

## Run

```bash
uv run uvicorn main:app --reload   # HTTP API + GraphQL on :8000
uv run python worker.py            # separate process: drains ingest_jobs
```

## API

All endpoints require `Authorization: Bearer <api_key>`.

- `POST /content` — `{ "text": "...", "metadata": { ... } }` → `202 { "job_id": "..." }`
- `GET /content/{job_id}` — job status + relevance_reason/error
- `PUT /config` — `{ "relevance_prompt", "entity_types": [...], "relationship_types": [...] }`
- `POST /graphql` — generic read schema:

```graphql
{ nodes(type: "Person", search: "ada", limit: 50) { id type name summary edges { type target { name } } } }
```

## Test

```bash
uv run pytest        # Neo4j/Postgres-backed tests skip when those stores are unreachable
```
````

- [ ] **Step 8: Full green sweep**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add main.py seed.py README.md test_main.py
git commit -m "feat: assemble FastAPI app, seed org+config+api key, run docs"
```

---

## Task 12: Delete the `app/` React frontend

The API-only product has no UI. This is a discrete, flagged deletion — approve/reject independently of the engine work.

**Files:**
- Delete: `app/` (entire directory)

**Interfaces:** none (the frontend has no runtime coupling to `ingestion/`).

- [ ] **Step 1: Confirm no cross-references from the engine**

Run:
```bash
cd /home/steve/Source/sinpi/anything_handwritten
grep -rIl "app/" ingestion/ --include="*.py" || echo "no references from ingestion"
```
Expected: `no references from ingestion`.

- [ ] **Step 2: Delete the frontend**

```bash
git rm -r app
```

- [ ] **Step 3: Verify the engine still builds green**

```bash
cd ingestion && uv run ruff check . && uv run mypy . && uv run pytest -q
```
Expected: PASS (unaffected by the frontend removal).

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: delete React frontend (API-only product)"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
| --- | --- |
| Async `POST /content` → `ingest_jobs(pending)` → 202 | Task 5 |
| `GET /content/{job_id}` status | Task 5 |
| `PUT /config` upsert | Task 6 |
| API-key auth → org, reject missing/invalid/revoked | Task 4 (+ enforced in 5/6/10) |
| Relevance filter (binary, structured, fail-safe) | Task 7 |
| Extraction constrained to entity types AND relationship types | Task 8 |
| Provenance references the ingest job id | Task 8 |
| Worker: claim SKIP LOCKED, relevance→extract→merge, transitions, bounded retry | Task 9 |
| Generic GraphQL schema (`nodes`/`node`/`Node`/`Edge`), org-scoped, full-text search | Task 10 |
| Postgres data model (`org_configs`, `api_keys`, `ingest_jobs`) + drop old tables | Task 2 |
| Demolition (transforms/gates/rss/prefect, models, config, Prefect concurrency) | Task 1 |
| Deps: add fastapi/uvicorn/strawberry, remove prefect | Task 3 |
| main.py = uvicorn app; worker.py entrypoint; seed rewrite; README | Task 11 |
| Delete `app/` frontend | Task 12 |
| Tests: relevance, worker lifecycle + no-double-claim, extraction constraints, GraphQL isolation, auth, ingest endpoint | Tasks 4–10 |

No spec requirement is left without a task.

**2. Placeholder scan**

No `TBD`/`TODO`/`similar to Task N`/"add error handling" left. Every code step carries complete, runnable code. The only intentional non-implementation is `downgrade()` raising `NotImplementedError` in the one-way dev migration (Task 2) — explicit and justified.

**3. Type consistency**

- `merge_content(org_id, content, entity_types, relationship_types, job_id) -> MergeResult` — defined Task 8, called identically in worker (Task 9) and tested in Task 8.
- `judge_relevance(relevance_prompt: str, content: str) -> RelevanceResult` — defined Task 7, called identically in worker (Task 9).
- `RelevanceResult{relevant, reason}` — Task 7, imported in Task 9 tests.
- `require_org(...) -> str` — Task 4, used as a dependency in Tasks 5/6/10.
- `hash_key`/`generate_api_key` — Task 4, reused in Tasks 5/6/10/11 tests and seed.
- `graph_read.query_nodes/query_node/query_edges` return-key shape (`id,type,name,summary`, edges `{type,target}`) — Task 10, consumed by `graph_api._to_node`/`Node.edges` with matching keys.
- `IngestJob.job_metadata` (attr) ↔ `metadata` (column) — consistent across models (Task 2), migration (Task 2), route (Task 5).
- `JobStatus` values match the migration `status` strings and the worker transitions.
- `build_extraction_messages`/`extract_knowledge` new signatures (Task 8) are matched by the updated `test_knowledge.py` and by `merge_content`'s call site.

No signature drift found.

---

## Notes for the planner (me)

Real ambiguities surfaced against the current code — flagged rather than silently guessed:

1. **No extraction prompt in `OrgConfig`.** The old `run_knowledge_transform` drove extraction from a per-`Transformation` free-form `prompt`. The spec's `OrgConfig` has only `relevance_prompt`, `entity_types`, `relationship_types` — no extraction prompt. I dropped the free-form prompt from `build_extraction_messages` and rely on a fixed instruction plus the configured type lists (org intent is already expressed through the types and the relevance filter). If you want per-org extraction guidance, add an `extraction_prompt` column to `OrgConfig` and thread it through `build_extraction_messages`/`extract_knowledge`/`merge_content` — a small, localized change.

2. **Model + LLM params moved from per-`Transformation` to global `config`.** The old code read `model` and `params` (LLM knobs) from the `Transformation` row. `OrgConfig` has neither, so `merge_content`/`judge_relevance` use `config.LLM_MODEL` and empty `llm_params`. If per-org model selection matters, it needs new config columns; out of scope per the spec.

3. **`key_hash` is SHA-256, not argon2.** The spec says "the key hashes to an `api_keys` row." A salted argon2 hash (as `seed.py` uses for passwords) cannot be looked up by hash. API keys are high-entropy random tokens, so a fast deterministic SHA-256 is the correct and standard choice here and enables the `WHERE key_hash = :h` lookup. Flagging because the repo already ships `argon2-cffi` and you might expect it reused.

4. **"Fails safe" resolved as raise-and-retry (controller decision).** An earlier draft returned `relevant=False` on empty/unparseable relevance output, but that records the job as `skipped` — indistinguishable from a genuine "not relevant" verdict — silently dropping content that was never actually judged. Since durable, non-lossy ingest is the whole reason for the Postgres queue, `judge_relevance` now **raises `RelevanceError`** on undecidable output; the worker's exception path retries it (back to `pending` under the attempt cap) and finally marks it `failed` (visible via `GET /content/{job_id}`). Only a real `relevant=False` verdict from a successful LLM call becomes `skipped`. Never lets unjudged content into the graph, and never silently drops it.

5. **Bounded retry re-queues before failing.** The spec says "on exception → failed (+error), increment attempts" and also "a job over the attempt cap stays failed." I implemented: increment `attempts`, set `error`; status → `pending` while `attempts < WORKER_MAX_ATTEMPTS`, else `failed`. That yields actual retries. If you'd rather a single failure be terminal (no retry), set `WORKER_MAX_ATTEMPTS = 1`.

6. **GraphQL test re-keys the seeded Neo4j graph to the Postgres org id.** Because `require_org` returns the Postgres `orgs.id` while `knowledge.py` write helpers accept an arbitrary `org_id` string, the Task 10 test seeds the graph then rewrites `org_id` to the Postgres org's id so the auth-derived scope matches. In production the worker always passes the real Postgres org id, so this is a test-seam artifact only — noting it so a reviewer doesn't read it as a design smell.

7. **Dead RSS deps removed in Task 3.** `feedparser`, `trafilatura`, `curl-cffi` were only used by the deleted `rss_feeds.py`; I remove them alongside `prefect`. The spec only names `prefect` for removal — flagging the extra cleanup in case any is wanted for a future POST-source adapter (the spec says such adapters are external producers, so removal is consistent).
