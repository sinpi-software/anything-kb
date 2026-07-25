# Entity Salience Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop discover-mode extraction from minting non-durable "fragment" entity types (TimeWindow, LaneClosure, MilepostRange…) by refining the existing `consolidate_types` gate to judge wiki-page durability, with a concrete example instance per candidate.

**Architecture:** The fix lives entirely in the type-admission choke point that already exists — `consolidate_types` in `ingestion/knowledge.py` — plus its wiring in `merge_content` and two `config.py` constants. No new gate, no enumerated ban-list, no config field, no deterministic backstop, no fold behavior.

**Tech Stack:** Python 3.12, Pydantic, OpenRouter client, pytest, ruff, mypy (strict).

## Global Constraints

- Python 3.12; `mypy` strict must stay green; `ruff check` and `ruff format --check` must pass.
- No new third-party dependencies.
- Extraction model constant value is exactly `"openai/gpt-5-nano"`; the gate model constant value is exactly `"openai/gpt-5-mini"`.
- The salience criterion is a single general principle ("would each instance merit its own standalone wiki page"), NOT an enumerated list of banned categories.
- The wiki-page durability criterion applies to `kind == "entity"` only; `kind == "relationship"` keeps its existing distinctness criterion.
- Preserve all existing `consolidate_types` / `merge_content` behavior: `existing`/`new`/`drop` mapping, pinned/banned handling, casing normalization, and `drop → entity not written → dangling relationships skipped`.
- Tests that touch Neo4j/Postgres use the existing `@requires_neo4j_and_postgres` marker; pure-function tests need no marker.

---

### Task 1: Salience criterion + example instances in `consolidate_types`

