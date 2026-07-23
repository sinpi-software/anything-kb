# Knowledge Transform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `KNOWLEDGE` transform type that builds and incrementally maintains a per-org knowledge graph in Neo4j from markdown artifacts — LLM entity+relationship extraction, LLM-assisted entity resolution, compounding summary merges, and open/LLM-inferred edges.

**Architecture:** A new Neo4j client (`neo4j_client.py`) alongside the Postgres one, and a focused `knowledge.py` module holding the pydantic extraction schema, resolution, compound-merge, and Cypher builders. `transformations.py` gains a thin `@task llm_knowledge_transform` wrapper registered in `DISPATCH`; the pipeline fold is unchanged. Everything is scoped by `org_id` in one shared Neo4j (Community edition).

**Tech Stack:** Python 3, Prefect 3, pydantic v2, OpenRouter SDK, neo4j Python driver, Neo4j 5 (Community), pytest, ruff, mypy (strict).

## Global Constraints

- Design doc: `docs/superpowers/specs/2026-07-23-knowledge-transform-design.md` — read it; it is the source of truth.
- **Per-org isolation is property-scoped:** every Neo4j node and relationship carries `org_id`, and EVERY read/write query filters by `org_id`. A query without an `org_id` predicate is a bug.
- Entity node label is `:Entity` with a `type` property (no dynamic per-type labels). Relationships are generic `:RELATED { type, org_id, source_artifact_id }` (inferred label in `type`). No APOC.
- Entity identity: a globally-unique UUID `id`; resolution decides existing-vs-new. Uniqueness constraint on `e.id` only (single-property; Community-safe). `org_id` is a filter property.
- ruff clean, mypy strict clean, pytest green. Match the existing code's minimal, self-documenting style (small functions; docstrings only when load-bearing).
- LLM calls go through the existing OpenRouter client under the `llm` global concurrency limit (`config.LLM_CONCURRENCY_NAME`). The API key env is `config.OPENROUTER_API_KEY_ENV`.
- Neo4j is already in `docker-compose.yml` (`neo4j:5`, `ingestion-neo4j`, ports 7474/7687, auth `neo4j/ingestion`, volume `ingestion-neo4jdata`) — currently an UNCOMMITTED working-tree change on the branch. `.env.sample` already has `INGESTION_NEO4J_URI/USER/PASSWORD`. The container is running and healthy.
- Do NOT commit `ingestion/transformations.py`'s unrelated staged `to_artifact`→`to_model` rename; only stage the files each task names.
- Integration tests run against the live docker Neo4j (`bolt://localhost:7687`, `neo4j/ingestion`) and must clean up their own data, scoped to throwaway `org_id`s. LLM calls in tests are monkeypatched (no real OpenRouter calls).

## File Structure

- `ingestion/neo4j_client.py` — cached `Driver`, `get_neo4j_session()`, `bootstrap_schema()`
- `ingestion/knowledge.py` — pydantic extraction models; `extract_knowledge`, `resolve_entity`, `merge_summary`, Cypher builders, `write_graph`, orchestrating `run_knowledge_transform`
- `ingestion/config.py` — Neo4j env-var names + `KNOWLEDGE_RESOLUTION_CANDIDATES` (modify)
- `ingestion/models.py` — `TransformationType.KNOWLEDGE` (modify)
- `ingestion/transformations.py` — `llm_knowledge_transform` task + `DISPATCH` entry + `validate_transform_config` extension (modify)
- `ingestion/main.py` — call `bootstrap_schema()` at startup (modify)
- `ingestion/pyproject.toml` — add `neo4j` dep + mypy override for neo4j (modify)
- `ingestion/docker-compose.yml` → repo-root `docker-compose.yml` (already changed; commit in Task 1)
- `ingestion/test_knowledge.py` — unit tests (pydantic parse, Cypher builders, validate) + Neo4j integration tests
- `ingestion/seed.py` — optional sample knowledge transform (modify, Task 6)

---

## Task 1: Neo4j client, schema bootstrap, deps

**Files:**
- Create: `ingestion/neo4j_client.py`
- Modify: `ingestion/config.py`, `ingestion/pyproject.toml`, `ingestion/main.py`
- Commit: repo-root `docker-compose.yml` + `.env.sample` (already changed)
- Test: `ingestion/test_knowledge.py` (a bootstrap/connection integration test)

