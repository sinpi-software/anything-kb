# Wiki-Grade Articles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade entity content to a structured, living markdown article (lead + `## sections`) plus a short abstract, synthesized incrementally; enrich `Source` nodes with a label+date; expose `article` + page-level `references` over GraphQL. A backfill upgrades existing data.

**Architecture:** `merge_summary` (already an incremental "encyclopedia article" in the node's `summary` string) becomes `synthesize_article`, returning structured `{abstract, article}` from one LLM call. The node stores `article` (full markdown) and `summary` (short abstract). `Source` nodes gain `label`+`date`. GraphQL exposes `article` + `references`.

**Tech Stack:** neo4j driver, Strawberry GraphQL, OpenRouter, FastAPI, Postgres.

**Design doc:** `docs/superpowers/specs/2026-07-24-wiki-articles-design.md`.

## Global Constraints

- **Cost parity:** synthesis runs **only when merging** an existing entity (one LLM call, as today). A **first mention does NO synthesis call** — `article = entity.description`, abstract via a non-LLM heuristic.
- **Tenancy:** every Neo4j read/write stays `knowledge_base_id`-scoped.
- **No ingest-API change:** the source label is the existing `metadata.source` string; the date is the job's `created_at`.
- **References are page-level:** the markdown article body must contain NO References/Sources section and NO inline citation markers — references come from `Source` nodes.
- **`summary` = short abstract** (≤ ~2 sentences); the full body lives in `article`.
- **Synthesis failure never loses content:** on empty/failed LLM output, keep the existing article and derive the abstract from it.
- Dev test env (backend commands assume these, run from `ingestion/`): `INGESTION_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/ingestion`, `INGESTION_NEO4J_URI=bolt://localhost:7687`, `INGESTION_NEO4J_USER=neo4j`, `INGESTION_NEO4J_PASSWORD=ingestion`, `INGESTION_OPENROUTER_API_KEY=test-key-not-used`.
- After each task: `uv run ruff check . && uv run ruff format --check . && uv run mypy .` clean.

---

### Task 1: `synthesize_article` + `_derive_abstract`

**Files:** Modify `ingestion/knowledge.py` (ADD the new functions; **leave `merge_summary` in place** — Task 2 removes it when it switches `merge_content` over, so this task keeps the file clean); Test `ingestion/test_knowledge.py`.

**Interfaces — Produces:** `ArticleResult(BaseModel){abstract: str, article: str}`; `synthesize_article(client, model, existing_article: str, new_info: str, llm_params) -> ArticleResult`; `_derive_abstract(text: str) -> str`.

- [ ] **Step 1: Write failing tests.** In `test_knowledge.py`:
```python
def test_derive_abstract_takes_first_sentence_truncated() -> None:
    from knowledge import _derive_abstract
    assert _derive_abstract("Ada was a mathematician. She wrote the first algorithm.") == "Ada was a mathematician."
    assert len(_derive_abstract("x " * 400)) <= 240

def test_synthesize_article_returns_structured_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from knowledge import ArticleResult, synthesize_article
    payload = ArticleResult(abstract="Ada, a mathematician.", article="Ada Lovelace…\n\n## Work\n…").model_dump_json()
    monkeypatch.setattr(knowledge_mod, "_chat", lambda *a, **k: payload)
    out = synthesize_article(client=None, model="m", existing_article="old", new_info="new", llm_params={})  # type: ignore[arg-type]
    assert out.abstract == "Ada, a mathematician." and out.article.startswith("Ada Lovelace")

def test_synthesize_article_falls_back_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from knowledge import synthesize_article
    monkeypatch.setattr(knowledge_mod, "_chat", lambda *a, **k: None)
    out = synthesize_article(client=None, model="m", existing_article="Existing body. More.", new_info="new", llm_params={})  # type: ignore[arg-type]
    assert out.article == "Existing body. More." and out.abstract == "Existing body."
```
Run `uv run pytest test_knowledge.py -q` → FAIL.

- [ ] **Step 2: Implement.** Add near the other pydantic models (do NOT delete `merge_summary` — it stays until Task 2 rewires `merge_content`):
```python
class ArticleResult(BaseModel):
    abstract: str
    article: str


def _derive_abstract(text: str) -> str:
    """A cheap (no-LLM) short abstract: the first sentence, capped at 240 chars."""
    text = text.strip()
    head = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0] if text else ""
    return head[:240]


def synthesize_article(
    client: OpenRouter, model: str, existing_article: str, new_info: str, llm_params: dict[str, Any]
) -> ArticleResult:
    messages = [
        {
            "role": "system",
            "content": (
                "You maintain an encyclopedia article about one entity as a living document. Integrate "
                "the new information into the existing article: a lead paragraph, then `## Section` "
                "headings as the material warrants. Keep all existing facts, add the new ones, and note "
                "contradictions. Do NOT add a References or Sources section and do NOT add inline "
                "citations — sources are tracked separately. Also produce a one-to-two-sentence abstract."
            ),
        },
        {"role": "user", "content": f"Existing article:\n{existing_article}\n\nNew source:\n{new_info}"},
    ]
    schema = {
        "type": "json_schema",
        "json_schema": {"name": "article", "strict": True, "schema": _strict_schema(ArticleResult.model_json_schema())},
    }
    out = _chat(client, model, messages, llm_params, schema)
    if out is None:
        return ArticleResult(abstract=_derive_abstract(existing_article), article=existing_article)
    return ArticleResult.model_validate_json(out)
