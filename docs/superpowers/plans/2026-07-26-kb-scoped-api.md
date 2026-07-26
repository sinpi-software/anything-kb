# KB-scoped API and Role Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a request name which knowledge base it acts on, add endpoints to create/rename/delete knowledge bases, and enforce the four roles already in the model — with no user-visible change to the current desk UI.

**Architecture:** A new `memberships.py` owns role ranking, membership resolution, and knowledge-base creation. A new `routes_knowledge_bases.py` exposes CRUD. The four existing cookie-authenticated modules each grow a KB-scoped twin route beside their existing unscoped one, both delegating to one extracted body function, both applying the same role check.

**Tech Stack:** FastAPI, SQLAlchemy (Postgres), Strawberry GraphQL, neo4j driver, pytest.

**Spec:** `docs/superpowers/specs/2026-07-26-kb-scoped-api-design.md` — read it first.

## Global Constraints

- **This is sub-project A of two.** Sub-project B (the desk UI: `/app/:kbId/*`, switcher, management page) is NOT in scope. Do not touch anything under `app/`.
- **The current desk UI must keep working, untouched.** Every existing unscoped route keeps its exact path and behaviour. `GET /api/keys`, `PUT /api/config`, `POST /api/content`, `POST /api/graphql` all continue to resolve the knowledge base via `home_knowledge_base_id`.
- **`accounts.home_knowledge_base_id` stays.** It is used only by the legacy unscoped routes. Sub-project B deletes it. Do not remove it.
- **404, never 403, for a knowledge base the caller is not a member of** — including one that does not exist, and one where they lack the required role. A 403 would confirm existence. This is a security property; assert it explicitly in tests.
- **Role floors, exact values:** `reader` reads graph and config; `editor` also ingests; `admin` also writes config and manages API keys; `owner` also renames and deletes.
- **Rank order, exact:** `{"reader": 0, "editor": 1, "admin": 2, "owner": 3}`.
- **Legacy routes carry the same role checks as their scoped twins.** They differ only in how the knowledge base is chosen. Anything less is an authorization bypass.
- **Real route prefixes** — do not guess from module names: `routes_keys.py` → `/api/keys`; `routes_settings.py` → `/api/config`; `routes_ingest.py` → **`/api/content`**; `graph_api.py`'s cookie router → `/api/graphql`.
- Existing commands must stay green: `uv run pytest`, `uv run ruff check .`, `uv run mypy .` in `ingestion/`. `mypy` runs `strict = true`; `ruff` uses `line-length = 120`.
- Postgres-backed tests use the existing `requires_pg` skip marker and the `_purge_user` teardown idiom already in `test_routes_keys.py`. Follow it — these tests run against a real database.

---

### Task 1: Membership resolution and the role ladder

The primitive every later task depends on. Lives in its own module rather than `accounts.py`, which already owns sessions, users and CSRF at 173 lines.

**Files:**
- Create: `ingestion/memberships.py`
- Test: `ingestion/test_memberships.py`

**Interfaces:**
- Consumes: `models.KnowledgeBaseUser`.
- Produces:
  - `ROLE_RANK: dict[str, int]` — `{"reader": 0, "editor": 1, "admin": 2, "owner": 3}`
  - `membership_role(session, user_id, kb_id) -> str | None`
  - `require_membership(session, user_id, kb_id, min_role) -> str` — returns the caller's role, raises `HTTPException(404)` otherwise.

- [ ] **Step 1: Write the failing tests**

Create `ingestion/test_memberships.py`:

```python
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
        with get_postgres_session() as s:
            with pytest.raises(HTTPException) as excinfo:
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
        with get_postgres_session() as s:
            with pytest.raises(HTTPException) as excinfo:
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/steve/Source/sinpi/anything_handwritten/ingestion
uv run pytest test_memberships.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'memberships'`.

- [ ] **Step 3: Write the implementation**

Create `ingestion/memberships.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest test_memberships.py -v
```

Expected: PASS (5 tests).

- [ ] **Step 5: Run lint, types, and the full suite**

```bash
uv run pytest && uv run ruff check . && uv run mypy .
```

Expected: all green, 151 existing tests still passing.

- [ ] **Step 6: Commit**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
git add ingestion/memberships.py ingestion/test_memberships.py
git commit -m "feat(auth): membership resolution and the role ladder

require_membership returns the caller's role or raises 404 — never 403,
because a 403 confirms a knowledge base exists to someone with no
membership in it, and tenancy is the graph's only isolation."
```

---

### Task 2: One knowledge-base creation path

Three places would otherwise know how to build a knowledge base, and the two that exist already disagree: `routes_auth.register` creates the knowledge base and owner membership but **no config**, while `seed.py` creates all three. Unifying is a precondition for adding a third caller in Task 4.

**Files:**
- Modify: `ingestion/memberships.py` (add creation + default config constants)
- Modify: `ingestion/routes_auth.py:102-115` (register uses the helper)
- Modify: `ingestion/seed.py:57-95` (seed uses the helper)
- Test: `ingestion/test_memberships.py` (extend)

**Interfaces:**
- Consumes: `ROLE_RANK` from Task 1.
- Produces:
  - `DEFAULT_INTERESTS: str`
  - `DEFAULT_ENTITY_TYPES: list[dict[str, Any]]`, `DEFAULT_RELATIONSHIP_TYPES: list[dict[str, Any]]`
  - `create_knowledge_base(session, user, name, charter=None) -> KnowledgeBase` — adds the knowledge base, the creator's `owner` membership, and a default config. **Flushes but does not commit**; the caller commits.

- [ ] **Step 1: Write the failing test**

Append to `ingestion/test_memberships.py`:

```python
@requires_pg
def test_create_knowledge_base_makes_membership_and_config() -> None:
    """register/ and seed.py disagreed before this helper existed: register made no
    config, so a registered user's knowledge base had none. One path, three artifacts."""
    from db import get_postgres_session
    from memberships import create_knowledge_base
    from models import KnowledgeBaseConfig, KnowledgeBaseUser, User

    user_id = kb_id = None
    try:
        with get_postgres_session() as s:
            user = User(email=f"create-{uuid.uuid4()}@example.com", password_hash="x", name="t")
            s.add(user)
            s.flush()
            kb = create_knowledge_base(s, user, "My second brain", charter="notes")
            s.commit()
            user_id, kb_id = str(user.id), str(kb.id)

        with get_postgres_session() as s:
            membership = s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.knowledge_base_id == kb_id).one()
            assert membership.role == "owner"
            cfg = s.query(KnowledgeBaseConfig).filter(KnowledgeBaseConfig.knowledge_base_id == kb_id).one()
            assert cfg.interests
            assert len(cfg.entity_types) == 4
            assert len(cfg.relationship_types) == 4
    finally:
        if user_id and kb_id:
            from db import get_postgres_session as gs

            with gs() as s:
                s.query(KnowledgeBaseConfig).filter(KnowledgeBaseConfig.knowledge_base_id == kb_id).delete(
                    synchronize_session=False
                )
                s.commit()
            _purge(user_id, kb_id)