**Interfaces:**
- Produces: `get_driver() -> neo4j.Driver` (lru_cached), `get_neo4j_session() -> neo4j.Session`, `bootstrap_schema() -> None` from `neo4j_client`. Config: `NEO4J_URI_ENV`, `NEO4J_USER_ENV`, `NEO4J_PASSWORD_ENV`, `KNOWLEDGE_RESOLUTION_CANDIDATES`.

- [ ] **Step 1: Add the neo4j dependency**

```bash
cd ingestion && uv add neo4j
```
Then add a mypy override in `pyproject.toml` next to the other `[[tool.mypy.overrides]]` blocks:
```toml
[[tool.mypy.overrides]]
module = ["neo4j.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Add config knobs**

Append to `ingestion/config.py`:
```python
# --- Neo4j ---
NEO4J_URI_ENV = "INGESTION_NEO4J_URI"
NEO4J_USER_ENV = "INGESTION_NEO4J_USER"
NEO4J_PASSWORD_ENV = "INGESTION_NEO4J_PASSWORD"

# How many existing entities to offer the LLM as resolution candidates.
KNOWLEDGE_RESOLUTION_CANDIDATES = 5
```

- [ ] **Step 3: Write `neo4j_client.py`**

```python
from functools import lru_cache
from os import getenv

from neo4j import Driver, GraphDatabase, Session

import config


@lru_cache(maxsize=1)
def get_driver() -> Driver:
    uri = getenv(config.NEO4J_URI_ENV, "bolt://localhost:7687")
    user = getenv(config.NEO4J_USER_ENV, "neo4j")
    password = getenv(config.NEO4J_PASSWORD_ENV, "neo4j")
    return GraphDatabase.driver(uri, auth=(user, password))


def get_neo4j_session() -> Session:
    return get_driver().session()


def bootstrap_schema() -> None:
    # Idempotent: a unique id per entity, and a full-text index over name + aliases
    # used for resolution candidate lookup. org_id is a filter property on every query.
    with get_neo4j_session() as session:
        session.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")
        session.run("CREATE FULLTEXT INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON EACH [e.name, e.aliases]")
```

- [ ] **Step 4: Bootstrap at startup**

In `ingestion/main.py`, inside `main()`, after `ensure_concurrency_limits()`, add:
```python
    from neo4j_client import bootstrap_schema

    bootstrap_schema()
```

- [ ] **Step 5: Write the connection/bootstrap integration test**

`ingestion/test_knowledge.py`:
```python
import os

import pytest

# These tests need the docker Neo4j. Skip if the driver can't connect.
neo4j = pytest.importorskip("neo4j")

os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")

from neo4j_client import bootstrap_schema, get_neo4j_session  # noqa: E402


def _neo4j_available() -> bool:
    try:
        with get_neo4j_session() as session:
            session.run("RETURN 1").consume()
        return True
    except Exception:
        return False


requires_neo4j = pytest.mark.skipif(not _neo4j_available(), reason="Neo4j not reachable")


@requires_neo4j
def test_bootstrap_is_idempotent() -> None:
    bootstrap_schema()
    bootstrap_schema()  # second call must not raise
    with get_neo4j_session() as session:
        names = {r["name"] for r in session.run("SHOW CONSTRAINTS YIELD name RETURN name")}
        assert "entity_id" in names
```

- [ ] **Step 6: Run the test**

Run: `cd ingestion && uv run pytest test_knowledge.py -v`
Expected: PASS (or SKIP if the container is down — start it with `docker compose up -d neo4j`).

- [ ] **Step 7: Lint, type-check, commit**

```bash
cd ingestion && uv run ruff check . && uv run ruff format --check . && uv run mypy .
cd /home/steve/Source/sinpi/anything_handwritten
git add docker-compose.yml .env.sample ingestion/neo4j_client.py ingestion/config.py ingestion/main.py ingestion/pyproject.toml ingestion/uv.lock ingestion/test_knowledge.py
git commit -m "feat(ingestion): neo4j client + schema bootstrap + compose service"
```
(Note: `git add` explicit paths only — do NOT include `transformations.py`.)

---

## Task 2: Knowledge extraction schema + LLM call

**Files:**
- Create: `ingestion/knowledge.py`
- Test: `ingestion/test_knowledge.py` (add extraction tests)

**Interfaces:**
- Consumes: OpenRouter client, `config`.
- Produces:
  - pydantic `ExtractedEntity { name: str, type: str, description: str, aliases: list[str] = [] }`
  - pydantic `ExtractedRelationship { source_name: str, target_name: str, type: str }`
  - pydantic `KnowledgeExtraction { entities: list[ExtractedEntity], relationships: list[ExtractedRelationship] }`
  - `build_extraction_messages(prompt: str, entity_types: list[str], text: str) -> list[dict[str, str]]`
  - `extract_knowledge(client, model, prompt, entity_types, text, llm_params) -> KnowledgeExtraction`

- [ ] **Step 1: Write the failing test for the schema + message builder**

Add to `ingestion/test_knowledge.py`:
```python
from knowledge import (  # noqa: E402
    ExtractedEntity,
    KnowledgeExtraction,
    build_extraction_messages,
)