```
(`re` and `_strict_schema` already imported/defined.)

- [ ] **Step 3: Run** tests → PASS. `uv run mypy .` (whole repo) stays clean — `merge_summary` is untouched, so `merge_content` still works; `synthesize_article` is added but not yet called (an unused module function is fine).

- [ ] **Step 4: Commit** — `git commit -am "feat(knowledge): structured article synthesis + abstract"`.

---

### Task 2: Store `article`; `merge_content` uses `synthesize_article`

**Files:** Modify `ingestion/knowledge.py` (`upsert_entity`, `merge_content` entity loop); Test `ingestion/test_knowledge.py`.

**Interfaces — Consumes:** Task 1. **Produces:** `upsert_entity(session, knowledge_base_id, entity_id, entity, summary: str, article: str)`; Entity nodes carry `e.article`.

- [ ] **Step 1: Update `upsert_entity`** to take `article` and write it:
```python
def upsert_entity(
    session: Session, knowledge_base_id: str, entity_id: str, entity: ExtractedEntity, summary: str, article: str
) -> None:
    session.run(
        "MERGE (e:Entity {id: $id}) "
        "ON CREATE SET e.knowledge_base_id = $knowledge_base_id, e.type = $type, e.created_at = datetime() "
        "SET e.name = $name, e.name_normalized = $nn, e.summary = $summary, e.article = $article, "
        "e.aliases = $aliases, e.updated_at = datetime()",
        {"id": entity_id, "knowledge_base_id": knowledge_base_id, "type": entity.type, "name": entity.name,
         "nn": normalize_name(entity.name), "summary": summary, "article": article,
         "aliases": [normalize_name(a) for a in entity.aliases]},
    )
```

- [ ] **Step 2: Update the `merge_content` entity loop** (the `for entity, existing_id in zip(...)` block). New entity → no LLM; existing → synthesize from the existing **article**:
```python
        for entity, existing_id in zip(entities, resolved_ids, strict=True):
            if existing_id is None:
                entity_id = str(uuid.uuid4())
                article, summary = entity.description, _derive_abstract(entity.description)
                created += 1
            else:
                entity_id = existing_id
                row = neo.run(
                    "MATCH (e:Entity {id: $id, knowledge_base_id: $knowledge_base_id}) RETURN e.article AS a",
                    {"id": entity_id, "knowledge_base_id": knowledge_base_id},
                ).single()
                existing_article = (row["a"] if row and row["a"] else "")
                result = synthesize_article(client, config.LLM_MODEL, existing_article, entity.description, llm_params)
                article, summary = result.article, result.abstract
                merged += 1
            upsert_entity(neo, knowledge_base_id, entity_id, entity, summary, article)
            write_provenance(neo, knowledge_base_id, entity_id, job_id)
            name_to_id[normalize_name(entity.name)] = entity_id
