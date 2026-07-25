# Self-Building Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the user-defined type schema with a self-building one: one prose `interests` field (gate + extraction lens), open vocabulary-hinted extraction, and an LLM **type-consolidation** step that folds novel types into a canonical, auto-growing vocabulary — with `discover_types` on/off spanning autopilot ↔ locked, and per-type `pinned`/`banned` flags.

**Architecture:** Type consolidation mirrors the existing entity resolution. Extraction emits free-form types; a post-extraction pass fast-path-matches them to the current vocabulary, and (when `discover_types` is on) resolves the remainder in one batched LLM call to either an existing canonical type, a new type (with an auto-written description), or a drop. New types are persisted back onto the knowledge base's config by the worker.

**Tech Stack:** FastAPI, Strawberry, SQLAlchemy/Alembic (Postgres), neo4j driver, OpenRouter, React Router 8 + Tailwind.

**Design doc:** `docs/superpowers/specs/2026-07-24-self-building-schema-design.md`.

## Global Constraints

- The stored type on every node/edge is always the **canonical configured name** (never the model's raw casing). Display consistency must not regress.
- `discover_types = false` ⇒ behavior identical to today (constrain to configured active types, drop novel).
- Consolidation-LLM failure ⇒ **fast-path fallback** (keep already-canonical types, defer novel candidates, mark job done) — never fail a document over consolidation.
- `banned` types are tombstones: excluded from the active vocabulary, never re-added, and future facts resolving to them are dropped. Existing nodes/edges are left untouched.
- Type identity is compared via `_norm_type` (already in `knowledge.py`): case/space/underscore-insensitive.
- Dev test env (all backend test commands assume these):
  `INGESTION_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/ingestion`,
  `INGESTION_NEO4J_URI=bolt://localhost:7687`, `INGESTION_NEO4J_USER=neo4j`, `INGESTION_NEO4J_PASSWORD=ingestion`, `INGESTION_OPENROUTER_API_KEY=test-key-not-used`.
- After each task: `uv run ruff check . && uv run ruff format --check . && uv run mypy .` must pass (run from `ingestion/`).

---

### Task 1: DB migration + model — `interests`, `discover_types`

**Files:**
- Modify: `ingestion/models.py` (KnowledgeBaseConfig, ~lines 99-110)
- Create: `ingestion/alembic/versions/<rev>_self_building_schema.py`
- Test: `ingestion/test_models.py`

**Interfaces — Produces:** `KnowledgeBaseConfig.interests: str`, `KnowledgeBaseConfig.discover_types: bool`. The JSONB `entity_types`/`relationship_types` gain optional `pinned`/`banned` keys per object (no column change).

- [ ] **Step 1: Model change.** In `models.py`, rename the column and add the flag:

```python
    interests: Mapped[str] = mapped_column(TEXT, nullable=False)
    discover_types: Mapped[bool] = mapped_column(BOOLEAN, nullable=False, server_default=true())
    # entity_types / relationship_types unchanged (JSONB list of {name, description, pinned?, banned?})
```
Add `true` to the existing `from sqlalchemy.sql.expression import false` import → `from sqlalchemy.sql.expression import false, true`. Rename the old `relevance_prompt` line to `interests`.

- [ ] **Step 2: Write the migration.** `uv run alembic revision -m "self building schema"`, then set the body:

```python
def upgrade() -> None:
    op.alter_column("knowledge_base_configs", "relevance_prompt", new_column_name="interests")
    op.add_column(
        "knowledge_base_configs",
        sa.Column("discover_types", sa.BOOLEAN(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("knowledge_base_configs", "discover_types")
    op.alter_column("knowledge_base_configs", "interests", new_column_name="relevance_prompt")
```
Use the file's existing import style (`from alembic import op`, `import sqlalchemy as sa`). Confirm `down_revision` is the current head (`uv run alembic heads`).

- [ ] **Step 3: Apply + verify.** Run `uv run alembic upgrade head`; then `docker exec ingestion-postgres psql -U ingestion -d ingestion -tAc "select column_name from information_schema.columns where table_name='knowledge_base_configs' and column_name in ('interests','discover_types','relevance_prompt')"`. Expected: `interests`, `discover_types` (no `relevance_prompt`).

- [ ] **Step 4: Test model attributes.** Add to `test_models.py` a test that constructs `KnowledgeBaseConfig(knowledge_base_id=..., interests="x", entity_types=[], relationship_types=[])` and asserts `discover_types` defaults truthy after a flush (server_default). Run: `uv run pytest test_models.py -q`. Expected: PASS.

- [ ] **Step 5: Commit** — `git add ingestion/models.py ingestion/alembic/versions/<rev>_*.py ingestion/test_models.py && git commit -m "feat(model): interests + discover_types on config"`.

---

### Task 2: Schemas — `TypeDef` flags, `interests`, `discover_types`

**Files:**
- Modify: `ingestion/schemas.py` (TypeDef, ConfigRequest, ConfigResponse)
- Test: `ingestion/test_schemas.py` (create if absent) or inline in `test_routes_settings.py`

**Interfaces — Consumes:** nothing. **Produces:** `TypeDef{name, description, pinned: bool=False, banned: bool=False}`; `ConfigRequest{interests, discover_types: bool, entity_types: list[TypeDef], relationship_types: list[TypeDef]}`; same fields on `ConfigResponse` plus `knowledge_base_id`.

- [ ] **Step 1: Write failing test.** In a new `ingestion/test_schemas.py`:

```python
from schemas import ConfigRequest, TypeDef


def test_typedef_flags_default_false() -> None:
    t = TypeDef(name="Person", description="d")
    assert t.pinned is False and t.banned is False


def test_config_request_has_interests_and_discover() -> None:
    req = ConfigRequest(
        interests="what I care about",
        discover_types=True,
        entity_types=[TypeDef(name="Person", description="", pinned=True)],
        relationship_types=[],
    )
    assert req.interests == "what I care about"
    assert req.entity_types[0].pinned is True
```
Run `uv run pytest test_schemas.py -q` → FAIL (fields don't exist).

- [ ] **Step 2: Update schemas.** In `schemas.py`:

```python
class TypeDef(BaseModel):
    name: str
    description: str = ""
    pinned: bool = False
    banned: bool = False


class ConfigRequest(BaseModel):
    interests: str
    discover_types: bool = True
    entity_types: list[TypeDef]
    relationship_types: list[TypeDef]


class ConfigResponse(BaseModel):
    knowledge_base_id: str
    interests: str
    discover_types: bool
    entity_types: list[TypeDef]
    relationship_types: list[TypeDef]
```

- [ ] **Step 3: Run test** → PASS. Then `uv run ruff check . && uv run mypy .`.

- [ ] **Step 4: Commit** — `git commit -am "feat(schemas): TypeDef flags + interests + discover_types"`.

---

### Task 3: Extraction prompt — open vs guided

**Files:**
- Modify: `ingestion/knowledge.py` (`build_extraction_messages`, ~lines 65-97; `extract_knowledge` ~line 107)
- Test: `ingestion/test_knowledge.py` (`test_build_extraction_messages_*`)

**Interfaces — Consumes:** type dicts `{name, description, ...}`. **Produces:** `build_extraction_messages(entity_types, relationship_types, text, *, interests: str, discover: bool)`.

- [ ] **Step 1: Update the failing test.** Replace `test_build_extraction_messages_includes_entity_and_relationship_types` to call with the new kwargs and assert both modes:

```python
def test_build_extraction_messages_open_mode_includes_interests_and_invites_new_types() -> None:
    msgs = build_extraction_messages(
        [{"name": "Person", "description": "a named human"}],
        [{"name": "Affected by", "description": ""}],
        "Some article",
        interests="US civic politics",
        discover=True,
    )
    joined = " ".join(m["content"] for m in msgs)
    assert "US civic politics" in joined
    assert "Person" in joined and "a named human" in joined
    assert "new" in joined.lower()  # invites coining new types


def test_build_extraction_messages_guided_mode_forbids_new_types() -> None:
    msgs = build_extraction_messages(
        [{"name": "Person", "description": ""}], [], "x", interests="i", discover=False
    )
    joined = " ".join(m["content"] for m in msgs).lower()
    assert "do not invent" in joined
```
Run → FAIL.

- [ ] **Step 2: Implement branch.** Replace `build_extraction_messages`:

```python
def build_extraction_messages(
    entity_types: list[dict[str, str]],
    relationship_types: list[dict[str, str]],
    text: str,
    *,
    interests: str,
    discover: bool,
) -> list[dict[str, str]]:
    lens = f"The user cares about: {interests}\n\n" if interests.strip() else ""
    if discover:
        system = (
            f"{lens}"
            "Extract entities and relationships that match the user's interests.\n"
            "Vocabulary discovered so far — reuse these exact names when a fact fits one:\n"
            f"Entity types:\n{_render_types(entity_types)}\n"
            f"Relationship types:\n{_render_types(relationship_types)}\n\n"
            "When you find something genuinely new that matches the user's interests and no existing "
            "type fits, coin a concise new type name and use it. Do not force-fit and do not create "
            "types for incidental mentions.\n"
            "For each entity, write a thorough, self-contained description (a rich paragraph, not a label)."
        )
    else:
        system = (
            f"{lens}"
            f"Extract only entities of these types:\n{_render_types(entity_types)}\n\n"
            "For each entity, write a thorough, self-contained description (a rich paragraph, not a label).\n\n"
            f"Also extract relationships, using only these relationship types:\n{_render_types(relationship_types)}\n\n"
            "Use the exact type names given; do not invent new ones."
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]
```
(`_render_types` already exists.) Update `extract_knowledge`'s call site to pass `interests`/`discover` through — add those two kwargs to `extract_knowledge`'s signature and forward them.

- [ ] **Step 3: Run** `uv run pytest test_knowledge.py -q` → the two new tests PASS. Fix any other call sites the type-checker flags. `uv run mypy .`.

- [ ] **Step 4: Commit** — `git commit -am "feat(extract): open vs guided prompt with interests lens"`.

---

### Task 4: Type-consolidation engine

**Files:**
- Modify: `ingestion/knowledge.py` (add `TypeDecision`, `TypeConsolidation`, `consolidate_types`)
- Test: `ingestion/test_knowledge.py`

**Interfaces — Produces:** `consolidate_types(client, model, kind: str, candidates: list[str], vocab: list[dict], interests: str, llm_params: dict) -> dict[str, dict]` returning `{candidate_norm: {"decision": "existing"|"new"|"drop", "canonical"?: str, "name"?: str, "description"?: str}}`. Uses `_chat` + `_strict_schema` like the other structured calls.

- [ ] **Step 1: Add the output models** near the other pydantic models:

```python
class TypeDecision(BaseModel):
    candidate: str
    decision: str  # "existing" | "new" | "drop"
    canonical: str = ""   # set when decision == "existing"
    name: str = ""        # cleaned name when decision == "new"
    description: str = ""  # one-line description when decision == "new"


class TypeConsolidation(BaseModel):
    decisions: list[TypeDecision] = []
```

- [ ] **Step 2: Write failing test** (LLM mocked via a fake `_chat`):

```python
def test_consolidate_types_maps_synonym_and_mints_new(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = TypeConsolidation(decisions=[
        TypeDecision(candidate="backed", decision="existing", canonical="Sponsors"),
        TypeDecision(candidate="Endorses", decision="new", name="Endorses", description="publicly endorses"),
        TypeDecision(candidate="mentioned", decision="drop"),
    ]).model_dump_json()
    monkeypatch.setattr(knowledge_mod, "_chat", lambda *a, **k: payload)
    out = knowledge_mod.consolidate_types(
        client=None, model="m", kind="relationship",
        candidates=["backed", "Endorses", "mentioned"],
        vocab=[{"name": "Sponsors", "description": "introduces legislation", "pinned": True}],
        interests="civic", llm_params={},
    )
    assert out[knowledge_mod._norm_type("backed")]["canonical"] == "Sponsors"
    assert out[knowledge_mod._norm_type("Endorses")]["name"] == "Endorses"
    assert out[knowledge_mod._norm_type("mentioned")]["decision"] == "drop"
```
Run → FAIL.

- [ ] **Step 3: Implement.**

```python
def consolidate_types(
    client: OpenRouter,
    model: str,
    kind: str,
    candidates: list[str],
    vocab: list[dict[str, Any]],
    interests: str,
    llm_params: dict[str, Any],
) -> dict[str, dict[str, str]]:
    """Resolve novel candidate type names to existing/new/drop against the current vocabulary."""
    if not candidates:
        return {}
    vocab_lines = "\n".join(
        f"- {t['name']}{' (pinned/authoritative)' if t.get('pinned') else ''}: {t.get('description') or ''}"
        for t in vocab
    )
    system = (
        f"You maintain a controlled vocabulary of {kind} types for a knowledge graph.\n"
        f"The user cares about: {interests}\n\n"
        f"Existing {kind} types (reuse the exact name when a candidate means the same thing; "
        f"pinned types are authoritative and must not be renamed):\n{vocab_lines or '(none yet)'}\n\n"
        "For each candidate below decide: 'existing' (a synonym of an existing type — give its exact "
        "canonical name), 'new' (genuinely distinct AND aligned with the user's interests — give a clean "
        "name and a one-line description), or 'drop' (incidental or not aligned). Merge near-synonyms; "
        "keep genuinely distinct relations separate (e.g. Funds vs Sponsors)."
    )
    user = "Candidates:\n" + "\n".join(f"- {c}" for c in candidates)
    schema = {
        "type": "json_schema",
        "json_schema": {"name": "type_consolidation", "strict": True,
                        "schema": _strict_schema(TypeConsolidation.model_json_schema())},
    }
    out = _chat(client, model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                llm_params, schema)
    if out is None:
        raise ValueError("type consolidation returned no content")
    result = TypeConsolidation.model_validate_json(out)
    return {_norm_type(d.candidate): d.model_dump(exclude={"candidate"}) for d in result.decisions}
```

- [ ] **Step 4: Run** test → PASS. `uv run mypy .`.

- [ ] **Step 5: Commit** — `git commit -am "feat(extract): type-consolidation engine"`.

---

### Task 5: Wire consolidation into `merge_content`

**Files:**
- Modify: `ingestion/knowledge.py` (`MergeResult`, `merge_content`)
- Test: `ingestion/test_knowledge.py`

**Interfaces — Consumes:** `consolidate_types` (Task 4), `_norm_type`. **Produces:** `merge_content(..., *, interests: str = "", discover: bool = False) -> MergeResult`, where `MergeResult` gains `new_entity_types: list[dict]` and `new_relationship_types: list[dict]`. `entity_types`/`relationship_types` are now the full type dicts (may include `pinned`/`banned`).

- [ ] **Step 1: Extend `MergeResult`** with `new_entity_types: list[dict[str, str]] = []` and `new_relationship_types: list[dict[str, str]] = []` (dataclass/pydantic — match its current definition).

- [ ] **Step 2: Write failing test.** Add to `test_knowledge.py` (LLM mocked like `test_merge_content_*`): config vocab `[Person]`/`[Affected by]` with `discover=True`; mock `extract_knowledge` to return an entity typed `person` (fast-path) and a relationship typed `AFFECTED_BY` (fast-path) **plus** a relationship typed `endorsed` (novel); mock `consolidate_types` to return `{norm("endorsed"): {"decision":"new","name":"Endorses","description":"backs publicly"}}`. Assert: relationships_created == 2; the stored edge types are `{"Affected by", "Endorses"}`; `result.new_relationship_types == [{"name":"Endorses","description":"backs publicly"}]`.

- [ ] **Step 3: Implement the merge loop.** Replace the type-handling in `merge_content`:

```python
    active_entities = [t for t in entity_types if not t.get("banned")]
    active_rels = [t for t in relationship_types if not t.get("banned")]
    # canon holds ACTIVE (non-banned) types only; banned is a disjoint drop-set. Order in resolve()
    # is safe because the two sets never overlap.
    entity_canon = {_norm_type(t["name"]): t["name"] for t in active_entities}
    rel_canon = {_norm_type(t["name"]): t["name"] for t in active_rels}
    banned_ent = {_norm_type(t["name"]) for t in entity_types if t.get("banned")}
    banned_rel = {_norm_type(t["name"]) for t in relationship_types if t.get("banned")}
    new_entity_types: list[dict[str, str]] = []
    new_relationship_types: list[dict[str, str]] = []
```
Add a helper inside `merge_content` (closure) that, given the extraction's distinct types for a kind, runs consolidation when `discover` and there are unmatched non-banned candidates, and returns a resolver `str -> str | None` (canonical name or None to drop), appending minted types to the `new_*` list:

```python
    def resolve_kind(kind, extracted_types, canon, banned, new_out, vocab):
        unmatched = sorted({t for t in extracted_types
                            if _norm_type(t) not in canon and _norm_type(t) not in banned})
        decisions: dict[str, dict[str, str]] = {}
        if discover and unmatched:
            try:
                decisions = consolidate_types(client, config.LLM_MODEL, kind, unmatched, vocab, interests, llm_params)
            except Exception:
                decisions = {}  # fast-path fallback: known types kept, novel deferred
        def resolve(t):
            key = _norm_type(t)
            if key in canon:
                return canon[key]
            if key in banned:
                return None
            d = decisions.get(key)
            if not d or d["decision"] == "drop":
                return None
            if d["decision"] == "existing" and _norm_type(d["canonical"]) in canon:
                return canon[_norm_type(d["canonical"])]
            if d["decision"] == "new" and _norm_type(d["name"]) not in banned:
                name = d["name"]
                if _norm_type(name) not in canon:
                    canon[_norm_type(name)] = name
                    new_out.append({"name": name, "description": d.get("description", "")})
                return name
            return None
        return resolve
```
Then use `resolve_entities = resolve_kind("entity", {e.type for e in extraction.entities}, entity_canon, banned_ent, new_entity_types, active_entities)` and canonicalize each kept entity via `resolve_entities(e.type)` (skip on None). Do the same for relationships with `rel_canon`/`banned_rel`/`new_relationship_types`/`active_rels`. Store the canonical name (as today). Return `MergeResult(..., new_entity_types=new_entity_types, new_relationship_types=new_relationship_types)`.

- [ ] **Step 4: Run** `uv run pytest test_knowledge.py -q` → all PASS (existing type-casing test still green: with `discover=False` the resolver only uses `canon`/`banned`, dropping unknowns exactly as before). `uv run mypy .`.

- [ ] **Step 5: Commit** — `git commit -am "feat(extract): consolidation + vocab growth in merge_content"`.

---

### Task 6: Worker + relevance use `interests`; persist grown vocab

**Files:**
- Modify: `ingestion/relevance.py` (rename param `relevance_prompt` → `interests`)
- Modify: `ingestion/worker.py` (`process_job`)
- Test: `ingestion/test_worker.py`

**Interfaces — Consumes:** `merge_content(..., interests=, discover=)` returning `new_*_types`. **Produces:** worker appends new types to `KnowledgeBaseConfig`.

- [ ] **Step 1: relevance.py rename.** Rename `relevance_prompt` param to `interests` in `judge_relevance` and `build_relevance_messages` (body unchanged). Update `test_relevance.py` call sites.

- [ ] **Step 2: Update `process_job` read + call.** Read `interests`, `discover_types`, and the full type dicts:

```python
        interests = cfg.interests if cfg else ""
        discover = bool(cfg.discover_types) if cfg else False
        entity_types = list(cfg.entity_types) if cfg else []
        relationship_types = list(cfg.relationship_types) if cfg else []
    ...
        verdict = judge_relevance(interests, content)
        ...
        result = merge_content(knowledge_base_id, content, entity_types, relationship_types, job_id,
                               interests=interests, discover=discover)
        _persist_new_types(knowledge_base_id, result.new_entity_types, result.new_relationship_types)
        _finalize(job_id, JobStatus.DONE, relevance_reason=verdict.reason)
```

- [ ] **Step 3: Add `_persist_new_types`** (dedup by `_norm_type`, skip banned, its own transaction):

```python
def _persist_new_types(knowledge_base_id: str, new_entities: list[dict], new_rels: list[dict]) -> None:
    if not new_entities and not new_rels:
        return
    from knowledge import _norm_type
    with get_postgres_session() as session:
        cfg = (session.query(KnowledgeBaseConfig)
               .filter(KnowledgeBaseConfig.knowledge_base_id == knowledge_base_id).one_or_none())
        if cfg is None:
            return
        def merged(existing, additions):
            seen = {_norm_type(t["name"]) for t in existing}
            out = list(existing)
            for t in additions:
                if _norm_type(t["name"]) not in seen:
                    seen.add(_norm_type(t["name"]))
                    out.append({"name": t["name"], "description": t.get("description", "")})
            return out
        cfg.entity_types = merged(cfg.entity_types, new_entities)
        cfg.relationship_types = merged(cfg.relationship_types, new_rels)
        session.commit()
```
(Reassigning the JSONB list triggers the UPDATE. Re-reading inside the txn keeps concurrent user edits safe.)

- [ ] **Step 4: Test.** In `test_worker.py`, extend the fixture config to `interests="anything"`, `discover_types=True`, seed `entity_types=[{"name":"Person","description":""}]`. Mock the LLM chain (as `test_knowledge` does) so extraction yields a novel relationship type and `consolidate_types` mints it; process a job; assert the config's `relationship_types` now contains the new type. Run `uv run pytest test_worker.py -q` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(worker): interests + discover + persist grown vocab"`.

---

### Task 7: Config routes — `interests`, `discover_types`, flags

**Files:**
- Modify: `ingestion/routes_settings.py` (cookie GET/PUT), `ingestion/routes_config.py` (Bearer PUT)
- Test: `ingestion/test_routes_settings.py`, `ingestion/test_routes_config.py`

**Interfaces — Consumes:** schemas (Task 2). Both routes read/write `interests`, `discover_types`, and per-type `pinned`/`banned`.

- [ ] **Step 1: Update failing tests.** In `test_routes_settings.py`, change bodies/asserts from `relevance_prompt` → `interests`, include `discover_types`, and add a case that PUTs a type with `pinned=True`/`banned=True` and GETs it back unchanged. In `test_routes_config.py` likewise for the Bearer route. Run → FAIL.

- [ ] **Step 2: Update `routes_settings.py`.** `_clean_types` preserves flags: `{"name": name, "description": sanitize(t.description).strip(), "pinned": t.pinned, "banned": t.banned}`. `get_config` returns `interests=cfg.interests`, `discover_types=cfg.discover_types`, and `[TypeDef.model_validate(t) for t in cfg.entity_types]` (validation fills flag defaults for legacy rows). `put_config` writes `cfg.interests`, `cfg.discover_types`, and the cleaned type dicts. Mirror the field changes in `routes_config.py`.

- [ ] **Step 3: Run** both test files → PASS. `uv run ruff check . && uv run mypy .`.

- [ ] **Step 4: Commit** — `git commit -am "feat(routes): interests + discover_types + type flags"`.

---

### Task 8: Seed defaults

**Files:**
- Modify: `ingestion/seed.py`
- Test: none (covered by existing seed smoke; verify manually)

- [ ] **Step 1:** In `seed.py` change the config defaults key `relevance_prompt` → `interests`, add `"discover_types": True`, and keep the typed entity/relationship seed lists (they already are `{name, description}` dicts; flags default via the model). Run `uv run python seed.py` against the dev DB (idempotent get_or_create) and confirm no error.

- [ ] **Step 2: Commit** — `git commit -am "feat(seed): interests + discover_types defaults"`.

---

### Task 9: Frontend — interests field, discover toggle, pin/ban controls

**Files:**
- Modify: `app/app/lib/types.ts`, `app/app/lib/api.ts`, `app/app/lib/auth.server.ts`, `app/app/routes/config.tsx`, `app/app/components/type-list-editor.tsx`
- Test: `pnpm run typecheck && pnpm run build` (Node 22.23.1 via nvm)

**Interfaces — Consumes:** the config API shape from Task 7.

- [ ] **Step 1: types.ts.** `TypeDef` gains `pinned: boolean; banned: boolean;`. `KbConfig` becomes `{ interests: string; discover_types: boolean; entity_types: TypeDef[]; relationship_types: TypeDef[] }`.

- [ ] **Step 2: auth.server.ts / api.ts.** `EMPTY_CONFIG` → `{ interests: "", discover_types: true, entity_types: [], relationship_types: [] }`. `updateConfig` payload passes the new fields through (no code change beyond the type).

- [ ] **Step 3: config.tsx.** Relabel the relevance textarea to **"What I care about"** bound to `interests` (help text: "Decides what gets in and what's worth extracting."). Add a **discover toggle** (a checkbox/switch bound to `discover_types`, label "Automatically discover new types"). Pass `interests` + `discover_types` in the `updateConfig` call. State init from `config.interests` / `config.discover_types`.

- [ ] **Step 4: type-list-editor.tsx.** Each row gains two small toggle buttons — **Pin** (sets `pinned`) and **Ban** (sets `banned`) — updating that row's `TypeDef` via the existing `update(index, patch)`. Show a subtle style when pinned (e.g. accent border) or banned (muted/strikethrough). Keep name + description inputs.

- [ ] **Step 5: Verify.** From `app/`: `corepack pnpm run typecheck` then `corepack pnpm run build` → both green.

- [ ] **Step 6: Commit** — `git commit -am "feat(web): interests field, discover toggle, pin/ban controls"`.

---

## Final verification (after all tasks)

- [ ] Full backend suite: from `ingestion/`, `uv run pytest -q` → all pass. `uv run ruff check . && uv run ruff format --check . && uv run mypy .`.
- [ ] Frontend: from `app/`, `pnpm run typecheck && pnpm run build`.
- [ ] Deploy via the pipeline (push to `main`); the migrate Job runs the rename/add-column on prod.
- [ ] Live smoke with a **throwaway account** (never the real admin KB — see `no-writes-on-real-accounts-when-testing` memory): set `interests` + `discover_types=on`, seed a couple of graph nodes, PUT config with a pinned + a banned type, GET back; confirm the config UI renders interests + toggle + pin/ban. Then clean up the throwaway account.