def test_extraction_parses_from_json() -> None:
    payload = (
        '{"entities": [{"name": "Ada Lovelace", "type": "Person", "description": "Mathematician", '
        '"aliases": ["Ada"]}], "relationships": [{"source_name": "Ada Lovelace", '
        '"target_name": "Analytical Engine", "type": "WORKED_ON"}]}'
    )
    result = KnowledgeExtraction.model_validate_json(payload)
    assert result.entities[0] == ExtractedEntity(
        name="Ada Lovelace", type="Person", description="Mathematician", aliases=["Ada"]
    )
    assert result.relationships[0].type == "WORKED_ON"


def test_extraction_defaults_aliases_empty() -> None:
    e = ExtractedEntity(name="X", type="Thing", description="d")
    assert e.aliases == []


def test_build_extraction_messages_includes_entity_types_and_text() -> None:
    msgs = build_extraction_messages("Find entities.", ["Person", "Place"], "Some article")
    joined = " ".join(m["content"] for m in msgs)
    assert "Person" in joined and "Place" in joined
    assert "Some article" in joined
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
```

- [ ] **Step 2: Run it (RED)**

Run: `cd ingestion && uv run pytest test_knowledge.py -k extraction -v`
Expected: FAIL — `knowledge` module / symbols not defined.

- [ ] **Step 3: Implement the schema + extraction**

Create `ingestion/knowledge.py`:
```python
from typing import Any

from openrouter import OpenRouter
from pydantic import BaseModel


class ExtractedEntity(BaseModel):
    name: str
    type: str
    description: str
    aliases: list[str] = []


class ExtractedRelationship(BaseModel):
    source_name: str
    target_name: str
    type: str


class KnowledgeExtraction(BaseModel):
    entities: list[ExtractedEntity] = []
    relationships: list[ExtractedRelationship] = []


def build_extraction_messages(prompt: str, entity_types: list[str], text: str) -> list[dict[str, str]]:
    system = (
        f"{prompt}\n\n"
        f"Extract only entities of these types: {', '.join(entity_types)}. "
        "Also extract relationships between the extracted entities; choose a concise "
        "UPPER_SNAKE_CASE relationship type that best fits the context."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]


def extract_knowledge(
    client: OpenRouter,
    model: str,
    prompt: str,
    entity_types: list[str],
    text: str,
    llm_params: dict[str, Any],
) -> KnowledgeExtraction:
    result = client.chat.send(
        model=model,
        messages=build_extraction_messages(prompt, entity_types, text),
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "knowledge_extraction", "schema": KnowledgeExtraction.model_json_schema()},
        },
        **llm_params,
    )
    content = result.choices[0].message.content
    if not isinstance(content, str):
        raise ValueError("LLM returned no text content")
    return KnowledgeExtraction.model_validate_json(content)
```

- [ ] **Step 4: Run it (GREEN)**

Run: `cd ingestion && uv run pytest test_knowledge.py -k extraction -v`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd ingestion && uv run ruff check . && uv run mypy .
git add ingestion/knowledge.py ingestion/test_knowledge.py
git commit -m "feat(ingestion): knowledge extraction schema + LLM call"
```

---

## Task 3: Entity resolution (candidate lookup + LLM judgment)

**Files:**
- Modify: `ingestion/knowledge.py`
- Test: `ingestion/test_knowledge.py`