@requires_pg
def test_register_now_creates_a_config() -> None:
    """The bug this unification fixes: a registered user's knowledge base had no config."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from db import get_postgres_session
    from models import KnowledgeBaseConfig, KnowledgeBaseUser, User
    from routes_auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    client = TestClient(app, base_url="https://testserver", headers={"Origin": "http://localhost:5173"})

    email = f"reg-cfg-{uuid.uuid4()}@example.com"
    try:
        resp = client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        assert resp.status_code == 201
        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            kb_id = s.query(KnowledgeBaseUser.knowledge_base_id).filter(KnowledgeBaseUser.user_id == user.id).one()[0]
            cfg = s.query(KnowledgeBaseConfig).filter(KnowledgeBaseConfig.knowledge_base_id == kb_id).one_or_none()
        assert cfg is not None, "register must create a default config"
    finally:
        from test_routes_keys import _purge_user

        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one_or_none()
            if user is not None:
                ids = [
                    r[0]
                    for r in s.query(KnowledgeBaseUser.knowledge_base_id)
                    .filter(KnowledgeBaseUser.user_id == user.id)
                    .all()
                ]
                if ids:
                    s.query(KnowledgeBaseConfig).filter(KnowledgeBaseConfig.knowledge_base_id.in_(ids)).delete(
                        synchronize_session=False
                    )
                    s.commit()
        _purge_user(email)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest test_memberships.py -k "create_knowledge_base or register_now" -v
```

Expected: FAIL — `ImportError: cannot import name 'create_knowledge_base'`, and the register test fails on `cfg is not None`.

- [ ] **Step 3: Add the creation helper**

Append to `ingestion/memberships.py`:

```python
# The starting vocabulary a new knowledge base gets. Lived in seed.py until three
# callers needed it; a knowledge base created without a config makes the worker burn
# an LLM call discovering it has no types.
DEFAULT_INTERESTS = "Is this content about technology, science, or business news?"

DEFAULT_ENTITY_TYPES: list[dict[str, Any]] = [
    {"name": "Person", "description": "A specific, named individual human."},
    {"name": "Organization", "description": "A company, agency, institution, or group."},
    {"name": "Place", "description": "A geographic location — city, country, region, or venue."},
    {"name": "Topic", "description": "A subject, field, technology, or theme."},
]

DEFAULT_RELATIONSHIP_TYPES: list[dict[str, Any]] = [
    {"name": "Works at", "description": "A person is employed by or leads an organization."},
    {"name": "Located in", "description": "An entity is situated in a place."},
    {"name": "Related to", "description": "A general association between two entities."},
    {"name": "Founded", "description": "A person or organization established an organization."},
]


def create_knowledge_base(session: OrmSession, user: Any, name: str, charter: str | None = None) -> Any:
    """A knowledge base, the creator's owner membership, and a default config.

    Flushes so the caller can read `kb.id`, but does not commit — callers create a
    knowledge base as part of a larger transaction (registration creates a user too).
    """
    from models import KnowledgeBase, KnowledgeBaseConfig, KnowledgeBaseUser, KnowledgeBaseUserRole

    kb = KnowledgeBase(name=name, charter=charter, created_by_id=user.id, updated_by_id=user.id)
    session.add(kb)
    session.flush()
    session.add(
        KnowledgeBaseUser(
            knowledge_base_id=kb.id,
            user_id=user.id,
            role=KnowledgeBaseUserRole.OWNER.value,
            created_by_id=user.id,
            updated_by_id=user.id,
        )
    )
    session.add(
        KnowledgeBaseConfig(
            knowledge_base_id=kb.id,
            interests=DEFAULT_INTERESTS,
            discover_types=True,
            entity_types=DEFAULT_ENTITY_TYPES,
            relationship_types=DEFAULT_RELATIONSHIP_TYPES,
        )
    )
    return kb
```

- [ ] **Step 4: Rewire `register`**

In `ingestion/routes_auth.py`, replace the knowledge-base block (currently lines 102-115, from `knowledge_base_name = ...` through the `session.add(KnowledgeBaseUser(...))` call) with:

```python
        knowledge_base_name = (payload.knowledge_base_name or "").strip() or "My workspace"
        create_knowledge_base(session, user, knowledge_base_name)
```

Add `from memberships import create_knowledge_base` to the imports. Remove `KnowledgeBase`, `KnowledgeBaseUser` and `KnowledgeBaseUserRole` from the `models` import **only if** nothing else in the file uses them — check with `grep -n "KnowledgeBaseUser\|KnowledgeBaseUserRole\|KnowledgeBase\b" ingestion/routes_auth.py` before editing, and leave any that are still referenced.

- [ ] **Step 5: Rewire `seed.py`**

In `ingestion/seed.py`, the knowledge base, membership and config are currently created by three separate `get_or_create` calls. Replace them with a lookup plus the helper, preserving idempotency — `seed.py` is run repeatedly and must not create a second knowledge base:

```python
        knowledge_base = (
            session.query(KnowledgeBase).filter(KnowledgeBase.name == "Default Knowledge Base").one_or_none()
        )
        org_created = knowledge_base is None
        if knowledge_base is None:
            knowledge_base = create_knowledge_base(
                session, admin, "Default Knowledge Base", charter="This is the default knowledge base."
            )
        membership_created = config_created = org_created
```

Add `from memberships import create_knowledge_base` to the imports. Delete the now-unused `_membership` and `_cfg` `get_or_create` blocks and the inline `entity_types`/`relationship_types` literals. Keep the `get_or_create` helper itself — the admin `User` still uses it. Keep the existing printed summary lines working: `org_created`, `membership_created` and `config_created` are all set above.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest test_memberships.py -v
```

Expected: PASS (7 tests).

- [ ] **Step 7: Verify seed.py is still idempotent against the real database**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
docker compose exec -T ingestion-api python seed.py
docker compose exec -T ingestion-api python seed.py
docker compose exec -T postgres psql -U ingestion -d ingestion -tAc \
  "SELECT count(*) FROM knowledge_bases WHERE name = 'Default Knowledge Base'"
```

Expected: both runs succeed, printing `exists` for the knowledge base on the second, and the count is exactly `1`. A count of 2 means idempotency broke — fix before continuing.

- [ ] **Step 8: Run lint, types, and the full suite**

```bash
cd ingestion && uv run pytest && uv run ruff check . && uv run mypy .
```

Expected: all green.

- [ ] **Step 9: Commit**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
git add ingestion/memberships.py ingestion/test_memberships.py ingestion/routes_auth.py ingestion/seed.py
git commit -m "refactor: one knowledge-base creation path

register created a knowledge base and membership but no config, while
seed.py created all three — so a registered user's knowledge base had
none, which is why the worker burned an LLM call discovering it had no
types. create_knowledge_base is now the only way to make one."
```

---

### Task 3: Purge a knowledge base's graph

Delete crosses two stores. This is the Neo4j half, isolated so it can be tested alone.

**Files:**
- Modify: `ingestion/neo4j_client.py`
- Test: `ingestion/test_neo4j_purge.py`

**Interfaces:**
- Consumes: `neo4j_client.get_driver`.
- Produces: `purge_knowledge_base(knowledge_base_id: str) -> int` — deletes every node carrying that `knowledge_base_id`, returns the count deleted.

- [ ] **Step 1: Write the failing test**

Create `ingestion/test_neo4j_purge.py`:

```python
import os
import uuid

os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")

import pytest


def _neo4j_available() -> bool:
    try:
        from neo4j_client import get_driver

        with get_driver().session() as s:
            s.run("RETURN 1").consume()
        return True
    except Exception:
        return False


requires_neo4j = pytest.mark.skipif(not _neo4j_available(), reason="Neo4j not reachable")


@requires_neo4j
def test_purge_deletes_only_the_named_knowledge_base() -> None:
    """Both labels carry knowledge_base_id and DETACH takes the relationships, so the
    purge is label-agnostic. The second knowledge base proves it is also scoped."""
    from neo4j_client import get_driver, purge_knowledge_base

    doomed, kept = str(uuid.uuid4()), str(uuid.uuid4())
    with get_driver().session() as s:
        s.run(
            "CREATE (a:Entity {id: $a, knowledge_base_id: $doomed, name: 'A'}) "
            "CREATE (b:Entity {id: $b, knowledge_base_id: $doomed, name: 'B'}) "
            "CREATE (c:Source {knowledge_base_id: $doomed, job_id: $j}) "
            "CREATE (k:Entity {id: $k, knowledge_base_id: $kept, name: 'K'}) "
            "CREATE (a)-[:RELATED {knowledge_base_id: $doomed}]->(b) "
            "CREATE (a)-[:MENTIONED_IN]->(c)",
            a=str(uuid.uuid4()), b=str(uuid.uuid4()), k=str(uuid.uuid4()),
            j=str(uuid.uuid4()), doomed=doomed, kept=kept,
        ).consume()

    try:
        deleted = purge_knowledge_base(doomed)
        assert deleted == 3
        with get_driver().session() as s:
            left = s.run(
                "MATCH (n) WHERE n.knowledge_base_id = $kb RETURN count(n) AS c", kb=doomed
            ).single()["c"]
            survivors = s.run(
                "MATCH (n) WHERE n.knowledge_base_id = $kb RETURN count(n) AS c", kb=kept
            ).single()["c"]
        assert left == 0
        assert survivors == 1, "purge must not touch another knowledge base"
    finally:
        with get_driver().session() as s:
            s.run("MATCH (n) WHERE n.knowledge_base_id IN [$a, $b] DETACH DELETE n", a=doomed, b=kept).consume()


@requires_neo4j
def test_purge_of_an_empty_knowledge_base_is_zero_not_an_error() -> None:
    """Delete must be re-runnable after a partial failure, so a second purge is a no-op."""
    from neo4j_client import purge_knowledge_base

    assert purge_knowledge_base(str(uuid.uuid4())) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/steve/Source/sinpi/anything_handwritten/ingestion
uv run pytest test_neo4j_purge.py -v
```

Expected: FAIL — `ImportError: cannot import name 'purge_knowledge_base'`.

- [ ] **Step 3: Write the implementation**

Append to `ingestion/neo4j_client.py`:

```python
def purge_knowledge_base(knowledge_base_id: str) -> int:
    """Delete every node belonging to a knowledge base. Returns the node count deleted.

    Label-agnostic on purpose: both labels (Entity, Source) carry knowledge_base_id and
    a new one would too, so matching on the property cannot miss a label someone adds
    later. DETACH removes the relationships, which carry the same property.
    """
    with get_driver().session() as session:
        record = session.run(
            "MATCH (n) WHERE n.knowledge_base_id = $kb DETACH DELETE n RETURN count(n) AS deleted",
            kb=knowledge_base_id,
        ).single()
        return int(record["deleted"]) if record is not None else 0
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest test_neo4j_purge.py -v
```

Expected: PASS (2 tests).

- [ ] **Step 5: Run lint, types, and the full suite**

```bash
uv run pytest && uv run ruff check . && uv run mypy .
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
git add ingestion/neo4j_client.py ingestion/test_neo4j_purge.py
git commit -m "feat(neo4j): purge a knowledge base's graph

Label-agnostic property match, so a label added later cannot be missed."
```

---

### Task 4: List, create and rename knowledge bases

**Files:**
- Modify: `ingestion/schemas.py`
- Create: `ingestion/routes_knowledge_bases.py`
- Modify: `ingestion/main.py:57-64` (register the router)
- Test: `ingestion/test_routes_knowledge_bases.py`

**Interfaces:**
- Consumes: `require_membership`, `create_knowledge_base` (Tasks 1-2).
- Produces: router at prefix `/api/knowledge-bases` with `GET ""`, `POST ""`, `PATCH "/{kb_id}"`. Response model `KnowledgeBaseOut{id, name, charter, role, created_at}`. Task 5 adds `DELETE` to this same file.

- [ ] **Step 1: Add the schemas**

Append to `ingestion/schemas.py`:

```python
class KnowledgeBaseOut(BaseModel):
    id: str
    name: str
    charter: str | None
    role: str
    created_at: datetime


class KnowledgeBaseCreateRequest(BaseModel):
    name: str
    charter: str | None = None


class KnowledgeBaseUpdateRequest(BaseModel):
    name: str | None = None
    charter: str | None = None


class KnowledgeBaseDeleteRequest(BaseModel):
    confirm_name: str
```

`datetime` is already imported in this file (`ApiKeyOut.created_at` uses it); confirm with `grep -n "^from datetime\|^import datetime" ingestion/schemas.py` and add the import only if absent.

- [ ] **Step 2: Write the failing tests**

Create `ingestion/test_routes_knowledge_bases.py`:

```python
import os
import uuid
from collections.abc import Iterator

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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
LOCALHOST_ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    from routes_auth import router as auth_router
    from routes_knowledge_bases import router as kb_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(kb_router)
    # base_url must be https:// — the session cookie is Secure, so httpx only sends it
    # back over https, matching real browser behavior.
    yield TestClient(app, base_url="https://testserver", headers=LOCALHOST_ORIGIN)


def _register_and_verify(client: TestClient, email: str) -> None:
    from db import get_postgres_session
    from models import User

    client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one()
        user.email_verified = True
        s.commit()


def _purge_everything(email: str) -> None:
    from db import get_postgres_session
    from models import (
        ApiKey, AuthSession, EmailToken, IngestJob, KnowledgeBase,
        KnowledgeBaseConfig, KnowledgeBaseUser, User,
    )

    with get_postgres_session() as s:
        user = s.query(User).filter(User.email == email).one_or_none()
        if user is None:
            return
        ids = [r[0] for r in s.query(KnowledgeBaseUser.knowledge_base_id).filter(
            KnowledgeBaseUser.user_id == user.id).all()]
        if ids:
            for model in (ApiKey, IngestJob, KnowledgeBaseConfig):
                s.query(model).filter(model.knowledge_base_id.in_(ids)).delete(synchronize_session=False)
        s.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
        s.query(EmailToken).filter(EmailToken.user_id == user.id).delete(synchronize_session=False)
        s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).delete(synchronize_session=False)
        if ids:
            s.query(KnowledgeBase).filter(KnowledgeBase.id.in_(ids)).delete(synchronize_session=False)
        s.query(User).filter(User.id == user.id).delete(synchronize_session=False)
        s.commit()


@requires_pg
def test_list_returns_the_registration_knowledge_base_with_role(client: TestClient) -> None:
    email = f"kb-list-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        resp = client.get("/api/knowledge-bases")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["name"] == "My workspace"
        assert body[0]["role"] == "owner"
    finally:
        _purge_everything(email)


@requires_pg
def test_create_adds_a_second_knowledge_base_owned_by_the_caller(client: TestClient) -> None:
    email = f"kb-create-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        resp = client.post("/api/knowledge-bases", json={"name": "Second brain"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 201
        assert resp.json()["role"] == "owner"
        assert len(client.get("/api/knowledge-bases").json()) == 2
    finally:
        _purge_everything(email)


@requires_pg
def test_create_is_403_when_email_unverified(client: TestClient) -> None:
    """Mirrors routes_keys.py's guard — a knowledge base is where API keys live."""
    email = f"kb-unver-{uuid.uuid4()}@example.com"
    try:
        client.post("/api/auth/register", json={"email": email, "password": "hunter22"})
        resp = client.post("/api/knowledge-bases", json={"name": "Nope"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 403
    finally:
        _purge_everything(email)


@requires_pg
def test_create_rejects_a_blank_name(client: TestClient) -> None:
    email = f"kb-blank-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        resp = client.post("/api/knowledge-bases", json={"name": "   "}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 422
    finally:
        _purge_everything(email)


@requires_pg
def test_rename_changes_the_name(client: TestClient) -> None:
    email = f"kb-rename-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        kb_id = client.get("/api/knowledge-bases").json()[0]["id"]
        resp = client.patch(f"/api/knowledge-bases/{kb_id}", json={"name": "Renamed"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"
    finally:
        _purge_everything(email)


@requires_pg
def test_another_users_knowledge_base_is_404_everywhere(client: TestClient) -> None:
    """404 not 403 — a 403 would confirm the knowledge base exists."""
    owner_email = f"kb-owner-{uuid.uuid4()}@example.com"
    other_email = f"kb-other-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, owner_email)
        kb_id = client.get("/api/knowledge-bases").json()[0]["id"]
        client.post("/api/auth/logout", headers=LOCALHOST_ORIGIN)
        _register_and_verify(client, other_email)

        assert client.get("/api/knowledge-bases").json()[0]["id"] != kb_id
        resp = client.patch(f"/api/knowledge-bases/{kb_id}", json={"name": "Stolen"}, headers=LOCALHOST_ORIGIN)
        assert resp.status_code == 404
    finally:
        _purge_everything(owner_email)
        _purge_everything(other_email)
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest test_routes_knowledge_bases.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'routes_knowledge_bases'`.

- [ ] **Step 4: Write the router**

Create `ingestion/routes_knowledge_bases.py`:

```python
"""Knowledge-base CRUD, session-authenticated (cookie auth via `current_user`).

Membership decides visibility and rank decides capability — see memberships.py. Every
refusal here is a 404, including one caused by role rather than existence.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from accounts import current_user, require_csrf
from db import get_postgres_session
from memberships import create_knowledge_base, require_membership
from models import KnowledgeBase, KnowledgeBaseUser, User
from sanitize import sanitize
from schemas import KnowledgeBaseCreateRequest, KnowledgeBaseOut, KnowledgeBaseUpdateRequest

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
```

- [ ] **Step 5: Register the router**

In `ingestion/main.py`, beside the other `app.include_router(...)` calls (currently lines 57-64), add:

```python
app.include_router(knowledge_bases_router)
```

with `from routes_knowledge_bases import router as knowledge_bases_router` alongside the sibling router imports.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest test_routes_knowledge_bases.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 7: Run lint, types, and the full suite**

```bash
uv run pytest && uv run ruff check . && uv run mypy .
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
git add ingestion/schemas.py ingestion/routes_knowledge_bases.py ingestion/main.py \
        ingestion/test_routes_knowledge_bases.py
git commit -m "feat(api): list, create and rename knowledge bases"
```

---

### Task 5: Delete a knowledge base

The riskiest endpoint: permanent, and it spans two stores that cannot be made atomic.

**Files:**
- Modify: `ingestion/routes_knowledge_bases.py`
- Test: `ingestion/test_routes_knowledge_bases.py` (extend)

**Interfaces:**
- Consumes: `purge_knowledge_base` (Task 3), `require_membership` (Task 1), `KnowledgeBaseDeleteRequest` (Task 4).
- Produces: `DELETE /api/knowledge-bases/{kb_id}` → 204.

- [ ] **Step 1: Write the failing tests**

Append to `ingestion/test_routes_knowledge_bases.py`:

```python
@requires_pg
def test_delete_removes_the_knowledge_base_and_its_children(client: TestClient) -> None:
    from db import get_postgres_session
    from models import KnowledgeBase, KnowledgeBaseConfig, KnowledgeBaseUser

    email = f"kb-del-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        kb_id = client.post(
            "/api/knowledge-bases", json={"name": "Doomed"}, headers=LOCALHOST_ORIGIN
        ).json()["id"]

        resp = client.request(
            "DELETE", f"/api/knowledge-bases/{kb_id}",
            json={"confirm_name": "Doomed"}, headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 204

        with get_postgres_session() as s:
            assert s.get(KnowledgeBase, kb_id) is None
            assert s.query(KnowledgeBaseUser).filter(
                KnowledgeBaseUser.knowledge_base_id == kb_id).count() == 0
            assert s.query(KnowledgeBaseConfig).filter(
                KnowledgeBaseConfig.knowledge_base_id == kb_id).count() == 0
    finally:
        _purge_everything(email)


@requires_pg
def test_delete_refuses_a_mismatched_confirm_name(client: TestClient) -> None:
    email = f"kb-delname-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        kb_id = client.post(
            "/api/knowledge-bases", json={"name": "Doomed"}, headers=LOCALHOST_ORIGIN
        ).json()["id"]
        resp = client.request(
            "DELETE", f"/api/knowledge-bases/{kb_id}",
            json={"confirm_name": "doomed"}, headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 422
        assert len(client.get("/api/knowledge-bases").json()) == 2
    finally:
        _purge_everything(email)


@requires_pg
def test_delete_refuses_the_callers_last_knowledge_base(client: TestClient) -> None:
    """Otherwise a user can delete themselves into a state with nowhere to land."""
    email = f"kb-dellast-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        only = client.get("/api/knowledge-bases").json()[0]
        resp = client.request(
            "DELETE", f"/api/knowledge-bases/{only['id']}",
            json={"confirm_name": only["name"]}, headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 409
        assert len(client.get("/api/knowledge-bases").json()) == 1
    finally:
        _purge_everything(email)


@requires_pg
def test_delete_of_another_users_knowledge_base_is_404(client: TestClient) -> None:
    owner_email = f"kb-delown-{uuid.uuid4()}@example.com"
    other_email = f"kb-deloth-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, owner_email)
        kb_id = client.post(
            "/api/knowledge-bases", json={"name": "Mine"}, headers=LOCALHOST_ORIGIN
        ).json()["id"]
        client.post("/api/auth/logout", headers=LOCALHOST_ORIGIN)
        _register_and_verify(client, other_email)
        resp = client.request(
            "DELETE", f"/api/knowledge-bases/{kb_id}",
            json={"confirm_name": "Mine"}, headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 404
    finally:
        _purge_everything(owner_email)
        _purge_everything(other_email)


@requires_pg
def test_delete_is_rerunnable_after_a_graph_only_success(client: TestClient, monkeypatch) -> None:
    """The failure the Neo4j-first order is designed for: the graph purge succeeded and
    the Postgres half did not. The knowledge base is still listed and deleting again
    converges, rather than stranding graph nodes whose owning row is gone."""
    import routes_knowledge_bases

    email = f"kb-delretry-{uuid.uuid4()}@example.com"
    try:
        _register_and_verify(client, email)
        kb_id = client.post(
            "/api/knowledge-bases", json={"name": "Flaky"}, headers=LOCALHOST_ORIGIN
        ).json()["id"]

        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("postgres unavailable")

        monkeypatch.setattr(routes_knowledge_bases, "_delete_postgres_rows", boom)
        with pytest.raises(RuntimeError):
            client.request(
                "DELETE", f"/api/knowledge-bases/{kb_id}",
                json={"confirm_name": "Flaky"}, headers=LOCALHOST_ORIGIN,
            )
        assert any(kb["id"] == kb_id for kb in client.get("/api/knowledge-bases").json())

        monkeypatch.undo()
        resp = client.request(
            "DELETE", f"/api/knowledge-bases/{kb_id}",
            json={"confirm_name": "Flaky"}, headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 204
    finally:
        _purge_everything(email)
```

Note: the `client` fixture must not swallow exceptions for the retry test. If `TestClient` re-raises server exceptions by default in this version (it does unless constructed with `raise_server_exceptions=False`), the `pytest.raises(RuntimeError)` above is correct as written.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest test_routes_knowledge_bases.py -k delete -v
```

Expected: FAIL — 405 Method Not Allowed, since `DELETE` is not routed yet.

- [ ] **Step 3: Write the delete endpoint**

Append to `ingestion/routes_knowledge_bases.py`, and add `KnowledgeBaseDeleteRequest` to the `schemas` import, `purge_knowledge_base` from `neo4j_client`, and `ApiKey`, `IngestJob`, `KnowledgeBaseConfig` to the `models` import:

```python
def _delete_postgres_rows(session: object, kb_id: str) -> None:
    """Every child row, then the knowledge base itself.

    Explicit because no knowledge_base_id foreign key declares ON DELETE CASCADE —
    only the users.id keys on sessions and email_tokens do. Named as a seam so a test
    can simulate the graph-succeeded / Postgres-failed split.
    """
    for model in (IngestJob, ApiKey, KnowledgeBaseConfig, KnowledgeBaseUser):
        session.query(model).filter(model.knowledge_base_id == kb_id).delete(synchronize_session=False)  # type: ignore[attr-defined]
    session.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).delete(synchronize_session=False)  # type: ignore[attr-defined]


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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest test_routes_knowledge_bases.py -v
```

Expected: PASS (11 tests).

- [ ] **Step 5: Run lint, types, and the full suite**

```bash
uv run pytest && uv run ruff check . && uv run mypy .
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
git add ingestion/routes_knowledge_bases.py ingestion/test_routes_knowledge_bases.py
git commit -m "feat(api): delete a knowledge base, graph first

The two stores cannot be atomic. Purging Neo4j first means a half-failure
leaves an empty but still-listed knowledge base that deleting again
converges on; the reverse strands graph nodes nothing can reclaim."
```

---

### Task 6: Scope API keys and config

The first half of the dual-route pattern. Each endpoint's body becomes a function taking `knowledge_base_id`; a legacy wrapper resolves it from `home_knowledge_base_id` and a scoped wrapper from the path. Both apply the same role check.

**Files:**
- Modify: `ingestion/routes_keys.py`
- Modify: `ingestion/routes_settings.py`
- Test: `ingestion/test_routes_keys.py`, `ingestion/test_routes_settings.py` (extend)

**Interfaces:**
- Consumes: `require_membership` (Task 1).
- Produces: `/api/knowledge-bases/{kb_id}/keys` (GET, POST), `/api/knowledge-bases/{kb_id}/keys/{key_id}` (DELETE), `/api/knowledge-bases/{kb_id}/config` (GET, PUT). Legacy `/api/keys` and `/api/config` unchanged in path and behaviour, now also role-checked.

- [ ] **Step 1: Write the failing tests**

Append to `ingestion/test_routes_keys.py`:

```python
@requires_pg
def test_scoped_and_legacy_key_listing_agree(client: TestClient) -> None:
    """The two paths differ only in how the knowledge base is chosen."""
    from db import get_postgres_session
    from models import KnowledgeBaseUser, User

    email = _unique_email()
    try:
        _register_and_verify(client, email)
        client.post("/api/keys", json={"name": "k1"}, headers=LOCALHOST_ORIGIN)
        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            kb_id = str(
                s.query(KnowledgeBaseUser.knowledge_base_id)
                .filter(KnowledgeBaseUser.user_id == user.id)
                .one()[0]
            )
        legacy = client.get("/api/keys").json()
        scoped = client.get(f"/api/knowledge-bases/{kb_id}/keys").json()
        assert legacy == scoped
        assert len(scoped) == 1
    finally:
        _purge_user(email)


@requires_pg
def test_scoped_keys_for_a_foreign_knowledge_base_are_404(client: TestClient) -> None:
    import uuid as _uuid

    email = _unique_email()
    try:
        _register_and_verify(client, email)
        resp = client.get(f"/api/knowledge-bases/{_uuid.uuid4()}/keys")
        assert resp.status_code == 404
    finally:
        _purge_user(email)


@requires_pg
def test_editor_may_not_manage_keys(client: TestClient) -> None:
    """API keys need admin. This is the boundary pair's refused side."""
    from db import get_postgres_session
    from models import KnowledgeBaseUser, User

    email = _unique_email()
    try:
        _register_and_verify(client, email)
        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            membership = s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).one()
            kb_id = str(membership.knowledge_base_id)
            membership.role = "editor"
            s.commit()
        assert client.get(f"/api/knowledge-bases/{kb_id}/keys").status_code == 404
        assert client.get("/api/keys").status_code == 404, "legacy must enforce the same floor"
    finally:
        _purge_user(email)
```

Append to `ingestion/test_routes_settings.py` (match that file's existing fixture and helper names — read it first; it registers users the same way):

```python
@requires_pg
def test_editor_may_read_config_but_not_write_it(client: TestClient) -> None:
    """Config reads need reader, writes need admin."""
    from db import get_postgres_session
    from models import KnowledgeBaseUser, User

    email = _unique_email()
    try:
        _register_and_verify(client, email)
        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            membership = s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).one()
            kb_id = str(membership.knowledge_base_id)
            membership.role = "editor"
            s.commit()
        assert client.get(f"/api/knowledge-bases/{kb_id}/config").status_code == 200
        resp = client.put(
            f"/api/knowledge-bases/{kb_id}/config",
            json={"interests": "x", "discover_types": True, "entity_types": [], "relationship_types": []},
            headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 404
    finally:
        _purge_user(email)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest test_routes_keys.py test_routes_settings.py -v
```

Expected: FAIL — 404 on the scoped paths (not routed yet); the editor tests fail because no role check exists.

- [ ] **Step 3: Restructure `routes_keys.py`**

Extract each handler body into a module-level function taking `session` and `kb_id`, then declare both wrappers. The shape, using the listing endpoint as the worked example — apply the identical pattern to `create_key` (floor `admin`) and `revoke_key` (floor `admin`):

```python
def _list_keys(session: object, kb_id: str) -> list[ApiKeyOut]:
    rows = (
        session.query(ApiKey)  # type: ignore[attr-defined]
        .filter(ApiKey.knowledge_base_id == kb_id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    return [
        ApiKeyOut(
            id=str(k.id), name=k.name, prefix=k.prefix, created_at=k.created_at,
            last_used_at=k.last_used_at, revoked_at=k.revoked_at,
        )
        for k in rows
    ]


@router.get("", response_model=list[ApiKeyOut])
def list_keys(user: User = Depends(current_user)) -> list[ApiKeyOut]:  # noqa: B008
    """Legacy: the knowledge base is implied. Sub-project B removes this."""
    with get_postgres_session() as session:
        kb_id = home_knowledge_base_id(session, user.id)
        require_membership(session, user.id, kb_id, "admin")
        return _list_keys(session, kb_id)


@scoped_router.get("/{kb_id}/keys", response_model=list[ApiKeyOut])
def list_keys_scoped(kb_id: str, user: User = Depends(current_user)) -> list[ApiKeyOut]:  # noqa: B008
    with get_postgres_session() as session:
        require_membership(session, user.id, kb_id, "admin")
        return _list_keys(session, kb_id)
```

Declare the second router at the top of the file, beside the existing one:

```python
scoped_router = APIRouter(
    prefix="/api/knowledge-bases", tags=["API keys"], dependencies=[Depends(require_csrf)]
)
```

Add `from memberships import require_membership` to the imports. Register `scoped_router` in `ingestion/main.py` alongside `keys_router`.

Note that `require_membership` raises 404 when `kb_id` is `None`, which is what the legacy wrapper needs when a user has no knowledge base — so the old explicit `if knowledge_base_id is None` check disappears from each handler. The one exception is `list_keys`, which previously returned `[]` in that case; it now 404s. That is a deliberate behaviour change and no current UI path can reach it, since every user has at least one knowledge base.

Before implementing, check whether an existing test asserts that empty-list behaviour:

```bash
grep -n "no.*knowledge_base\|== \[\]" ingestion/test_routes_keys.py
```

If one exists, update it to expect 404 and say so in your report — do not silently delete it. If none exists, note that too.

- [ ] **Step 4: Restructure `routes_settings.py` the same way**

`_get_config(session, kb_id) -> ConfigResponse` and `_put_config(session, kb_id, body) -> ConfigResponse`, with legacy and scoped wrappers over each. Floors: **`reader` for GET, `admin` for PUT**. Keep the existing `email_verified` check on PUT, before the membership check. Declare a `scoped_router` with prefix `/api/knowledge-bases` and paths `/{kb_id}/config`. Register it in `main.py`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest test_routes_keys.py test_routes_settings.py -v
```

Expected: PASS, including every pre-existing test in both files unchanged.

- [ ] **Step 6: Run lint, types, and the full suite**

```bash
uv run pytest && uv run ruff check . && uv run mypy .
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
git add ingestion/routes_keys.py ingestion/routes_settings.py ingestion/main.py \
        ingestion/test_routes_keys.py ingestion/test_routes_settings.py
git commit -m "feat(api): knowledge-base-scoped keys and config

Legacy paths keep working and now carry the same role floors — differing
only in how the knowledge base is chosen, never in what is permitted."
```

---

### Task 7: Scope content ingestion and the GraphQL explorer

The second half. `routes_ingest.py` mounts at **`/api/content`**, not `/api/ingest`.

**Files:**
- Modify: `ingestion/routes_ingest.py`
- Modify: `ingestion/graph_api.py:138-149`
- Modify: `ingestion/main.py`
- Test: `ingestion/test_routes_ingest.py`, `ingestion/test_routes_graphql.py` (extend)

**Interfaces:**
- Consumes: `require_membership` (Task 1).
- Produces: `/api/knowledge-bases/{kb_id}/content` (POST, and GET `/{job_id}`), `/api/knowledge-bases/{kb_id}/graphql`. Legacy `/api/content` and `/api/graphql` unchanged in path.

- [ ] **Step 1: Write the failing tests**

Append to `ingestion/test_routes_ingest.py` (read the file first and match its existing fixture and helper names):

```python
@requires_pg
def test_reader_may_not_ingest_but_editor_may(client: TestClient) -> None:
    """The ingest boundary pair: editor is the floor, reader is refused."""
    from db import get_postgres_session
    from models import KnowledgeBaseUser, User

    email = _unique_email()
    try:
        _register_and_verify(client, email)
        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            membership = s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).one()
            kb_id = str(membership.knowledge_base_id)
            membership.role = "reader"
            s.commit()
        refused = client.post(
            f"/api/knowledge-bases/{kb_id}/content", json={"text": "hi"}, headers=LOCALHOST_ORIGIN
        )
        assert refused.status_code == 404

        with get_postgres_session() as s:
            user = s.query(User).filter(User.email == email).one()
            s.query(KnowledgeBaseUser).filter(KnowledgeBaseUser.user_id == user.id).one().role = "editor"
            s.commit()
        allowed = client.post(
            f"/api/knowledge-bases/{kb_id}/content", json={"text": "hi"}, headers=LOCALHOST_ORIGIN
        )
        assert allowed.status_code == 202
    finally:
        _purge_user(email)
```

Append to `ingestion/test_routes_graphql.py` (match its fixture and helper names):

```python
@requires_pg
def test_scoped_graphql_requires_membership(client: TestClient) -> None:
    import uuid as _uuid

    email = _unique_email()
    try:
        _register_and_verify(client, email)
        resp = client.post(
            f"/api/knowledge-bases/{_uuid.uuid4()}/graphql",
            json={"query": "{ nodes(limit: 1) { id } }"},
            headers=LOCALHOST_ORIGIN,
        )
        assert resp.status_code == 404
    finally:
        _purge_user(email)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest test_routes_ingest.py test_routes_graphql.py -v
```

Expected: FAIL — 404 on the scoped paths, since neither is routed yet.

- [ ] **Step 3: Restructure `routes_ingest.py`**

Extract `_ingest_content(session, kb_id, body, user) -> ContentAccepted` and `_job_status(session, kb_id, job_id) -> JobStatusResponse`. Legacy wrappers resolve via `home_knowledge_base_id`; scoped wrappers take `kb_id` from the path. Floors: **`editor` for POST, `reader` for GET**. Keep the existing `email_verified` check on POST, before the membership check. Declare a `scoped_router` with prefix `/api/knowledge-bases` and paths `/{kb_id}/content` and `/{kb_id}/content/{job_id}`. Register it in `main.py`.

- [ ] **Step 4: Add a scoped GraphQL router**

In `ingestion/graph_api.py`, leave `get_cookie_context` and `cookie_graphql_router` exactly as they are — that is the legacy path — and add beside them:

```python
async def get_scoped_cookie_context(
    kb_id: str,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> dict[str, Any]:
    """Resolve the knowledge base from the path, for the explorer once sub-project B
    passes it explicitly. Reading the graph needs `reader`."""
    with get_postgres_session() as session:
        require_membership(session, user.id, kb_id, "reader")
    return {"knowledge_base_id": kb_id}


# Mounted at /api/knowledge-bases/{kb_id}/graphql.
scoped_cookie_graphql_router: GraphQLRouter[dict[str, Any], None] = GraphQLRouter(
    schema, context_getter=get_scoped_cookie_context
)
```

Add `from memberships import require_membership` to the imports. In `main.py`, mount it:

```python
app.include_router(scoped_cookie_graphql_router, prefix="/api/knowledge-bases/{kb_id}/graphql")
```

Also add the `reader` floor to the legacy `get_cookie_context`, so both paths enforce the same thing: after resolving `knowledge_base_id`, call `require_membership(session, user.id, knowledge_base_id, "reader")` inside the same session block.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest test_routes_ingest.py test_routes_graphql.py -v
```

Expected: PASS, with every pre-existing test in both files still passing.

- [ ] **Step 6: Run lint, types, and the full suite**

```bash
uv run pytest && uv run ruff check . && uv run mypy .
```

Expected: all green — the full suite is now 151 original plus roughly 25 new tests.

- [ ] **Step 7: Verify the desk UI is genuinely untouched**

The whole point of A is that it changes nothing a user can see. Against the running stack:

```bash
cd /home/steve/Source/sinpi/anything_handwritten
docker compose up -d --build ingestion-api
sleep 15
C=$(curl -s -i -X POST http://localhost:5173/api/auth/login \
  -H 'Content-Type: application/json' -H 'Origin: http://localhost:5173' \
  -d "{\"email\":\"$(grep INGESTION_ADMIN_EMAIL .env | cut -d'"' -f2)\",\"password\":\"$(grep INGESTION_ADMIN_PASSWORD .env | cut -d'"' -f2)\"}" \
  | grep -i '^set-cookie' | sed 's/.*session=\([^;]*\).*/\1/')
for p in /api/auth/me /api/keys /api/config /api/knowledge-bases; do
  printf '%-26s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:5173$p" -H "Cookie: session=$C")"
done
```

Expected: all four return `200`, including the new `/api/knowledge-bases`. Then load `http://localhost:5173/app` in a browser, confirm the dashboard still renders, and confirm the config and explore pages still work. Any change a user can see here is a bug in A.

- [ ] **Step 8: Commit**

```bash
git add ingestion/routes_ingest.py ingestion/graph_api.py ingestion/main.py \
        ingestion/test_routes_ingest.py ingestion/test_routes_graphql.py
git commit -m "feat(api): knowledge-base-scoped content and GraphQL

Completes the scoped surface. Legacy paths keep their behaviour and gain
the same role floors, so the unscoped route is not an authorization bypass
for as long as sub-project B takes to retire it."
```

---

## Notes for the implementer

- **Do not touch `app/`.** Sub-project B owns the UI. If a change here seems to require a UI change, A has broken its own contract — stop and say so.
- **Every refusal is a 404.** If you find yourself writing `403` for a membership or role failure, re-read the spec. The only 403s in A are the pre-existing `email_verified` guards, which are about the account rather than the knowledge base.
- **`home_knowledge_base_id` survives A.** Deleting it is B's job.
- The `_delete_postgres_rows` seam exists so a test can simulate a half-failed delete. Do not inline it.
- Tests here run against a real Postgres and a real Neo4j. They create and purge their own users and knowledge bases — never assert against, or delete, data you did not create. `Default Knowledge Base` is the operator's live data.