```

- [ ] **Step 3: Update existing tests.** The provenance/casing merge tests call `upsert_entity`/assert `summary`. Adjust the direct `upsert_entity` test call (in `test_knowledge.py`) to pass both `summary=` and `article=`, and where a test asserts stored content, assert on `e.article`/`e.summary` as appropriate. The `test_merge_content_*` tests mock `extract_knowledge`/`resolve_entities_batch`; for the merge path, monkeypatch `knowledge_mod.synthesize_article` to return a known `ArticleResult` and assert the node's `article` + `summary` are stored. New-entity path: assert `article == entity.description` and `summary` is the derived abstract (no synthesis call — the mock must not be invoked).

- [ ] **Step 4: Remove `merge_summary`.** It now has no callers (this task rewired the only one). Delete the function; confirm nothing else references it (`grep -rn merge_summary ingestion/` shows only removed/updated spots).

- [ ] **Step 5: Run** `uv run pytest test_knowledge.py -q` → PASS; `uv run mypy .` clean.

- [ ] **Step 6: Commit** — `git commit -am "feat(knowledge): store article + abstract via synthesize_article"`.

---

### Task 3: Enrich `Source` nodes with label + date

**Files:** Modify `ingestion/knowledge.py` (`write_provenance`, `merge_content` signature), `ingestion/worker.py` (`process_job`); Test `ingestion/test_knowledge.py`, `ingestion/test_worker.py`.

**Interfaces — Produces:** `write_provenance(session, knowledge_base_id, entity_id, job_id, *, label: str = "", date: str = "")`; `merge_content(..., *, interests="", discover=False, source_label: str = "", source_date: str = "")`.

- [ ] **Step 1: Update `write_provenance`** to set label+date on the Source:
```python
def write_provenance(
    session: Session, knowledge_base_id: str, entity_id: str, job_id: str, *, label: str = "", date: str = ""
) -> None:
    session.run(
        "MERGE (s:Source {knowledge_base_id: $knowledge_base_id, job_id: $job_id}) "
        "SET s.label = $label, s.date = $date "
        "WITH s MATCH (e:Entity {id: $entity_id, knowledge_base_id: $knowledge_base_id}) "
        "MERGE (e)-[:MENTIONED_IN]->(s)",
        {"knowledge_base_id": knowledge_base_id, "job_id": job_id, "entity_id": entity_id, "label": label, "date": date},
    )
```

- [ ] **Step 2: Thread label/date through `merge_content`.** Add keyword-only `source_label: str = ""`, `source_date: str = ""` to `merge_content`'s signature; in the entity loop call `write_provenance(neo, knowledge_base_id, entity_id, job_id, label=source_label, date=source_date)`.

- [ ] **Step 3: Worker passes them.** In `process_job`, inside the first session block (while `job` is attached), read:
```python
        source_label = (job.job_metadata or {}).get("source", "") if job.job_metadata else ""
        source_date = job.created_at.isoformat() if job.created_at else ""
```
and pass `source_label=source_label, source_date=source_date` into the `merge_content(...)` call.

- [ ] **Step 4: Tests.** In `test_knowledge.py`, extend a merge_content test to pass `source_label="newsletter", source_date="2026-07-22"` and assert the `Source` node has `s.label == "newsletter"` and `s.date == "2026-07-22"` (query it after). Existing worker tests already monkeypatch `merge_content`, so they're unaffected; add/adjust a worker assertion only if a test inspects the call. Run `uv run pytest test_knowledge.py test_worker.py -q` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(knowledge): source label+date on provenance"`.

---

### Task 4: GraphQL — expose `article` + `references`

**Files:** Modify `ingestion/graph_read.py` (`_NODE_RETURN`, add `query_references`), `ingestion/graph_api.py` (`Node.article`, `Reference` type, `Node.references`); Test `ingestion/test_graph_api.py`.

**Interfaces — Produces:** `graph_read.query_references(knowledge_base_id, entity_id) -> list[dict]`; GraphQL `Node.article: str | None`, `Node.references: [Reference{label, date}]`.