**Interfaces:**
- Consumes: `neo4j_client.get_neo4j_session`, OpenRouter client, `config.KNOWLEDGE_RESOLUTION_CANDIDATES`, `ExtractedEntity`.
- Produces:
  - `normalize_name(name: str) -> str` (lowercase, collapse whitespace)
  - `candidate_query(org_id: str, entity_type: str, name_normalized: str, limit: int) -> tuple[str, dict[str, Any]]` (Cypher + params)
  - `build_resolution_messages(entity: ExtractedEntity, candidates: list[dict[str, Any]]) -> list[dict[str, str]]`
  - `resolve_entity(session, client, model, org_id, entity, llm_params) -> str | None` — returns an existing entity `id` or `None` (meaning "new")

- [ ] **Step 1: Write failing tests for the pure helpers**

Add to `ingestion/test_knowledge.py`:
```python
from knowledge import candidate_query, normalize_name  # noqa: E402


def test_normalize_name() -> None:
    assert normalize_name("  Barack   Obama ") == "barack obama"


def test_candidate_query_is_org_and_type_scoped() -> None:
    query, params = candidate_query("org-1", "Person", "ada lovelace", 5)
    assert "org_id" in query
    assert params["org_id"] == "org-1"
    assert params["type"] == "Person"
    assert params["name_normalized"] == "ada lovelace"
    assert params["limit"] == 5
```

- [ ] **Step 2: Run (RED)**

Run: `cd ingestion && uv run pytest test_knowledge.py -k "normalize or candidate" -v`
Expected: FAIL — symbols not defined.

- [ ] **Step 3: Implement resolution**

Append to `ingestion/knowledge.py` (add imports at top: `import re`, `from neo4j import Session`, `from neo4j_client import get_neo4j_session` is NOT needed here — the session is passed in; add `import config`):
```python
import re

import config


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def candidate_query(org_id: str, entity_type: str, name_normalized: str, limit: int) -> tuple[str, dict[str, Any]]:
    # Exact/alias match within the org + type; scoped by org_id.
    query = (
        "MATCH (e:Entity {org_id: $org_id, type: $type}) "
        "WHERE e.name_normalized = $name_normalized OR $name_normalized IN e.aliases "
        "RETURN e.id AS id, e.name AS name, e.summary AS summary "
        "LIMIT $limit"
    )
    params: dict[str, Any] = {
        "org_id": org_id,
        "type": entity_type,
        "name_normalized": name_normalized,
        "limit": limit,
    }
    return query, params


def build_resolution_messages(
    entity: "ExtractedEntity", candidates: list[dict[str, Any]]
) -> list[dict[str, str]]:
    listed = "\n".join(f'- id={c["id"]}: {c["name"]} — {c["summary"]}' for c in candidates)
    system = (
        "You resolve whether a newly mentioned entity is the SAME as one of the existing "
        "entities. Reply with ONLY the matching id, or the word NEW if none match."
    )
    user = (
        f"New entity: {entity.name} ({entity.type}) — {entity.description}\n\n"
        f"Existing candidates:\n{listed}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def resolve_entity(
    session: Session,
    client: OpenRouter,
    model: str,
    org_id: str,
    entity: ExtractedEntity,
    llm_params: dict[str, Any],
) -> str | None:
    query, params = candidate_query(org_id, entity.type, normalize_name(entity.name), config.KNOWLEDGE_RESOLUTION_CANDIDATES)
    candidates = [dict(record) for record in session.run(query, params)]
    if not candidates:
        return None
    if len(candidates) == 1:
        return str(candidates[0]["id"])
    result = client.chat.send(
        model=model, messages=build_resolution_messages(entity, candidates), **llm_params
    )
    content = result.choices[0].message.content
    answer = content.strip() if isinstance(content, str) else "NEW"
    valid_ids = {str(c["id"]) for c in candidates}
    return answer if answer in valid_ids else None
```

- [ ] **Step 4: Run (GREEN)**