**Files:**
- Modify: `ingestion/knowledge.py` (`consolidate_types`, lines 400–441)
- Test: `ingestion/test_knowledge.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: updated signature
  ```python
  def consolidate_types(
      client: OpenRouter,
      model: str,
      kind: str,                                 # "entity" | "relationship"
      candidates: list[str],
      vocab: list[dict[str, Any]],
      interests: str,
      llm_params: dict[str, Any],
      examples: dict[str, str] | None = None,    # NEW: raw-candidate-string -> one example instance
  ) -> dict[str, dict[str, str]]:
  ```
  Return shape unchanged: `{_norm_type(candidate): {"decision", "canonical"?, "name"?, "description"?}}`.

- [ ] **Step 1: Write the failing tests**

Add to `ingestion/test_knowledge.py`:

```python
def test_consolidate_types_entity_uses_wiki_criterion_and_renders_example(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_chat(client: Any, model: str, messages: list[dict[str, str]], llm_params: Any, schema: Any) -> str:
        captured["messages"] = messages
        return TypeConsolidation(
            decisions=[TypeDecision(candidate="TimeWindow", decision="drop")]
        ).model_dump_json()

    monkeypatch.setattr(knowledge_mod, "_chat", fake_chat)
    out = knowledge_mod.consolidate_types(
        client=None,  # type: ignore[arg-type]
        model="m",
        kind="entity",
        candidates=["TimeWindow"],
        vocab=[],
        interests="local news",
        llm_params={},
        examples={"TimeWindow": "7:00 a.m.-3:30 p.m."},
    )
    system = captured["messages"][0]["content"]
    user = captured["messages"][1]["content"]
    assert "wiki page" in system
    assert '7:00 a.m.-3:30 p.m.' in user  # example instance rendered next to the candidate
    assert out[knowledge_mod._norm_type("TimeWindow")]["decision"] == "drop"


def test_consolidate_types_relationship_keeps_distinctness_criterion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_chat(client: Any, model: str, messages: list[dict[str, str]], llm_params: Any, schema: Any) -> str:
        captured["messages"] = messages
        return TypeConsolidation(
            decisions=[TypeDecision(candidate="backed", decision="existing", canonical="Sponsors")]
        ).model_dump_json()

    monkeypatch.setattr(knowledge_mod, "_chat", fake_chat)
    knowledge_mod.consolidate_types(
        client=None,  # type: ignore[arg-type]
        model="m",
        kind="relationship",
        candidates=["backed"],
        vocab=[{"name": "Sponsors", "description": ""}],
        interests="civic",
        llm_params={},
    )
    system = captured["messages"][0]["content"]
    assert "Funds vs Sponsors" in system   # existing distinctness guidance retained
    assert "wiki page" not in system       # durability test is entity-only
```

The existing `test_consolidate_types_maps_synonym_and_mints_new` must remain unchanged and still pass (it omits `examples`, exercising the default).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest test_knowledge.py -q -k consolidate_types -v`
Expected: the two new tests FAIL (current prompt has no "wiki page" text and no example rendering); `test_consolidate_types_maps_synonym_and_mints_new` PASSES.

- [ ] **Step 3: Implement the criterion split + example rendering**

Replace the body of `consolidate_types` (from the `system = (...)` assignment through the `user = ...` line) with:

```python
    vocab_lines = "\n".join(
        f"- {t['name']}{' (pinned/authoritative)' if t.get('pinned') else ''}: {t.get('description') or ''}"
        for t in vocab
    )
    if kind == "entity":
        criterion = (
            "Choose 'new' ONLY if each instance of the type would merit its own standalone wiki "
            "page — a durable, individually-notable subject (a person, organization, place, work, "
            "law, lasting event). Choose 'drop' when instances are passing details, circumstances, "
            "measurements, time windows, or attributes of some other subject rather than subjects in "
            "their own right — even if topically relevant. Merge near-synonyms onto an existing type."
        )
    else:
        criterion = (
            "Choose 'new' when genuinely distinct AND aligned with the user's interests; 'drop' when "
            "incidental or not aligned. Merge near-synonyms; keep genuinely distinct relations separate "
            "(e.g. Funds vs Sponsors)."
        )
    system = (
        f"You maintain a controlled vocabulary of {kind} types for a knowledge graph.\n"
        f"The user cares about: {interests}\n\n"
        f"Existing {kind} types (reuse the exact name when a candidate means the same thing; "
        f"pinned types are authoritative and must not be renamed):\n{vocab_lines or '(none yet)'}\n\n"
        "For each candidate below decide: 'existing' (a synonym of an existing type — give its exact "
        "canonical name), 'new' (give a clean name and a one-line description), or 'drop'.\n"
        f"{criterion}"
    )
    ex = examples or {}
    user = "Candidates:\n" + "\n".join(
        f'- {c}' + (f' (e.g. "{ex[c]}")' if c in ex else "") for c in candidates
    )
```

Add the `examples: dict[str, str] | None = None` parameter to the signature (after `llm_params`). Leave the schema block, the `_chat` call, the `if out is None` guard, and the return statement exactly as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest test_knowledge.py -q -k consolidate_types -v`
Expected: all three tests PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff format knowledge.py test_knowledge.py
uv run ruff check knowledge.py test_knowledge.py
uv run mypy knowledge.py
git add ingestion/knowledge.py ingestion/test_knowledge.py
git commit -m "Add wiki-page durability criterion + example instances to consolidate_types"
```
Expected: ruff/mypy clean; commit succeeds.

---

### Task 2: Wire example maps + gate model through `merge_content` and `config`

**Files:**
- Modify: `ingestion/config.py` (LLM_MODEL value; add TYPE_GATE_MODEL)
- Modify: `ingestion/knowledge.py` (`merge_content` → `resolve_kind` and its two call sites, lines ~481–558)
- Test: `ingestion/test_knowledge.py`

**Interfaces:**
- Consumes: `consolidate_types(..., examples=...)` from Task 1.
- Produces:
  - `config.LLM_MODEL == "openai/gpt-5-nano"`, new `config.TYPE_GATE_MODEL == "openai/gpt-5-mini"`.
  - `resolve_kind` gains a trailing `examples: dict[str, str]` parameter and calls `consolidate_types` with `config.TYPE_GATE_MODEL` and `examples=examples`.

- [ ] **Step 1: Write the failing test**

Add to `ingestion/test_knowledge.py`:

```python
@requires_neo4j_and_postgres
def test_merge_content_passes_examples_and_gate_model_and_drops_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_schema()
    extraction = KnowledgeExtraction(
        entities=[
            ExtractedEntity(name="7:00 a.m.-3:30 p.m.", type="TimeWindow", description="a work window"),
            ExtractedEntity(name="Harry Morgan Bridge", type="Bridge", description="a bridge"),
        ],
        relationships=[],
    )
    monkeypatch.setattr(knowledge_mod, "extract_knowledge", lambda *a, **k: extraction)
    monkeypatch.setattr(knowledge_mod, "resolve_entities_batch", lambda *a, **k: [None] * len(a[4]))

    captured: dict[str, Any] = {}

    def fake_consolidate(
        client: Any, model: str, kind: str, candidates: Any, vocab: Any,
        interests: str, llm_params: Any, examples: Any = None,
    ) -> dict[str, dict[str, str]]:
        captured[kind] = {"model": model, "examples": examples}
        return {
            knowledge_mod._norm_type("TimeWindow"): {"decision": "drop"},
            knowledge_mod._norm_type("Bridge"): {"decision": "new", "name": "Bridge", "description": "a bridge"},
        }

    monkeypatch.setattr(knowledge_mod, "consolidate_types", fake_consolidate)

    class _NullClient:
        def __enter__(self) -> "_NullClient":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(knowledge_mod, "OpenRouter", lambda *a, **k: _NullClient())

    knowledge_base_id = f"merge-{uuid.uuid4()}"
    job_id = str(uuid.uuid4())
    try:
        result = merge_content(
            knowledge_base_id,
            "Bridge work 7:00 a.m.-3:30 p.m.",
            [{"name": "Person", "description": ""}],  # neither type is pre-known -> both go through the gate
            [],
            job_id,
            discover=True,
        )
        # Example instance for the fragment type was threaded into the gate, on the gate model.
        assert captured["entity"]["examples"]["TimeWindow"] == "7:00 a.m.-3:30 p.m."
        assert captured["entity"]["model"] == config.TYPE_GATE_MODEL
        # TimeWindow dropped, Bridge admitted -> exactly one entity written.
        assert result.entities_created == 1
        with get_neo4j_session() as neo:
            types = {
                r["t"]
                for r in neo.run(
                    "MATCH (e:Entity {knowledge_base_id: $o}) RETURN e.type AS t",
                    {"o": knowledge_base_id},
                )
            }
        assert types == {"Bridge"}
    finally:
        with get_neo4j_session() as neo:
            neo.run("MATCH (n) WHERE n.knowledge_base_id = $o DETACH DELETE n", {"o": knowledge_base_id})
```

Ensure `import config` (or `from ... import config`) is available in the test module — it is already imported as `config` via the existing test references to `config.LLM_MODEL`; if not present, add `import config  # noqa: E402` beside the other module imports.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest test_knowledge.py -q -k passes_examples_and_gate_model -v`
Expected: FAIL — `resolve_kind` does not yet pass `examples`, so `captured["entity"]["examples"]` is `None` (TypeError/AssertionError), and it still calls the gate with `config.LLM_MODEL`.

- [ ] **Step 3: Revert extraction model + add gate model**

In `ingestion/config.py`, replace the model line and its comment with:

```python
# Default model for relevance judging, extraction, resolution, and synthesis.
LLM_MODEL = "openai/gpt-5-nano"
# The salience/type-admission gate (consolidate_types) runs on a more capable model:
# it is low-volume (once per novel type) and is the sole guard against fragment types.
TYPE_GATE_MODEL = "openai/gpt-5-mini"
```

- [ ] **Step 4: Thread examples + gate model through `merge_content`**

In `merge_content`, change the `resolve_kind` definition to accept `examples` and use the gate model. Replace its signature and the `consolidate_types(...)` call:

```python
        def resolve_kind(
            kind: str,
            extracted_types: set[str],
            canon: dict[str, str],
            banned: set[str],
            new_out: list[dict[str, str]],
            vocab: list[dict[str, str]],
            examples: dict[str, str],
        ) -> Callable[[str], str | None]:
            unmatched = sorted(
                {t for t in extracted_types if _norm_type(t) not in canon and _norm_type(t) not in banned}
            )
            decisions: dict[str, dict[str, str]] = {}
            if discover and unmatched:
                try:
                    decisions = consolidate_types(
                        client, config.TYPE_GATE_MODEL, kind, unmatched, vocab, interests, llm_params, examples=examples
                    )
                except Exception:
                    decisions = {}  # fast-path fallback: known types kept, novel deferred
```

Leave the inner `resolve` closure unchanged.

Then update the two call sites. Replace the entity resolve call:

```python
        entity_examples: dict[str, str] = {}
        for e in extraction.entities:
            entity_examples.setdefault(e.type, e.name)
        resolve_entities = resolve_kind(
            "entity",
            {e.type for e in extraction.entities},
            entity_canon,
            banned_ent,
            new_entity_types,
            active_entities,
            entity_examples,
        )
```

Replace the relationship resolve call:

```python
        rel_examples: dict[str, str] = {}
        for r in extraction.relationships:
            rel_examples.setdefault(r.type, f"{r.source_name} -> {r.target_name}")
        resolve_rels = resolve_kind(
            "relationship",
            {r.type for r in extraction.relationships},
            rel_canon,
            banned_rel,
            new_relationship_types,
            active_rels,
            rel_examples,
        )
```

- [ ] **Step 5: Run the new test + full suite**

Run: `uv run pytest test_knowledge.py -q`
Expected: all pass, including the new test and the existing `merge_content` / `consolidate_types` tests.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff format config.py knowledge.py test_knowledge.py
uv run ruff check config.py knowledge.py test_knowledge.py
uv run mypy config.py knowledge.py
git add ingestion/config.py ingestion/knowledge.py ingestion/test_knowledge.py
git commit -m "Wire example instances + gate model through merge_content; revert extraction to nano"
```
Expected: clean; commit succeeds.

---

## Post-merge validation (controller, after both tasks land + deploy)

Not a task step — run after merge and deploy:

1. Push, watch `deploy.yml` succeed, confirm the running worker reports `config.LLM_MODEL == "openai/gpt-5-nano"` and `config.TYPE_GATE_MODEL == "openai/gpt-5-mini"`.
2. Clean-slate KLOG run: scale worker to 0 → skip pending/processing jobs → clear the KB graph → scale worker to 1 → run the KLOG ingest script → poll to drain.
3. Inspect the graph: the Harry Morgan Bridge story must no longer mint `TimeWindow`, `LaneClosure`, `MilepostRange`, `TrafficImpact`, `MaintenanceActivity`, or `MaintenanceCrew`; durable subjects (people, Deer Island wildfire, OPMA settlement, cities, venues) must survive.
4. If fragments still leak, the reserved next step is the deterministic pattern backstop — not another prompt edit.