- [ ] **Step 1: `graph_read`.** Extend the return and add the query:
```python
_NODE_RETURN = "RETURN e.id AS id, e.type AS type, e.name AS name, e.summary AS summary, e.article AS article"


def query_references(knowledge_base_id: str, entity_id: str) -> list[dict[str, Any]]:
    with get_neo4j_session() as session:
        return [
            dict(r)
            for r in session.run(
                "MATCH (e:Entity {id: $id, knowledge_base_id: $kb})-[:MENTIONED_IN]->(s:Source) "
                "RETURN s.label AS label, s.date AS date ORDER BY s.date",
                {"id": entity_id, "kb": knowledge_base_id},
            )
        ]
```

- [ ] **Step 2: `graph_api`.** Add `article` to `Node`, a `Reference` type, and a `references` resolver; make `_to_node` tolerant of rows lacking `article` (edge targets don't project it):
```python
@strawberry.type
class Reference:
    label: str
    date: str


# in class Node, add the field:
    article: str | None
    @strawberry.field
    def references(self) -> list[Reference]:
        rows = graph_read.query_references(self.knowledge_base_id, str(self.id))
        return [Reference(label=r.get("label") or "", date=r.get("date") or "") for r in rows]


# in _to_node:
    article=row.get("article"),
```
Add `article` to the `Node(...)` construction in `_to_node`.

- [ ] **Step 3: Test** (`test_graph_api.py`, `@requires_stack`): seed an entity with `article` set + a `Source{label,date}` linked via `MENTIONED_IN`; query `{ node(id:"…"){ article references { label date } } }`; assert `article` matches and `references` contains the label/date. Add a cross-KB guard: a `Source` under another knowledge base must not appear in this node's `references`. Run `uv run pytest test_graph_api.py -q` → PASS.

- [ ] **Step 4: Commit** — `git commit -am "feat(graphql): expose node article + references"`.

---

### Task 5: Backfill script

**Files:** Create `ingestion/backfill_articles.py`; Test `ingestion/test_backfill_articles.py`.

**Interfaces — Consumes:** `_derive_abstract` (Task 1).

- [ ] **Step 1: Write the script.** A `main()` that:
  - Entities missing `article`: `MATCH (e:Entity) WHERE e.article IS NULL AND e.summary IS NOT NULL` → for each, `SET e.article = e.summary` and `SET e.summary = <_derive_abstract(e.summary)>` (compute the abstract in Python, write both in one `SET`).
  - Sources missing `label`: `MATCH (s:Source) WHERE s.label IS NULL` → look up `ingest_jobs` in Postgres by `job_id` for `metadata->>'source'` and `created_at`; `SET s.label = $label, s.date = $date` (label `""` and date `""` when the job/label is absent).
  - Print counts. Load `.env` like `seed.py`/`worker.py` do.
  - Keep it a plain script (`if __name__ == "__main__": main()`), tenancy-agnostic is fine (it's an operator backfill over all rows).

- [ ] **Step 2: Test** (`@requires_stack`): create an Entity with only `summary` (no `article`) and a `Source` with a `job_id` matching a real `ingest_jobs` row that has `metadata={"source":"x"}`; run the backfill functions; assert the Entity now has `article == old summary` + a short `summary`, and the Source got `label=="x"`. Clean up in `finally`. Run `uv run pytest test_backfill_articles.py -q` → PASS.

- [ ] **Step 3: Commit** — `git commit -am "feat: article/source backfill script"`.

---

## Final verification (after all tasks)

- [ ] Backend: from `ingestion/`, `uv run pytest -q` all pass; `uv run ruff check . && uv run ruff format --check . && uv run mypy .` clean.
- [ ] Deploy via the pipeline (push to `main`) — no DB migration (article/label are Neo4j node properties added by Cypher SET).
- [ ] **Run the backfill on prod once** (Neo4j has no auto-migration): `kubectl -n ingestion exec deploy/ingestion-api -- python backfill_articles.py` — upgrades existing entities to `article` + short `summary` and labels existing sources.
- [ ] Live smoke with a **throwaway account** (never the real admin KB — see `no-writes-on-real-accounts-when-testing` memory): ingest a doc with `metadata.source` set, ingest a second doc mentioning the same entity, then GraphQL `{ node(id){ article references { label date } } }` — confirm a structured markdown `article` (lead + a `##` section after the merge) and a References list with the source label + date. Clean up the throwaway.