Run: `cd ingestion && uv run pytest test_knowledge.py -k "normalize or candidate" -v`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd ingestion && uv run ruff check . && uv run mypy .
git add ingestion/knowledge.py ingestion/test_knowledge.py
git commit -m "feat(ingestion): LLM-assisted entity resolution"
```

---

## Task 4: Graph write — upsert nodes, compound-merge, edges, provenance

**Files:**
- Modify: `ingestion/knowledge.py`
- Test: `ingestion/test_knowledge.py` (Neo4j integration)

**Interfaces:**
- Consumes: OpenRouter client, `neo4j.Session`, `ExtractedEntity`, `ExtractedRelationship`, `normalize_name`.
- Produces:
  - `merge_summary(client, model, existing: str, new: str, llm_params) -> str`
  - `upsert_entity(session, org_id, entity_id, entity, summary) -> None` (create or update by id, scoped by org_id)
  - `write_relationship(session, org_id, source_id, target_id, rel_type, artifact_id) -> None`
  - `write_provenance(session, org_id, entity_id, artifact_id) -> None`

- [ ] **Step 1: Implement the write helpers**

Append to `ingestion/knowledge.py`:
```python
def merge_summary(client: OpenRouter, model: str, existing: str, new: str, llm_params: dict[str, Any]) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "You maintain an encyclopedia entry. Merge the new information into the existing "
                "summary, keeping it accurate and concise. Return only the revised summary."
            ),
        },
        {"role": "user", "content": f"Existing summary:\n{existing}\n\nNew information:\n{new}"},
    ]
    result = client.chat.send(model=model, messages=messages, **llm_params)
    content = result.choices[0].message.content
    return content.strip() if isinstance(content, str) else existing


def upsert_entity(session: Session, org_id: str, entity_id: str, entity: ExtractedEntity, summary: str) -> None:
    session.run(
        "MERGE (e:Entity {id: $id}) "
        "ON CREATE SET e.org_id = $org_id, e.type = $type, e.created_at = datetime() "
        "SET e.name = $name, e.name_normalized = $nn, e.summary = $summary, "
        "e.aliases = $aliases, e.updated_at = datetime()",
        {
            "id": entity_id,
            "org_id": org_id,
            "type": entity.type,
            "name": entity.name,
            "nn": normalize_name(entity.name),
            "summary": summary,
            "aliases": [normalize_name(a) for a in entity.aliases],
        },
    )


def write_relationship(
    session: Session, org_id: str, source_id: str, target_id: str, rel_type: str, artifact_id: str
) -> None:
    session.run(
        "MATCH (a:Entity {id: $source_id, org_id: $org_id}), (b:Entity {id: $target_id, org_id: $org_id}) "
        "MERGE (a)-[r:RELATED {type: $rel_type, org_id: $org_id}]->(b) "
        "ON CREATE SET r.source_artifact_id = $artifact_id, r.created_at = datetime()",
        {
            "source_id": source_id,
            "target_id": target_id,
            "org_id": org_id,
            "rel_type": rel_type,
            "artifact_id": artifact_id,
        },
    )


def write_provenance(session: Session, org_id: str, entity_id: str, artifact_id: str) -> None:
    session.run(
        "MERGE (s:Source {org_id: $org_id, artifact_id: $artifact_id}) "
        "WITH s MATCH (e:Entity {id: $entity_id, org_id: $org_id}) "
        "MERGE (e)-[:MENTIONED_IN]->(s)",
        {"org_id": org_id, "artifact_id": artifact_id, "entity_id": entity_id},
    )
```

- [ ] **Step 2: Write the integration test (upsert + re-upsert + relationship)**

Add to `ingestion/test_knowledge.py`:
```python
import uuid  # noqa: E402

from knowledge import (  # noqa: E402
    ExtractedEntity,
    upsert_entity,
    write_provenance,
    write_relationship,
)


def _cleanup(org_id: str) -> None:
    with get_neo4j_session() as session:
        session.run("MATCH (n) WHERE n.org_id = $org_id DETACH DELETE n", {"org_id": org_id})


@requires_neo4j
def test_upsert_and_relationship_roundtrip() -> None:
    bootstrap_schema()
    org = f"test-{uuid.uuid4()}"
    a_id, b_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        with get_neo4j_session() as session:
            upsert_entity(session, org, a_id, ExtractedEntity(name="Ada", type="Person", description="d"), "sum A")
            upsert_entity(session, org, b_id, ExtractedEntity(name="Engine", type="Thing", description="d"), "sum B")
            # Re-upsert A with a new summary — must update, not duplicate.
            upsert_entity(session, org, a_id, ExtractedEntity(name="Ada", type="Person", description="d"), "sum A v2")
            write_relationship(session, org, a_id, b_id, "WORKED_ON", "art-1")
            write_provenance(session, org, a_id, "art-1")

            count = session.run("MATCH (e:Entity {org_id: $o}) RETURN count(e) AS c", {"o": org}).single()["c"]
            assert count == 2  # no duplicate
            summ = session.run("MATCH (e:Entity {id: $id}) RETURN e.summary AS s", {"id": a_id}).single()["s"]
            assert summ == "sum A v2"
            rels = session.run(
                "MATCH (:Entity {org_id: $o})-[r:RELATED]->() RETURN r.type AS t", {"o": org}
            ).single()["t"]
            assert rels == "WORKED_ON"
    finally:
        _cleanup(org)


