# Conservative Entity Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop entity resolution from fusing two unrelated entities by verifying every candidate merge with a conservative LLM check (no more no-LLM auto-merge), on a capable model.

**Architecture:** One cohesive change to `resolve_entities_batch` in `ingestion/knowledge.py` — remove the `len(cands) == 1` auto-merge so every candidate-having entity goes through the batched resolver, and make that resolver's prompt adversarial (default NEW). Add `config.RESOLUTION_MODEL` and pass it from `merge_content`.

**Tech Stack:** Python 3.12, Pydantic, OpenRouter client, pytest, ruff, mypy (strict).

## Global Constraints

- Python 3.12; `mypy` strict stays green; `ruff check` and `ruff format --check` pass.
- No new third-party dependencies. `ruff` line-length 120.
- `config.RESOLUTION_MODEL` value is exactly `"openai/gpt-5-mini"`.
- Preserve: the 0-candidate path (new, no LLM), tenancy scoping in `_gather_candidates`, and the `valid`-id guard that ignores returned ids not among a given entity's candidates (so `"NEW"` and hallucinated ids resolve to new).
- Safe failure: if the resolver returns empty/invalid, candidate-having entities stay `None` (new) — never a merge.

---

### Task 1: Conservative, always-verified entity resolution

**Files:**
- Modify: `ingestion/config.py` (add `RESOLUTION_MODEL`)
- Modify: `ingestion/knowledge.py` (`resolve_entities_batch` behavior + prompt + docstring; `merge_content` call site)
- Test: `ingestion/test_knowledge.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `config.RESOLUTION_MODEL == "openai/gpt-5-mini"`. `resolve_entities_batch` signature is unchanged; behavior changes to: 0 candidates → `None`; ≥1 candidate → batched conservative resolver; resolver empty/invalid → `None`.

- [x] **Step 1: Write/adjust the failing tests**

In `ingestion/test_knowledge.py`, **replace** `test_resolve_batch_single_candidate_skips_llm` (it asserts the auto-merge behavior being removed) with these two, and add the resolver-empty test. Leave the other `test_resolve_batch_*` tests unchanged.

```python
def test_resolve_batch_single_candidate_is_verified_and_can_merge() -> None:
    # A single candidate no longer auto-merges; the resolver is consulted and may confirm it.
    session = _FakeNeoSession([{"id": "a", "name": "Ada", "summary": "s1"}])
    client = _FakeResolutionClient('{"resolutions": [{"index": 0, "id": "a"}]}')
    assert _resolve_batch(session, client) == ["a"]
    assert client.chat.send_called


def test_resolve_batch_single_candidate_different_subject_stays_new() -> None:
    # The bug this fixes: a single (weak) candidate that is a DIFFERENT subject must NOT merge.
    # The resolver answers NEW, which is not among the candidate ids, so the entity becomes new.
    session = _FakeNeoSession([{"id": "a", "name": "Ada", "summary": "an unrelated thing"}])
    client = _FakeResolutionClient('{"resolutions": [{"index": 0, "id": "NEW"}]}')
    assert _resolve_batch(session, client) == [None]
    assert client.chat.send_called


def test_resolve_batch_resolver_empty_leaves_candidate_new() -> None:
    # If the resolver returns nothing, a candidate-having entity is created new — never wrongly merged.
    session = _FakeNeoSession([{"id": "a", "name": "Ada", "summary": "s1"}])
    client = _FakeResolutionClient("")
    assert _resolve_batch(session, client) == [None]
    assert client.chat.send_called
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest test_knowledge.py -q -k resolve_batch -v`
Expected: the two "single_candidate" tests FAIL — current code auto-merges the single candidate (returns `["a"]` without calling the LLM), so `send_called` is False and the different-subject case wrongly returns `["a"]`.

- [x] **Step 3: Add the config constant**

In `ingestion/config.py`, below `TYPE_GATE_MODEL`, add:

```python
# The precision-critical entity-resolution merge check runs on a more capable model: it is
# batched (once per item) and a wrong merge fuses two unrelated subjects into one node.
RESOLUTION_MODEL = "openai/gpt-5-mini"
```

- [x] **Step 4: Make resolution always-verified and conservative**

In `ingestion/knowledge.py`, `resolve_entities_batch`:

Replace the classification loop (the `for i, cands in enumerate(candidates):` block containing `if len(cands) == 1:`) with:

```python
    resolved: list[str | None] = []
    to_resolve: list[int] = []
    for i, cands in enumerate(candidates):
        resolved.append(None)  # default new; a candidate merge must be LLM-verified below
        if cands:
            to_resolve.append(i)
```

Then rename every remaining use of `ambiguous` to `to_resolve` (the `if ambiguous:` guard, the `blocks` comprehension, and the `valid` dict comprehension).

Replace the resolver system prompt content with the conservative one:

```python
                "content": (
                    "For each numbered entity below, decide whether it is UNMISTAKABLY the same "
                    "real-world entity as one of its listed candidates — the same specific person, "
                    "place, organization, work, or event, not merely the same type or a related "
                    "topic. Return that candidate's id ONLY when you are sure they are the same "
                    "thing. If they are different things that share a name or category, or you are "
                    "unsure, return NEW. Creating a new node is always safe; fusing two different "
                    "subjects is not."
                ),
```

Update the docstring to:

```python
    """For each entity, the id of the existing entity it refers to, or None if new. Entities with no
    candidate are new without an LLM call; every entity with a candidate is verified in a single
    conservative batched call — a merge is never made without that check."""
```

- [x] **Step 5: Pass the resolution model from `merge_content`**

In `ingestion/knowledge.py`, `merge_content`, change the resolution call from `config.LLM_MODEL` to `config.RESOLUTION_MODEL`:

```python
        resolved_ids = resolve_entities_batch(neo, client, config.RESOLUTION_MODEL, knowledge_base_id, entities, llm_params)
```

(Only the model argument changes; the other arguments stay.)

- [x] **Step 6: Run the resolution tests, then the full file**

Run: `uv run pytest test_knowledge.py -q -k resolve_batch -v`
Expected: all `test_resolve_batch_*` PASS, including the two new single-candidate tests and the empty-resolver test.

Run: `uv run pytest test_knowledge.py -q`
Expected: all pass (the `merge_content` integration tests monkeypatch `resolve_entities_batch`, so they are unaffected by the model-arg change).

- [x] **Step 7: Lint, type-check, commit**

```bash
uv run ruff format config.py knowledge.py test_knowledge.py
uv run ruff check config.py knowledge.py test_knowledge.py
uv run mypy config.py knowledge.py
git add ingestion/config.py ingestion/knowledge.py ingestion/test_knowledge.py
git commit -m "Verify every entity-resolution merge with a conservative resolver on mini"
```
Expected: ruff/mypy clean; commit succeeds.

---

## Post-merge validation (controller, after the task lands + deploy)

Not a task step — run after merge and deploy:

1. Push, watch `deploy.yml` succeed, confirm the worker reports `config.RESOLUTION_MODEL == "openai/gpt-5-mini"`.
2. Clean-slate KLOG re-ingest (worker→0, skip queue, clear graph, worker→1, ingest, drain).
3. Verify the "Attorney Fees and Litigation Costs" fusion does not recur: no single node mixes the Stageworks production with the OPMA settlement; its edges/sources belong to one subject.
4. Spot-check that legitimate recurring entities still merge across stories (e.g. `Longview`, `Cowlitz County` appear once with multiple sources) — the fix must not suppress correct merges.