@requires_neo4j
def test_org_isolation() -> None:
    bootstrap_schema()
    org_a, org_b = f"a-{uuid.uuid4()}", f"b-{uuid.uuid4()}"
    try:
        with get_neo4j_session() as session:
            upsert_entity(session, org_a, str(uuid.uuid4()), ExtractedEntity(name="Ada", type="Person", description="d"), "A")
            upsert_entity(session, org_b, str(uuid.uuid4()), ExtractedEntity(name="Ada", type="Person", description="d"), "B")
            a_count = session.run("MATCH (e:Entity {org_id: $o}) RETURN count(e) AS c", {"o": org_a}).single()["c"]
            assert a_count == 1  # org_b's identically-named entity is invisible to org_a
    finally:
        _cleanup(org_a)
        _cleanup(org_b)
```

- [ ] **Step 3: Run (RED then GREEN)**

Run: `cd ingestion && uv run pytest test_knowledge.py -k "roundtrip or isolation" -v`
Expected: PASS (requires docker Neo4j). The re-upsert asserts no duplicate + updated summary; the isolation test proves two orgs with an identically-named entity stay separate.

- [ ] **Step 4: Lint, type-check, commit**

```bash
cd ingestion && uv run ruff check . && uv run mypy .
git add ingestion/knowledge.py ingestion/test_knowledge.py
git commit -m "feat(ingestion): neo4j graph writes with compound-merge and org scoping"
```

---

## Task 5: Orchestration, task registration, validation, KNOWLEDGE type

**Files:**
- Modify: `ingestion/knowledge.py` (add `run_knowledge_transform`), `ingestion/models.py`, `ingestion/transformations.py`
- Test: `ingestion/test_knowledge.py` (end-to-end with monkeypatched LLM)

**Interfaces:**
- Consumes: everything from Tasks 2–4; `models.Artifact`, `Transformation`; `db.get_postgres_session`.
- Produces:
  - `run_knowledge_transform(artifact_id: str, transformation_id: str) -> str` — orchestrates extract → resolve → upsert/merge → relationships → provenance → emit a JSON summary Artifact; returns the new artifact id.
  - `TransformationType.KNOWLEDGE = "knowledge"`.
  - `@task llm_knowledge_transform(artifact_id, transformation_id) -> str` in `transformations.py`, in `DISPATCH`.
  - `validate_transform_config` accepts `knowledge` and requires a non-empty `params["entity_types"]`.

- [ ] **Step 1: Add the enum value**

In `ingestion/models.py`, `TransformationType`:
```python
class TransformationType(Enum):
    SCORE = "score"
    SUMMARIZE = "summarize"
    CLASSIFY = "classify"
    KNOWLEDGE = "knowledge"
```

- [ ] **Step 2: Implement `run_knowledge_transform`**

Append to `ingestion/knowledge.py` (add imports: `import os`, `from prefect.concurrency.sync import concurrency`, `from db import get_postgres_session`, `from models import Artifact, Transformation`):
```python
import os

from prefect.concurrency.sync import concurrency

from db import get_postgres_session
from models import Artifact, Transformation


class KnowledgeTransformOutput(BaseModel):
    entities_created: int
    entities_merged: int
    relationships_created: int
    source_artifact_id: str

    def to_model(self) -> tuple[str, str]:
        return self.model_dump_json(), "application/json"


def run_knowledge_transform(artifact_id: str, transformation_id: str) -> str:
    with get_postgres_session() as session:
        transformation = session.get(Transformation, transformation_id)
        if transformation is None:
            raise ValueError(f"Transformation {transformation_id} not found")
        model = transformation.model
        prompt = transformation.prompt
        params = dict(transformation.params or {})
        entity_types = params.pop("entity_types", [])
        llm_params = params  # remaining keys are LLM knobs (temperature, etc.)

        artifact = session.get(Artifact, artifact_id)
        if artifact is None:
            raise ValueError(f"Artifact {artifact_id} not found")
        org_id = artifact.org_id
        if org_id is None:
            raise ValueError(f"Artifact {artifact_id} has no org")
        text = artifact.data

    created = merged = rels = 0
    with OpenRouter(api_key=os.environ[config.OPENROUTER_API_KEY_ENV]) as client:
        with concurrency(config.LLM_CONCURRENCY_NAME, occupy=1):
            extraction = extract_knowledge(client, model, prompt, entity_types, text, llm_params)

        name_to_id: dict[str, str] = {}
        with get_neo4j_session() as neo:
            for entity in extraction.entities:
                existing_id = resolve_entity(neo, client, model, org_id, entity, llm_params)
                if existing_id is None:
                    entity_id = str(uuid.uuid4())
                    summary = entity.description
                    created += 1
                else:
                    entity_id = existing_id
                    record = neo.run(
                        "MATCH (e:Entity {id: $id, org_id: $org_id}) RETURN e.summary AS s",
                        {"id": entity_id, "org_id": org_id},
                    ).single()
                    existing_summary = record["s"] if record else ""
                    with concurrency(config.LLM_CONCURRENCY_NAME, occupy=1):
                        summary = merge_summary(client, model, existing_summary, entity.description, llm_params)
                    merged += 1
                upsert_entity(neo, org_id, entity_id, entity, summary)
                write_provenance(neo, org_id, entity_id, artifact_id)
                name_to_id[normalize_name(entity.name)] = entity_id

            for rel in extraction.relationships:
                source_id = name_to_id.get(normalize_name(rel.source_name))
                target_id = name_to_id.get(normalize_name(rel.target_name))
                if source_id and target_id:
                    write_relationship(neo, org_id, source_id, target_id, rel.type, artifact_id)
                    rels += 1

    output = KnowledgeTransformOutput(
        entities_created=created, entities_merged=merged, relationships_created=rels, source_artifact_id=artifact_id
    )
    data, content_type = output.to_model()
    with get_postgres_session() as session:
        out = Artifact(
            org_id=org_id,
            ref_table_name=Artifact.__tablename__,
            ref_table_id=artifact_id,
            type=content_type,
            data=data,
        )
        session.add(out)
        session.flush()
        out_id = out.id
        session.commit()
    return out_id
```
Add `import uuid` and `from neo4j_client import get_neo4j_session` to the top imports of `knowledge.py`.

Note the resolution/merge LLM calls occupy the `llm` concurrency slot individually; the extraction call is wrapped once above. This keeps every OpenRouter call under the limit.

- [ ] **Step 3: Register the task + extend validation in `transformations.py`**

Add after the other task wrappers:
```python
@task
def llm_knowledge_transform(artifact_id: str, transformation_id: str) -> str:
    from knowledge import run_knowledge_transform

    return run_knowledge_transform(artifact_id, transformation_id)
```
Add to `DISPATCH`:
```python
    TransformationType.KNOWLEDGE.value: llm_knowledge_transform,
```
Extend `validate_transform_config` — before the `LLMParams.model_validate(...)` line, add:
```python
    if transform_type == TransformationType.KNOWLEDGE.value:
        entity_types = (params or {}).get("entity_types")
        if not isinstance(entity_types, list) or not entity_types:
            raise ValueError("knowledge transform requires a non-empty params['entity_types'] list")
```
Keep the existing `LLMParams.model_validate(params or {})` — `LLMParams` has `extra="allow"`, so `entity_types` passes.

- [ ] **Step 4: Write the end-to-end test with a monkeypatched LLM**

Add to `ingestion/test_knowledge.py`:
```python
import knowledge as knowledge_mod  # noqa: E402


class _FakeExtraction:
    def __init__(self, extraction: KnowledgeExtraction) -> None:
        self._e = extraction

    def __call__(self, *args: object, **kwargs: object) -> KnowledgeExtraction:
        return self._e


@requires_neo4j
def test_run_knowledge_writes_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap_schema()
    org = f"e2e-{uuid.uuid4()}"
    extraction = KnowledgeExtraction(
        entities=[
            ExtractedEntity(name="Ada", type="Person", description="Mathematician"),
            ExtractedEntity(name="Engine", type="Thing", description="A machine"),
        ],
        relationships=[knowledge_mod.ExtractedRelationship(source_name="Ada", target_name="Engine", type="WORKED_ON")],
    )
    # Stub every OpenRouter-backed step so no real LLM call happens.
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    monkeypatch.setattr(knowledge_mod, "resolve_entity", lambda *a, **k: None)
    monkeypatch.setattr(knowledge_mod, "merge_summary", lambda *a, **k: "merged")
    monkeypatch.setattr(knowledge_mod, "OpenRouter", lambda *a, **k: _NullClient())
    # Seed an org + artifact in Postgres via a helper (see below); or patch DB reads.
    # ... (the task implementer wires a throwaway org/artifact using get_postgres_session)
    # After run: assert 2 Entity nodes + 1 RELATED edge exist for `org`.
```
The implementer completes this test: create a throwaway `Org` + markdown `Artifact` (with `org_id=org`) in Postgres, call `run_knowledge_transform(artifact_id, transformation_id)` with a `Transformation` row (`type="knowledge"`, `params={"entity_types":["Person","Thing"]}`), then assert against Neo4j that 2 `:Entity` nodes and 1 `:RELATED` edge exist for `org`, and clean up both stores. Provide a minimal `_NullClient` whose `chat.send` is never reached (all LLM funcs are patched). If wiring a full Postgres fixture is heavy, instead unit-test `run_knowledge_transform`'s graph-writing portion by patching the Postgres reads to return a stub transformation/artifact — the implementer picks the lighter path and documents it.

- [ ] **Step 5: Run the suite**

Run: `cd ingestion && uv run pytest test_knowledge.py -v`
Expected: PASS (Neo4j integration tests included; LLM fully stubbed).

- [ ] **Step 6: Lint, type-check, commit**

```bash
cd ingestion && uv run ruff check . && uv run mypy .
git add ingestion/knowledge.py ingestion/models.py ingestion/transformations.py ingestion/test_knowledge.py
git commit -m "feat(ingestion): knowledge transform orchestration + registration"
```

---

## Task 6: Seed a sample knowledge transform

**Files:**
- Modify: `ingestion/seed.py`

**Interfaces:**
- Consumes: existing `seed.py` `get_or_create` + `validate_transform_config`; `TransformationType.KNOWLEDGE`.

- [ ] **Step 1: Add a knowledge step to the seeded chain**

In `ingestion/seed.py`, where the transform chain is seeded, add a knowledge transform for the org (append at the next `position`). Use a real prompt and entity types:
```python
(2, TransformationType.KNOWLEDGE.value,
 "Extract the notable entities and how they relate from this article.",
 {"entity_types": ["Person", "Place", "Organization", "Topic", "Story"]}),
```
Match `seed.py`'s existing tuple shape and `validate_transform_config` call (pass the `params` dict through; the other transforms pass `None`). Ensure the knowledge step's `params` carries `entity_types`.

- [ ] **Step 2: Run the seed against the dev DB**

Run: `cd ingestion && uv run python seed.py`
Expected: no error; the org now has a `knowledge` transform at the end of its chain. Verify:
```bash
docker exec -i $(docker compose -f ../docker-compose.yml ps -q postgres) psql -U ingestion -d ingestion -tAc "SELECT position, type FROM transformations ORDER BY position;"
```
Expected: the chain ends with a `knowledge` row.

- [ ] **Step 3: Commit**

```bash
git add ingestion/seed.py
git commit -m "chore(ingestion): seed a sample knowledge transform"
```

---

## Self-Review Notes

- **Spec coverage:** graph model entities+relationships (Tasks 2, 4); property-scoped org isolation (every Cypher has `org_id`; Task 4 has an explicit isolation test); compound/LLM-merge (Task 4 `merge_summary`, Task 5 orchestration); LLM-assisted resolution (Task 3); open/LLM-inferred edges (`:RELATED{type}`, Tasks 2/4); config (`entity_types` validated, Task 5); Neo4j client + bootstrap (Task 1); output artifact via `to_model` (Task 5); cost note (resolution/merge under the `llm` limit, Task 5). Testing: unit (parse, Cypher builders, validate) + Neo4j integration incl. org isolation.
- **Known executor watch-points:** Neo4j full-text index over a list property (`aliases`) — if the index creation errors on this Neo4j version, index `[e.name]` only and note it. The end-to-end test's Postgres fixture (Task 5 Step 4) is the heaviest bit — the plan explicitly allows the lighter patch-the-DB-reads path. `client.chat.send` signature must match the existing `transformations.py` usage (it does — same SDK). Confirm `neo4j` driver `session.run(...).single()` / iteration API against the installed driver version.
- **Consistency:** `to_model()` (not `to_artifact`) matches the current tree. `TransformationType.KNOWLEDGE.value == "knowledge"` used in both `DISPATCH` and `validate_transform_config`. `resolve_entity` returns `str | None` and is consumed that way in Task 5.
